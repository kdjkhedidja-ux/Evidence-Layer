"""Read-only release planning and catalog checks for the existing Supabase project.

The allowed migration inventory is intentional: a new migration needs a reviewed
update to this manifest before it can be considered for production.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
PROJECT_REF = "ayrtyjgqlgtuiwuyigbz"
PROJECT_NAME = "Evidence-test"
EXPECTED_FILES = (
    "20261007170000_platform_baseline.sql",
    "20261016100000_saas_identity_workspace_boundary.sql",
    "20261017100000_personal_workspace_bootstrap.sql",
    "20261018150000_workspace_scoped_evidence_records.sql",
)
EXPECTED_HASHES = {
    "20261007170000_platform_baseline.sql": "de057b2e76b1a0e14cbeb5e54e5f281b26a1a5ef71e7977eb4513fd301042af4",
    "20261016100000_saas_identity_workspace_boundary.sql": "1eae97b07caf88eaec5f13c980e26be29d547221725a829b251662f68bf5a27b",
    "20261017100000_personal_workspace_bootstrap.sql": "16fd91767300c19d22a9fd6138ef7bbdc00610dc667a82c78f478ff99cc407a6",
    "20261018150000_workspace_scoped_evidence_records.sql": "dd16b90d2cacf9d4d08478094431e3e01cef9f853de07d155aa2f1da4a87a200",
}
VERSION_RE = re.compile(r"^[0-9]{14}_[a-z0-9_]+\.sql$")
DESTRUCTIVE_RE = re.compile(
    r"(?im)^\s*(?:drop\s|truncate\s|delete\s+from\b|update\s|"
    r"alter\s+table\b[^;]*\bdrop\b)"
)


class GuardError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardError(message)


def local_inventory() -> list[dict[str, str]]:
    names = sorted(path.name for path in MIGRATIONS.iterdir() if path.is_file())
    require(tuple(names) == EXPECTED_FILES, "Migration inventory changed; review and update the guarded manifest")
    inventory = []
    for name in names:
        require(bool(VERSION_RE.fullmatch(name)), f"Invalid migration filename: {name}")
        content = (MIGRATIONS / name).read_text(encoding="utf-8")
        require(content.strip() != "", f"Empty migration: {name}")
        without_comments = re.sub(r"/\*.*?\*/|--[^\n]*", "", content, flags=re.S)
        require(not DESTRUCTIVE_RE.search(without_comments), f"Potentially destructive SQL requires a separate release review: {name}")
        digest = hashlib.sha256(content.encode()).hexdigest()
        require(digest == EXPECTED_HASHES[name], f"Reviewed migration contents changed: {name}")
        inventory.append({"version": name[:14], "file": name, "sha256": digest})
    require(len({entry["version"] for entry in inventory}) == len(inventory), "Duplicate migration version")
    return inventory


class Supabase:
    def __init__(self) -> None:
        token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
        require(bool(token), "SUPABASE_ACCESS_TOKEN is unavailable")
        self.token = token
        self.base = f"https://api.supabase.com/v1/projects/{PROJECT_REF}"

    def request(self, path: str, body: dict | None = None):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise GuardError(f"Supabase {path} returned HTTP {error.code}") from None
        except urllib.error.URLError:
            raise GuardError(f"Supabase {path} could not be reached") from None

    def query(self, sql: str) -> list[dict]:
        rows = self.request("/database/query", {"query": sql, "read_only": True})
        require(isinstance(rows, list), "Unexpected read-only SQL response")
        return rows

    def target(self) -> None:
        configured = os.environ.get("SUPABASE_PROJECT_REF", "")
        require(configured == PROJECT_REF, "Configured project ref does not match the reviewed production target")
        project = self.request("")
        require(project.get("ref") == PROJECT_REF and project.get("name") == PROJECT_NAME, "Authenticated project metadata does not match the reviewed target")
        require(project.get("status") == "ACTIVE_HEALTHY", "Target project is not healthy")

    def history(self) -> list[str]:
        rows = self.query("select to_regclass('supabase_migrations.schema_migrations')::text as table_name")
        require(len(rows) == 1, "Could not determine remote migration history")
        if rows[0]["table_name"] is None:
            return []
        versions = self.query("select version::text from supabase_migrations.schema_migrations order by version")
        return [str(row["version"]) for row in versions]

    def backup(self) -> dict:
        data = self.request("/database/backups")
        completed = [item for item in (data.get("backups") or []) if item.get("status") == "COMPLETED"]
        dates = [item["inserted_at"] for item in completed if item.get("inserted_at")]
        return {"pitr_enabled": data.get("pitr_enabled") is True, "latest_completed_backup": max(dates) if dates else None,
                "physical_backup_data": data.get("physical_backup_data") or {}}

    def catalog(self) -> dict:
        queries = {
            "tables": "select c.relname as name,c.relrowsecurity as rls,c.relforcerowsecurity as force_rls from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind in ('r','p') order by c.relname",
            "indexes": "select indexname as name from pg_indexes where schemaname='public' order by indexname",
            "foreign_keys": "select co.conrelid::regclass::text as source,co.confrelid::regclass::text as target,co.confdeltype::text as on_delete from pg_constraint co where co.contype='f' and co.connamespace='public'::regnamespace order by 1,2",
            "policies": "select policyname as name from pg_policies where schemaname='public' order by policyname",
            "triggers": "select t.tgname as name from pg_trigger t join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and not t.tgisinternal order by t.tgname",
            "functions": "select n.nspname||'.'||p.proname as name from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('public','private') order by 1",
            "extensions": "select extname as name from pg_extension where extname='vector'",
        }
        return {name: self.query(sql) for name, sql in queries.items()}


def expected_catalog(versions: list[str]) -> dict:
    present = set(versions)
    identity = EXPECTED_FILES[1][:14] in present
    personal = EXPECTED_FILES[2][:14] in present
    evidence = EXPECTED_FILES[3][:14] in present
    tables = {"workspaces", "workspace_memberships"} if identity else set()
    if evidence:
        tables.add("evidence_records")
    indexes = {"workspaces_pkey", "workspace_memberships_workspace_user_key", "workspace_memberships_user_status_workspace_idx"} if identity else set()
    if personal:
        indexes.add("workspaces_personal_owner_unique")
    if evidence:
        indexes.update({"evidence_records_pkey", "evidence_records_workspace_created_idx"})
    policies = {"workspaces_select_active_members", "workspaces_update_active_owners", "workspaces_client_insert_denied", "workspaces_client_delete_denied",
                "workspace_memberships_select_own_active", "workspace_memberships_client_insert_denied", "workspace_memberships_client_update_denied", "workspace_memberships_client_delete_denied"} if identity else set()
    if evidence:
        policies.update({"evidence_records_select_active_members", "evidence_records_insert_active_members", "evidence_records_update_active_members", "evidence_records_delete_active_members"})
    foreign_keys = {("public.workspace_memberships", "public.workspaces", "c"), ("public.workspace_memberships", "auth.users", "c")} if identity else set()
    if personal:
        foreign_keys.add(("public.workspaces", "auth.users", "c"))
    if evidence:
        foreign_keys.add(("public.evidence_records", "public.workspaces", "c"))
    return {"tables": tables, "indexes": indexes, "policies": policies, "foreign_keys": foreign_keys,
            "triggers": {"evidence_records_protect_workspace_ownership"} if evidence else set(),
            "functions": ({"public.ensure_personal_workspace"} if personal else set()) | ({"private.protect_evidence_record"} if evidence else set()),
            "extensions": {"vector"} if EXPECTED_FILES[0][:14] in present else set()}


def assert_catalog(actual: dict, versions: list[str]) -> None:
    expected = expected_catalog(versions)
    for category in ("tables", "indexes", "policies", "triggers", "functions", "extensions"):
        found = {row["name"] for row in actual[category]}
        require(found == expected[category], f"Remote {category} differ from the applied migration manifest: expected {sorted(expected[category])}, found {sorted(found)}")
    found_fks = {(row["source"], row["target"], row["on_delete"]) for row in actual["foreign_keys"]}
    require(found_fks == expected["foreign_keys"], "Remote foreign keys differ from the applied migration manifest")
    for row in actual["tables"]:
        require(row["rls"] is True and row["force_rls"] is True, f"RLS is not enabled and forced on {row['name']}")


def assert_grants(api: Supabase, versions: list[str]) -> None:
    present = set(versions)
    if EXPECTED_FILES[1][:14] in present:
        rows = api.query("select "
            "has_column_privilege('authenticated','public.workspaces','id','SELECT') as workspace_id_select, "
            "has_column_privilege('authenticated','public.workspaces','name','UPDATE') as workspace_name_update, "
            "has_table_privilege('authenticated','public.workspace_memberships','SELECT') as membership_select, "
            "not has_table_privilege('anon','public.workspaces','SELECT') as anon_workspace_denied, "
            "not has_table_privilege('authenticated','public.workspace_memberships','INSERT') as membership_insert_denied")
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Workspace grants differ from the migration manifest")
    if EXPECTED_FILES[2][:14] in present:
        rows = api.query("select "
            "not has_column_privilege('authenticated','public.workspaces','personal_owner_id','SELECT') as owner_key_private, "
            "has_function_privilege('authenticated','public.ensure_personal_workspace()','EXECUTE') as authenticated_rpc, "
            "not has_function_privilege('anon','public.ensure_personal_workspace()','EXECUTE') as anon_rpc_denied")
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Personal workspace grants differ from the migration manifest")
    if EXPECTED_FILES[3][:14] in present:
        rows = api.query("select "
            "has_table_privilege('authenticated','public.evidence_records','SELECT') as evidence_select, "
            "has_column_privilege('authenticated','public.evidence_records','content','INSERT') as evidence_insert, "
            "has_column_privilege('authenticated','public.evidence_records','content','UPDATE') as evidence_update, "
            "has_table_privilege('authenticated','public.evidence_records','DELETE') as evidence_delete, "
            "not has_table_privilege('anon','public.evidence_records','SELECT') as anon_evidence_denied, "
            "not has_table_privilege('service_role','public.evidence_records','SELECT') as service_evidence_denied")
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Evidence grants differ from the migration manifest")


def recovery_is_recent(backup: dict) -> bool:
    latest = backup["latest_completed_backup"]
    if latest:
        try:
            timestamp = datetime.fromisoformat(latest.replace("Z", "+00:00"))
            if 0 <= (datetime.now(timezone.utc) - timestamp).total_seconds() <= 48 * 3600:
                return True
        except (ValueError, TypeError):
            pass
    if backup["pitr_enabled"]:
        timestamp = backup["physical_backup_data"].get("latest_physical_backup_date_unix")
        try:
            return 0 <= datetime.now(timezone.utc).timestamp() - int(timestamp) <= 48 * 3600
        except (ValueError, TypeError):
            pass
    return False


def build_plan(api: Supabase, inventory: list[dict[str, str]], require_recovery: bool) -> dict:
    api.target()
    local_versions = [item["version"] for item in inventory]
    remote_versions = api.history()
    require(remote_versions == local_versions[:len(remote_versions)], "Remote migration history is not a prefix of reviewed local migrations")
    assert_catalog(api.catalog(), remote_versions)
    assert_grants(api, remote_versions)
    backup = api.backup()
    if require_recovery:
        require(recovery_is_recent(backup), "No recent completed backup or PITR recovery point verified")
    commit = os.environ.get("GITHUB_SHA", "")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "GITHUB_SHA must identify the exact reviewed commit")
    plan = {"target_ref": PROJECT_REF, "target_name": PROJECT_NAME, "commit": commit,
            "files": inventory, "remote_applied": remote_versions,
            "pending": local_versions[len(remote_versions):], "recovery": backup}
    plan["digest"] = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("validate", "audit", "plan", "assert-plan", "verify"))
    parser.add_argument("--plan-file", type=Path)
    args = parser.parse_args()
    inventory = local_inventory()
    if args.mode == "validate":
        print(f"Validated {len(inventory)} reviewed migration files; no destructive statements detected")
        return 0
    api = Supabase()
    if args.mode in ("audit", "plan"):
        plan = build_plan(api, inventory, require_recovery=args.mode == "plan")
        output = json.dumps(plan, sort_keys=True, indent=2)
        if args.plan_file:
            args.plan_file.write_text(output + "\n", encoding="utf-8")
        print(output)
        return 0
    require(args.plan_file is not None and args.plan_file.is_file(), "Reviewed plan artifact is required")
    reviewed = json.loads(args.plan_file.read_text(encoding="utf-8"))
    require(reviewed.get("target_ref") == PROJECT_REF, "Reviewed plan targets another project")
    require(reviewed.get("commit") == os.environ.get("GITHUB_SHA"), "Reviewed plan belongs to another commit")
    require(reviewed.get("files") == inventory, "Reviewed plan files or hashes differ from the checked-out migration files")
    if args.mode == "assert-plan":
        current = build_plan(api, inventory, require_recovery=True)
        require(current == reviewed, "Remote state or migration files changed since the reviewed plan")
        print(f"Reviewed plan verified: {reviewed['digest']}; pending={len(reviewed['pending'])}")
        return 0
    api.target()
    current = api.history()
    versions = [item["version"] for item in inventory]
    require(current == versions, "Post-apply migration history has missing or unexpected versions")
    assert_catalog(api.catalog(), current)
    assert_grants(api, current)
    print(f"Post-apply history and catalog verified: {len(current)} migrations; pending=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GuardError as error:
        print(f"Migration guard blocked: {error}", file=sys.stderr)
        raise SystemExit(1)
