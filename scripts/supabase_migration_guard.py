"""Read-only release planning and catalog checks for the existing Supabase project.

Three controls are deliberately separate and must never be conflated:

* ``validate`` - the migration directory matches the reviewed manifest and every
  file hash is unchanged. This proves the *identity* of the reviewed bytes only.
* ``classify`` - a best-effort heuristic listing of statements worth a human
  look. It is not a parser and not an approval.
* ``plan`` / ``assert-plan`` / ``verify`` - read-only remote inspection and
  post-apply checks. ``plan`` never writes to the project.

None of these asserts that a migration is safe to deploy; that stays a human
release decision, recorded in the manifest provenance.
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

try:  # imported as a package from tests
    from scripts import sql_statement as sql
except ModuleNotFoundError:  # executed directly as a script
    import sql_statement as sql  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
MANIFEST = ROOT / "supabase" / "migrations.manifest.json"
LEDGER = ROOT / "supabase" / "migrations.statements.json"
PROJECT_REF = "ayrtyjgqlgtuiwuyigbz"
PROJECT_NAME = "Evidence-test"

# Version checks reference migrations by name, never by position in a tuple:
# appending a migration must not silently re-point an existing expectation.
V_PLATFORM = "20261007170000"
V_RLS_AUTO = "20261008000000"
V_IDENTITY = "20261016100000"
V_EVIDENCE = "20261018150000"
V_PERSONAL = "20261019135502"
V_RLS_RESTRICT = "20261019152707"
V_SOURCE_CATALOG = "20261022100000"
V_SNAPSHOT_STORAGE = "20261022110000"
V_VERIFIER_IDENTITY = "20261022120000"
V_FAIL_CLOSED = "20261022130000"
V_ADMIN_AUDIT = "20261023150000"

VERSION_RE = re.compile(r"^[0-9]{14}_[a-z0-9_]+\.sql$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class GuardError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardError(message)


def load_manifest() -> list[dict[str, str]]:
    try:
        label = MANIFEST.relative_to(ROOT)
    except ValueError:  # manifest patched to a path outside the repository
        label = MANIFEST
    require(MANIFEST.is_file(), f"Missing reviewed migration manifest: {label}")
    try:
        document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise GuardError(f"Reviewed migration manifest is not valid JSON: {error}") from error
    require(isinstance(document, dict), "Reviewed migration manifest must be a JSON object")
    entries = document.get("migrations")
    require(isinstance(entries, list) and bool(entries), "Reviewed migration manifest has no migrations list")
    seen: set[str] = set()
    for entry in entries:
        require(isinstance(entry, dict), f"Manifest entry must be an object: {entry!r}")
        for field in ("file", "version", "sha256", "provenance"):
            value = entry.get(field)
            require(isinstance(value, str) and value.strip() != "",
                    f"Manifest entry is missing {field}: {entry!r}")
        require(bool(VERSION_RE.fullmatch(entry["file"])), f"Invalid migration filename in manifest: {entry['file']}")
        require(entry["version"] == entry["file"][:14],
                f"Manifest version does not match its filename: {entry['file']}")
        require(bool(SHA256_RE.fullmatch(entry["sha256"])), f"Invalid sha256 in manifest: {entry['file']}")
        require(entry["version"] not in seen, f"Duplicate migration version in manifest: {entry['version']}")
        seen.add(entry["version"])
    return entries


def local_inventory() -> list[dict[str, str]]:
    entries = load_manifest()
    reviewed = {entry["file"] for entry in entries}
    present = {path.name for path in MIGRATIONS.iterdir() if path.is_file()}
    require(reviewed == present,
            "Migration inventory drifted from the reviewed manifest: "
            f"reviewed but absent {sorted(reviewed - present)}; present but unreviewed {sorted(present - reviewed)}")
    inventory = []
    for entry in sorted(entries, key=lambda item: item["file"]):
        name = entry["file"]
        content = (MIGRATIONS / name).read_text(encoding="utf-8")
        require(content.strip() != "", f"Empty migration: {name}")
        digest = hashlib.sha256(content.encode()).hexdigest()
        require(digest == entry["sha256"], f"Reviewed migration contents changed: {name}")
        inventory.append({"version": entry["version"], "file": name, "sha256": digest})
    require(len({entry["version"] for entry in inventory}) == len(inventory), "Duplicate migration version")
    return inventory


def analyse_migrations() -> tuple[list[dict], list[str]]:
    """Lex and classify every reviewed migration statement.

    Returns the per-statement records plus a list of blocking findings (lexing
    errors, ambiguous statements, hard-denied constructs).
    """
    records: list[dict] = []
    findings: list[str] = []
    for entry in local_inventory():
        text = (MIGRATIONS / entry["file"]).read_text(encoding="utf-8")
        try:
            parsed = sql.split_statements(text)
        except sql.SqlLexError as error:
            findings.append(f"{entry['file']}: {error}")
            continue
        for statement in parsed:
            try:
                tier, reason = sql.classify(statement)
            except sql.SqlLexError as error:
                findings.append(f"{entry['file']}:{statement.line}: {error}")
                continue
            digest = hashlib.sha256(
                f"{entry['file']}\n{statement.normalised}".encode("utf-8")
            ).hexdigest()[:16]
            records.append({
                "id": digest,
                "file": entry["file"],
                "line": statement.line,
                "verb": statement.verb,
                "tier": tier,
                "reason": reason,
                "text": statement.normalised,
            })
            if tier == sql.TIER_HARD_DENY:
                findings.append(
                    f"{entry['file']}:{statement.line}: hard-denied statement ({reason})"
                )
    return records, findings


def load_ledger() -> dict:
    try:
        label = LEDGER.relative_to(ROOT)
    except ValueError:
        label = LEDGER
    require(LEDGER.is_file(), f"Missing statement approval ledger: {label}")
    try:
        document = json.loads(LEDGER.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise GuardError(f"Statement approval ledger is not valid JSON: {error}") from error
    require(isinstance(document, dict), "Statement approval ledger must be a JSON object")
    rationales = document.get("rationales")
    require(isinstance(rationales, dict) and bool(rationales), "Ledger has no rationale library")
    entries = document.get("entries")
    require(isinstance(entries, list), "Ledger has no entries list")
    for ref, text in rationales.items():
        require(isinstance(text, str) and text.strip(), f"Empty rationale: {ref}")
    for entry in entries:
        require(isinstance(entry, dict), f"Ledger entry must be an object: {entry!r}")
        for field in ("id", "file", "verb", "rationale_ref"):
            value = entry.get(field)
            require(isinstance(value, str) and value.strip(),
                    f"Ledger entry is missing {field}: {entry!r}")
        require(entry["rationale_ref"] in rationales,
                f"Ledger entry references an unknown rationale: {entry['rationale_ref']}")
    return document


def assert_ledger(records: list[dict]) -> None:
    """Every registry-tier statement must be approved, in order, per file."""
    ledger = load_ledger()
    current: dict[str, list[str]] = {}
    for record in records:
        if record["tier"] == sql.TIER_REGISTRY:
            current.setdefault(record["file"], []).append(record["id"])
    approved: dict[str, list[str]] = {}
    for entry in ledger["entries"]:
        approved.setdefault(entry["file"], []).append(entry["id"])
    for name in sorted(set(approved) - set(current)):
        raise GuardError(
            f"Ledger records statements for {name} that are no longer registry-gated; "
            "remove or re-approve them"
        )
    for name in sorted(current):
        if approved.get(name, []) != current[name]:
            raise GuardError(
                f"Registry-gated statements changed in {name}: "
                f"{len(current[name])} present, {len(approved.get(name, []))} approved; "
                "re-run --emit-ledger and review the new statements"
            )


def emit_ledger(records: list[dict]) -> dict:
    """Build a ledger skeleton for the current registry-tier statements."""
    entries = []
    for record in records:
        if record["tier"] != sql.TIER_REGISTRY:
            continue
        entries.append({
            "id": record["id"],
            "file": record["file"],
            "line": record["line"],
            "verb": record["verb"],
            "rationale_ref": "",
        })
    return {"schema": 1, "rationales": {}, "entries": entries}


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
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise GuardError(f"Supabase {path} returned invalid JSON") from None
        except urllib.error.HTTPError as error:
            raise GuardError(f"Supabase {path} returned HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise GuardError(f"Supabase {path} could not be reached") from None

    def query(
        self,
        sql: str,
        expected_columns: dict[str, type | tuple[type, ...]],
    ) -> list[dict]:
        rows = self.request("/database/query/read-only", {"query": sql})
        require(isinstance(rows, list), "Unexpected read-only SQL response")
        require(bool(expected_columns), "Read-only SQL response schema is unavailable")
        expected_names = set(expected_columns)
        for index, row in enumerate(rows):
            require(isinstance(row, dict), f"Unexpected read-only SQL row at index {index}")
            actual_names = set(row)
            missing = expected_names - actual_names
            unexpected = actual_names - expected_names
            require(
                not missing and not unexpected,
                "Unexpected read-only SQL columns at row "
                f"{index}: missing={sorted(missing)}, unexpected={sorted(unexpected)}",
            )
            for name, expected_type in expected_columns.items():
                require(
                    type(row[name]) in (
                        expected_type if isinstance(expected_type, tuple) else (expected_type,)
                    ),
                    f"Unexpected read-only SQL value type for column {name} at row {index}",
                )
        return rows

    def target(self) -> None:
        configured = os.environ.get("SUPABASE_PROJECT_REF", "")
        require(configured == PROJECT_REF, "Configured project ref does not match the reviewed production target")
        project = self.request("")
        require(project.get("ref") == PROJECT_REF and project.get("name") == PROJECT_NAME, "Authenticated project metadata does not match the reviewed target")
        require(project.get("status") == "ACTIVE_HEALTHY", "Target project is not healthy")

    def history(self) -> list[str]:
        rows = self.query(
            "select pg_catalog.to_regclass('supabase_migrations.schema_migrations')::pg_catalog.text as table_name",
            {"table_name": (str, type(None))},
        )
        require(len(rows) == 1, "Could not determine remote migration history")
        require(
            rows[0]["table_name"] is not None,
            "Remote migration history table is missing",
        )
        versions = self.query(
            "select version::pg_catalog.text from supabase_migrations.schema_migrations order by version",
            {"version": str},
        )
        return [str(row["version"]) for row in versions]

    def backup(self) -> dict:
        data = self.request("/database/backups")
        completed = [item for item in (data.get("backups") or []) if item.get("status") == "COMPLETED"]
        dates = [item["inserted_at"] for item in completed if item.get("inserted_at")]
        return {"pitr_enabled": data.get("pitr_enabled") is True, "latest_completed_backup": max(dates) if dates else None,
                "physical_backup_data": data.get("physical_backup_data") or {}}

    def catalog(self) -> dict:
        # Schemas are explicit in every query so a new object can never be
        # silently adopted: anything outside public/private/storage is ignored,
        # and the expected inventory below must name it explicitly.
        queries = {
            "tables": (
                "select n.nspname as schema,c.relname as name,"
                "c.relrowsecurity as rls,c.relforcerowsecurity as force_rls "
                "from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace "
                "where n.nspname in ('public','private') and c.relkind in ('r','p') "
                "order by 1,2",
                {"schema": str, "name": str, "rls": bool, "force_rls": bool},
            ),
            "indexes": (
                "select schemaname as schema,indexname as name from pg_catalog.pg_indexes "
                "where schemaname in ('public','private') order by 1,2",
                {"schema": str, "name": str},
            ),
            "foreign_keys": (
                "select co.conname as name,"
                "sn.nspname||'.'||sc.relname as source,"
                "tn.nspname||'.'||tc.relname as target,"
                "co.confdeltype::pg_catalog.text as on_delete "
                "from pg_catalog.pg_constraint co "
                "join pg_catalog.pg_class sc on sc.oid=co.conrelid "
                "join pg_catalog.pg_namespace sn on sn.oid=sc.relnamespace "
                "join pg_catalog.pg_class tc on tc.oid=co.confrelid "
                "join pg_catalog.pg_namespace tn on tn.oid=tc.relnamespace "
                "where co.contype='f' and sn.nspname in ('public','private') "
                "order by 1",
                {"name": str, "source": str, "target": str, "on_delete": str},
            ),
            "policies": (
                "select schemaname as schema,tablename as table,policyname as name "
                "from pg_catalog.pg_policies "
                "where schemaname in ('public','private','storage') order by 1,2",
                {"schema": str, "table": str, "name": str},
            ),
            "triggers": (
                "select n.nspname as schema,t.tgname as name "
                "from pg_catalog.pg_trigger t "
                "join pg_catalog.pg_class c on c.oid=t.tgrelid "
                "join pg_catalog.pg_namespace n on n.oid=c.relnamespace "
                "where n.nspname in ('public','private') and not t.tgisinternal "
                "order by 1,2",
                {"schema": str, "name": str},
            ),
            # Extension-provided functions (e.g. the ~118 pgvector helpers the
            # platform installs into public) are not migration objects and must
            # not make the inventory diverge. pg_depend deptype 'e' marks them.
            "functions": (
                "select n.nspname as schema,p.proname as name "
                "from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace "
                "where n.nspname in ('public','private') "
                "and not exists (select 1 from pg_catalog.pg_depend d "
                "where d.classid='pg_catalog.pg_proc'::pg_catalog.regclass and d.objid=p.oid "
                "and d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass and d.deptype='e') "
                "order by 1,2",
                {"schema": str, "name": str},
            ),
            "extensions": (
                "select extname as name from pg_catalog.pg_extension where extname='vector'",
                {"name": str},
            ),
            "roles": (
                "select rolname as name,rolcanlogin as canlogin,rolinherit as inherit,"
                "rolsuper as super,rolbypassrls as bypassrls,rolcreaterole as createrole,"
                "rolcreatedb as createdb,rolreplication as replication "
                "from pg_catalog.pg_roles where rolname like 'snapshot\\_%' order by rolname",
                {
                    "name": str,
                    "canlogin": bool,
                    "inherit": bool,
                    "super": bool,
                    "bypassrls": bool,
                    "createrole": bool,
                    "createdb": bool,
                    "replication": bool,
                },
            ),
            "role_memberships": (
                "select role_row.rolname as name,"
                "count(membership.roleid)::pg_catalog.int8 as membership_count,"
                "count(membership.roleid) filter (where "
                "membership.roleid=role_row.oid "
                "and membership.member=current_user::pg_catalog.regrole "
                "and membership.admin_option "
                "and not membership.set_option "
                "and not membership.inherit_option"
                ")::pg_catalog.int8 as allowed_admin_memberships "
                "from pg_catalog.pg_roles as role_row "
                "left join pg_catalog.pg_auth_members as membership "
                "on membership.roleid=role_row.oid or membership.member=role_row.oid "
                "where role_row.rolname like 'snapshot\\_%' "
                "group by role_row.oid,role_row.rolname order by role_row.rolname",
                {
                    "name": str,
                    "membership_count": int,
                    "allowed_admin_memberships": int,
                },
            ),
            "event_triggers": (
                "select evtname as name from pg_catalog.pg_event_trigger order by evtname",
                {"name": str},
            ),
        }
        catalog_probe = self.query(
            "select count(*)::pg_catalog.int8 as visible_catalog_relations "
            "from pg_catalog.pg_class "
            "where oid in ("
            "'pg_catalog.pg_class'::pg_catalog.regclass,"
            "'pg_catalog.pg_namespace'::pg_catalog.regclass,"
            "'pg_catalog.pg_indexes'::pg_catalog.regclass,"
            "'pg_catalog.pg_constraint'::pg_catalog.regclass,"
            "'pg_catalog.pg_policies'::pg_catalog.regclass,"
            "'pg_catalog.pg_trigger'::pg_catalog.regclass,"
            "'pg_catalog.pg_proc'::pg_catalog.regclass,"
            "'pg_catalog.pg_depend'::pg_catalog.regclass,"
            "'pg_catalog.pg_extension'::pg_catalog.regclass,"
            "'pg_catalog.pg_roles'::pg_catalog.regclass,"
            "'pg_catalog.pg_auth_members'::pg_catalog.regclass,"
            "'pg_catalog.pg_event_trigger'::pg_catalog.regclass)",
            {"visible_catalog_relations": int},
        )
        require(
            len(catalog_probe) == 1 and catalog_probe[0]["visible_catalog_relations"] == 12,
            "Read-only SQL role cannot verify all required PostgreSQL catalog relations",
        )
        return {
            name: self.query(query, expected_columns)
            for name, (query, expected_columns) in queries.items()
        }


def expected_roles(versions: list[str]) -> set[str]:
    present = set(versions)
    roles: set[str] = set()
    if V_SOURCE_CATALOG in present:
        roles.add("snapshot_verifier")
    if V_VERIFIER_IDENTITY in present:
        roles.add("snapshot_publisher_owner")
    if V_ADMIN_AUDIT in present:
        roles.update({
            "snapshot_verifier_allowlist_owner",
            "snapshot_verifier_allowlist_manager",
            "snapshot_verifier_audit_owner",
        })
    return roles


def expected_policy_identity(policy: str) -> str:
    schema, name = policy.split(".", 1)
    relation_prefixes = (
        ("source_snapshots_objects_", "storage", "objects"),
        ("workspace_memberships_", "public", "workspace_memberships"),
        ("workspaces_", "public", "workspaces"),
        ("evidence_records_", "public", "evidence_records"),
        ("source_snapshots_", "public", "source_snapshots"),
        ("source_grants_", "public", "source_grants"),
        ("snapshot_verifier_allowlist_audit_", "private", "snapshot_verifier_allowlist_audit"),
        ("snapshot_verifier_allowlist_", "private", "snapshot_verifier_allowlist"),
    )
    for prefix, expected_schema, table in relation_prefixes:
        if name.startswith(prefix):
            require(schema == expected_schema, f"Policy {policy} has an unexpected schema")
            return f"{schema}.{table}.{name}"
    raise GuardError(f"Policy {policy} has no reviewed relation mapping")


def expected_catalog(versions: list[str]) -> dict:
    present = set(versions)
    rls_auto = V_RLS_AUTO in present
    identity = V_IDENTITY in present
    personal = V_PERSONAL in present
    evidence = V_EVIDENCE in present
    source = V_SOURCE_CATALOG in present
    storage = V_SNAPSHOT_STORAGE in present
    verifier = V_VERIFIER_IDENTITY in present
    fences = V_FAIL_CLOSED in present
    admin = V_ADMIN_AUDIT in present

    # schema.name -> (rls_required, force_rls_required). A table listed with
    # False/False (the read-policy baseline) must NOT have RLS enabled.
    tables: dict[str, tuple[bool, bool]] = {}
    if identity:
        tables["public.workspaces"] = (True, True)
        tables["public.workspace_memberships"] = (True, True)
    if evidence:
        tables["public.evidence_records"] = (True, True)
    if source:
        tables["public.source_snapshots"] = (True, True)
        tables["public.source_grants"] = (True, True)
    if verifier:
        tables["private.snapshot_verifier_allowlist"] = (True, True)
    if admin:
        tables["private.snapshot_verifier_read_policy_baseline"] = (False, False)
        tables["private.snapshot_verifier_allowlist_audit"] = (True, True)

    indexes: set[str] = set()
    if identity:
        indexes.update({
            "public.workspaces_pkey",
            "public.workspace_memberships_workspace_user_key",
            "public.workspace_memberships_user_status_workspace_idx",
        })
    if evidence:
        indexes.update({"public.evidence_records_pkey", "public.evidence_records_workspace_created_idx"})
    if personal:
        indexes.add("public.workspaces_personal_owner_unique")
    if source:
        indexes.update({
            "public.source_snapshots_pkey",
            "public.source_snapshots_object_key_key",
            "public.source_grants_pkey",
            "public.source_grants_principal_idx",
        })
    if verifier:
        indexes.add("private.snapshot_verifier_allowlist_pkey")
    if admin:
        indexes.update({
            "private.snapshot_verifier_read_policy_baseline_pkey",
            "private.snapshot_verifier_allowlist_audit_pkey",
        })

    policies: set[str] = set()
    if identity:
        policies.update({
            "public.workspaces_select_active_members",
            "public.workspaces_update_active_owners",
            "public.workspaces_client_insert_denied",
            "public.workspaces_client_delete_denied",
            "public.workspace_memberships_select_own_active",
            "public.workspace_memberships_client_insert_denied",
            "public.workspace_memberships_client_update_denied",
            "public.workspace_memberships_client_delete_denied",
        })
    if evidence:
        policies.update({
            "public.evidence_records_select_active_members",
            "public.evidence_records_insert_active_members",
            "public.evidence_records_update_active_members",
            "public.evidence_records_delete_active_members",
        })
    if source:
        policies.update({
            "public.source_snapshots_select_granted_published_or_own_staged",
            "public.source_snapshots_insert_active_owners",
            "public.source_grants_select_own_or_owner",
            "public.source_grants_insert_active_owners",
            "public.source_grants_delete_active_owners",
        })
    if storage:
        policies.update({
            "storage.source_snapshots_objects_insert_staged_by_owner",
            "storage.source_snapshots_objects_select_granted_published",
        })
    if verifier:
        policies.update({
            "public.source_snapshots_select_publisher_owner",
            "public.source_snapshots_update_publisher_owner",
            "storage.source_snapshots_objects_select_publisher_owner",
            "storage.source_snapshots_objects_select_allowlisted_verifier",
            "private.snapshot_verifier_allowlist_select_owner",
        })
    if fences:
        policies.update({
            "public.source_snapshots_select_verifier_role_gate_restrictive_fence",
            "public.source_snapshots_select_authenticated_restrictive_fence",
            "public.source_snapshots_select_publisher_owner_restrictive_fence",
            "storage.source_snapshots_objects_verifier_role_gate_restrictive_fence",
            "storage.source_snapshots_objects_select_authenticated_restrictive_fence",
            "storage.source_snapshots_objects_publisher_owner_restrictive_fence",
        })
    if admin:
        policies.update({
            "private.snapshot_verifier_allowlist_select_manager",
            "private.snapshot_verifier_allowlist_insert_manager",
            "private.snapshot_verifier_allowlist_delete_manager",
            "private.snapshot_verifier_allowlist_audit_insert_owner",
            "private.snapshot_verifier_allowlist_audit_select_manager",
        })

    triggers: set[str] = set()
    if evidence:
        triggers.add("public.evidence_records_protect_workspace_ownership")
    if source:
        triggers.update({
            "public.source_snapshots_protect_immutability",
            "public.source_snapshots_reject_delete",
        })
    if storage:
        triggers.add("public.source_snapshots_require_stored_object")
    if verifier:
        triggers.add("public.source_snapshots_require_verification_binding")
    if admin:
        triggers.add("private.snapshot_verifier_allowlist_audit_changes")

    functions: set[str] = set()
    if rls_auto:
        functions.add("public.rls_auto_enable")
    if personal:
        functions.add("public.ensure_personal_workspace")
    if evidence:
        functions.add("private.protect_evidence_record")
    if source:
        functions.update({
            "private.assert_snapshot_role_safe",
            "private.protect_source_snapshot",
            "private.reject_source_snapshot_delete",
        })
    if storage:
        functions.update({
            "private.ensure_snapshot_bucket",
            "private.require_stored_snapshot_object",
        })
    if verifier:
        functions.update({
            "private.verified_user_id",
            "private.require_verification_binding",
            "private.assert_snapshot_verifier_function_acl_safe",
            "public.verifier_may_read_staged_object",
            "public.begin_snapshot_verification",
            "public.publish_verified_snapshot",
        })
    if fences:
        functions.update({
            "private.assert_snapshot_verifier_allowlist_shape",
            "private.assert_snapshot_api_role_cannot_assume",
            "private.assert_snapshot_read_policy_inventory",
        })
    if admin:
        functions.update({
            "private.assert_snapshot_read_policy_inventory_v2",
            "private.assert_snapshot_verifier_allowlist_admin_shape",
            "private.audit_snapshot_verifier_allowlist_change",
        })

    foreign_keys: set[tuple[str, str, str, str]] = set()
    if identity:
        foreign_keys.update({
            ("workspace_memberships_workspace_id_fkey", "public.workspace_memberships", "public.workspaces", "c"),
            ("workspace_memberships_user_id_fkey", "public.workspace_memberships", "auth.users", "c"),
        })
    if personal:
        foreign_keys.add(("workspaces_personal_owner_id_fkey", "public.workspaces", "auth.users", "c"))
    if evidence:
        foreign_keys.add(("evidence_records_workspace_id_fkey", "public.evidence_records", "public.workspaces", "c"))
    if source:
        foreign_keys.update({
            ("source_snapshots_workspace_id_fkey", "public.source_snapshots", "public.workspaces", "r"),
            ("source_snapshots_created_by_fkey", "public.source_snapshots", "auth.users", "a"),
            ("source_grants_member_fkey", "public.source_grants", "public.workspace_memberships", "c"),
            ("source_grants_granted_by_fkey", "public.source_grants", "auth.users", "a"),
        })
    if verifier:
        foreign_keys.update({
            ("source_snapshots_verified_by_fkey", "public.source_snapshots", "auth.users", "a"),
            ("snapshot_verifier_allowlist_user_id_fkey", "private.snapshot_verifier_allowlist", "auth.users", "c"),
            ("snapshot_verifier_allowlist_workspace_id_fkey", "private.snapshot_verifier_allowlist", "public.workspaces", "c"),
        })

    return {
        "tables": tables,
        "indexes": indexes,
        "policies": policies,
        "triggers": triggers,
        "functions": functions,
        "foreign_keys": foreign_keys,
        "extensions": {"vector"} if V_PLATFORM in present else set(),
        "roles": expected_roles(versions),
        "event_triggers": {"ensure_rls"} if rls_auto else set(),
    }


def assert_catalog(actual: dict, versions: list[str]) -> None:
    expected = expected_catalog(versions)

    found_tables = {f"{row['schema']}.{row['name']}" for row in actual["tables"]}
    require(found_tables == set(expected["tables"]),
            f"Remote tables differ from the applied migration manifest: expected {sorted(expected['tables'])}, found {sorted(found_tables)}")
    for row in actual["tables"]:
        qualified = f"{row['schema']}.{row['name']}"
        rls_required, force_required = expected["tables"][qualified]
        require(row["rls"] is rls_required and row["force_rls"] is force_required,
                f"RLS state differs from the manifest on {qualified}: expected rls={rls_required} force={force_required}, found rls={row['rls']} force={row['force_rls']}")

    for category in ("indexes", "triggers", "functions"):
        found = {f"{row['schema']}.{row['name']}" for row in actual[category]}
        require(found == expected[category],
                f"Remote {category} differ from the applied migration manifest: expected {sorted(expected[category])}, found {sorted(found)}")

    # The catalog guard pins policy identity to its target relation. pgTAP
    # checks policy commands, roles, predicates, and RLS behavior.
    assert_policy_inventory(actual["policies"], expected["policies"])

    found_extensions = {row["name"] for row in actual["extensions"]}
    require(found_extensions == expected["extensions"],
            f"Remote extensions differ from the applied migration manifest: expected {sorted(expected['extensions'])}, found {sorted(found_extensions)}")
    # Event triggers live in one global namespace: Supabase pre-installs its own
    # (pgrst_*, issue_*) that are not migration objects, so exact equality would
    # fail on every healthy project. Require the migration-defined trigger(s) to
    # be present instead; new triggers still fail closed through the static
    # ledger (any 'create event trigger' is registry-gated).
    found_event_triggers = {row["name"] for row in actual["event_triggers"]}
    require(expected["event_triggers"] <= found_event_triggers,
            f"Remote event triggers are missing migration-defined triggers: expected {sorted(expected['event_triggers'])}, found {sorted(found_event_triggers)}")

    found_roles = {row["name"] for row in actual["roles"]}
    require(found_roles == expected["roles"],
            f"Remote snapshot roles differ from the applied migration manifest: expected {sorted(expected['roles'])}, found {sorted(found_roles)}")
    for row in actual["roles"]:
        for attribute in ("canlogin", "inherit", "super", "bypassrls", "createrole", "createdb", "replication"):
            require(row[attribute] is False, f"Snapshot role {row['name']} has an unsafe attribute: {attribute}")
    assert_role_memberships(actual["role_memberships"], expected["roles"])

    found_fks = {(row["name"], row["source"], row["target"], row["on_delete"]) for row in actual["foreign_keys"]}
    require(found_fks == expected["foreign_keys"],
            "Remote foreign keys differ from the applied migration manifest: "
            f"expected {sorted(expected['foreign_keys'])}, found {sorted(found_fks)}")


def assert_role_memberships(actual: list[dict], expected_roles: set[str]) -> None:
    found: dict[str, dict] = {}
    for index, row in enumerate(actual):
        require(isinstance(row, dict), f"Unexpected role membership row at index {index}")
        require(
            set(row) == {"name", "membership_count", "allowed_admin_memberships"},
            f"Unexpected role membership columns at index {index}",
        )
        require(
            isinstance(row["name"], str)
            and type(row["membership_count"]) is int
            and type(row["allowed_admin_memberships"]) is int,
            f"Unexpected role membership value type at index {index}",
        )
        require(row["name"] not in found, f"Duplicate role membership audit row for {row['name']}")
        found[row["name"]] = row

    require(
        set(found) == expected_roles,
        "Snapshot role membership audit is incomplete or contains unexpected roles",
    )
    for role_name, row in found.items():
        membership_count = row["membership_count"]
        allowed_count = row["allowed_admin_memberships"]
        require(
            membership_count >= 0 and allowed_count in (0, 1)
            and membership_count == allowed_count,
            f"Snapshot role {role_name} has an unexpected direct or transitive membership",
        )


def assert_policy_inventory(actual: list[dict], expected: set[str]) -> None:
    found: set[str] = set()
    for index, row in enumerate(actual):
        require(isinstance(row, dict), f"Unexpected policy row at index {index}")
        require(
            set(row) == {"schema", "table", "name"}
            and all(isinstance(row[field], str) for field in ("schema", "table", "name")),
            f"Unexpected policy catalog columns or value types at index {index}",
        )
        identity = f"{row['schema']}.{row['table']}.{row['name']}"
        require(identity not in found, f"Duplicate policy catalog row for {identity}")
        found.add(identity)

    expected_identities = {expected_policy_identity(policy) for policy in expected}
    require(
        found == expected_identities,
        "Remote policies differ from the migration manifest: "
        f"expected {sorted(expected_identities)}, found {sorted(found)}",
    )


def assert_grants(api: Supabase, versions: list[str]) -> None:
    present = set(versions)
    if V_IDENTITY in present:
        rows = api.query("select "
            "pg_catalog.has_column_privilege('authenticated','public.workspaces','id','SELECT') as workspace_id_select, "
            "pg_catalog.has_column_privilege('authenticated','public.workspaces','name','UPDATE') as workspace_name_update, "
            "pg_catalog.has_table_privilege('authenticated','public.workspace_memberships','SELECT') as membership_select, "
            "not pg_catalog.has_table_privilege('anon','public.workspaces','SELECT') as anon_workspace_denied, "
            "not pg_catalog.has_table_privilege('authenticated','public.workspace_memberships','INSERT') as membership_insert_denied",
            {
                "workspace_id_select": bool,
                "workspace_name_update": bool,
                "membership_select": bool,
                "anon_workspace_denied": bool,
                "membership_insert_denied": bool,
            })
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Workspace grants differ from the migration manifest")
    if V_PERSONAL in present:
        rows = api.query("select "
            "not pg_catalog.has_column_privilege('authenticated','public.workspaces','personal_owner_id','SELECT') as owner_key_private, "
            "pg_catalog.has_function_privilege('authenticated','public.ensure_personal_workspace()','EXECUTE') as authenticated_rpc, "
            "not pg_catalog.has_function_privilege('anon','public.ensure_personal_workspace()','EXECUTE') as anon_rpc_denied",
            {
                "owner_key_private": bool,
                "authenticated_rpc": bool,
                "anon_rpc_denied": bool,
            })
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Personal workspace grants differ from the migration manifest")
    if V_EVIDENCE in present:
        rows = api.query("select "
            "pg_catalog.has_table_privilege('authenticated','public.evidence_records','SELECT') as evidence_select, "
            "pg_catalog.has_column_privilege('authenticated','public.evidence_records','content','INSERT') as evidence_insert, "
            "pg_catalog.has_column_privilege('authenticated','public.evidence_records','content','UPDATE') as evidence_update, "
            "pg_catalog.has_table_privilege('authenticated','public.evidence_records','DELETE') as evidence_delete, "
            "not pg_catalog.has_table_privilege('anon','public.evidence_records','SELECT') as anon_evidence_denied, "
            "not pg_catalog.has_table_privilege('service_role','public.evidence_records','SELECT') as service_evidence_denied",
            {
                "evidence_select": bool,
                "evidence_insert": bool,
                "evidence_update": bool,
                "evidence_delete": bool,
                "anon_evidence_denied": bool,
                "service_evidence_denied": bool,
            })
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Evidence grants differ from the migration manifest")
    if V_SOURCE_CATALOG in present:
        rows = api.query("select "
            "pg_catalog.has_table_privilege('authenticated','public.source_snapshots','SELECT') as snapshots_select, "
            "pg_catalog.has_column_privilege('authenticated','public.source_snapshots','object_key','INSERT') as snapshots_insert_staged, "
            "not pg_catalog.has_column_privilege('authenticated','public.source_snapshots','published_at','INSERT') as snapshots_publish_denied, "
            "not pg_catalog.has_table_privilege('authenticated','public.source_snapshots','UPDATE') as snapshots_update_denied, "
            "not pg_catalog.has_table_privilege('authenticated','public.source_snapshots','DELETE') as snapshots_delete_denied, "
            "not pg_catalog.has_table_privilege('anon','public.source_snapshots','SELECT') as anon_snapshots_denied, "
            "not pg_catalog.has_table_privilege('service_role','public.source_snapshots','SELECT') as service_snapshots_denied, "
            "pg_catalog.has_table_privilege('authenticated','public.source_grants','SELECT') as grants_select, "
            "pg_catalog.has_table_privilege('authenticated','public.source_grants','DELETE') as grants_delete, "
            "pg_catalog.has_column_privilege('authenticated','public.source_grants','granted_by','INSERT') as grants_insert, "
            "not pg_catalog.has_table_privilege('authenticated','public.source_grants','UPDATE') as grants_update_denied, "
            "not pg_catalog.has_table_privilege('anon','public.source_grants','SELECT') as anon_grants_denied, "
            "not pg_catalog.has_table_privilege('service_role','public.source_grants','SELECT') as service_grants_denied",
            {
                "snapshots_select": bool,
                "snapshots_insert_staged": bool,
                "snapshots_publish_denied": bool,
                "snapshots_update_denied": bool,
                "snapshots_delete_denied": bool,
                "anon_snapshots_denied": bool,
                "service_snapshots_denied": bool,
                "grants_select": bool,
                "grants_delete": bool,
                "grants_insert": bool,
                "grants_update_denied": bool,
                "anon_grants_denied": bool,
                "service_grants_denied": bool,
            })
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Source snapshot grants differ from the migration manifest")
    if V_VERIFIER_IDENTITY in present:
        rows = api.query("select "
            "pg_catalog.has_function_privilege('authenticated','public.begin_snapshot_verification(uuid,uuid,uuid)','EXECUTE') as begin_rpc, "
            "pg_catalog.has_function_privilege('authenticated','public.publish_verified_snapshot(uuid,uuid,uuid,text,bigint,uuid,text)','EXECUTE') as publish_rpc, "
            "pg_catalog.has_function_privilege('authenticated','public.verifier_may_read_staged_object(text)','EXECUTE') as staged_read_rpc, "
            "not pg_catalog.has_function_privilege('anon','public.begin_snapshot_verification(uuid,uuid,uuid)','EXECUTE') as anon_begin_denied, "
            "not pg_catalog.has_function_privilege('anon','public.publish_verified_snapshot(uuid,uuid,uuid,text,bigint,uuid,text)','EXECUTE') as anon_publish_denied, "
            "pg_catalog.has_table_privilege('snapshot_publisher_owner','public.source_snapshots','SELECT') as owner_catalog_select, "
            "pg_catalog.has_table_privilege('snapshot_publisher_owner','storage.objects','SELECT') as owner_storage_select, "
            "pg_catalog.has_table_privilege('snapshot_publisher_owner','private.snapshot_verifier_allowlist','SELECT') as owner_allowlist_select, "
            "pg_catalog.has_function_privilege('snapshot_publisher_owner','private.verified_user_id()','EXECUTE') as owner_uid_rpc, "
            "not pg_catalog.has_table_privilege('snapshot_publisher_owner','private.snapshot_verifier_allowlist','INSERT') as owner_allowlist_insert_denied, "
            "not pg_catalog.has_table_privilege('snapshot_publisher_owner','private.snapshot_verifier_allowlist','UPDATE') as owner_allowlist_update_denied, "
            "not pg_catalog.has_table_privilege('snapshot_publisher_owner','private.snapshot_verifier_allowlist','DELETE') as owner_allowlist_delete_denied",
            {
                "begin_rpc": bool,
                "publish_rpc": bool,
                "staged_read_rpc": bool,
                "anon_begin_denied": bool,
                "anon_publish_denied": bool,
                "owner_catalog_select": bool,
                "owner_storage_select": bool,
                "owner_allowlist_select": bool,
                "owner_uid_rpc": bool,
                "owner_allowlist_insert_denied": bool,
                "owner_allowlist_update_denied": bool,
                "owner_allowlist_delete_denied": bool,
            })
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Snapshot verifier grants differ from the migration manifest")
    if V_ADMIN_AUDIT in present:
        rows = api.query("select "
            "pg_catalog.has_column_privilege('snapshot_verifier_allowlist_manager','private.snapshot_verifier_allowlist','user_id','SELECT') as manager_select, "
            "pg_catalog.has_table_privilege('snapshot_verifier_allowlist_manager','private.snapshot_verifier_allowlist','DELETE') as manager_delete, "
            "pg_catalog.has_column_privilege('snapshot_verifier_allowlist_manager','private.snapshot_verifier_allowlist','user_id','INSERT') as manager_insert, "
            "pg_catalog.has_table_privilege('snapshot_verifier_allowlist_manager','private.snapshot_verifier_allowlist_audit','SELECT') as audit_select, "
            "pg_catalog.has_column_privilege('snapshot_verifier_audit_owner','private.snapshot_verifier_allowlist_audit','operation','INSERT') as audit_insert, "
            "not pg_catalog.has_table_privilege('authenticated','private.snapshot_verifier_allowlist','SELECT') as authenticated_allowlist_denied, "
            "not pg_catalog.has_table_privilege('snapshot_publisher_owner','private.snapshot_verifier_allowlist_audit','SELECT') as owner_audit_denied",
            {
                "manager_select": bool,
                "manager_delete": bool,
                "manager_insert": bool,
                "audit_select": bool,
                "audit_insert": bool,
                "authenticated_allowlist_denied": bool,
                "owner_audit_denied": bool,
            })
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Snapshot allowlist administration grants differ from the migration manifest")


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
    parser.add_argument("mode", choices=("validate", "classify", "emit-ledger", "audit", "plan", "assert-plan", "verify"))
    parser.add_argument("--plan-file", type=Path)
    args = parser.parse_args()
    inventory = local_inventory()
    if args.mode == "validate":
        print(f"Manifest conforms to the reviewed inventory ({len(inventory)} files); "
              "static SQL analysis is a separate control ('classify')")
        return 0
    if args.mode in ("classify", "emit-ledger"):
        records, findings = analyse_migrations()
        if findings:
            for message in findings:
                print(f"blocked: {message}", file=sys.stderr)
            if args.mode == "emit-ledger":
                print("Refusing to emit an approval ledger: the inventory could not be "
                      "analyzed in full, so any skeleton would be incomplete.",
                      file=sys.stderr)
            return 1
        if args.mode == "emit-ledger":
            print(json.dumps(emit_ledger(records), indent=2))
            return 0
        try:
            assert_ledger(records)
        except GuardError as error:
            print(f"blocked: {error}", file=sys.stderr)
            return 1
        registry = sum(1 for record in records if record["tier"] == sql.TIER_REGISTRY)
        print(f"Static analysis passed: {len(records)} statements across "
              f"{len(inventory)} files, {registry} registry-approved, 0 hard-denied.")
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
