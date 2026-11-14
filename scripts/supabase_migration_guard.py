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
# Production target. Overridable for an explicitly reviewed environment, but
# the default must point at the live project so a stale ref cannot 404.
PROJECT_REF = os.environ.get("SUPABASE_PROJECT_REF") or "slxfsjnsrvfkdtxayiud"
PROJECT_NAME = os.environ.get("SUPABASE_PROJECT_NAME", "Evidence Layer")

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
V_EVIDENCE_SOURCE_LINKS = "20261101120000"
V_EVIDENCE_RELATIONSHIPS = "20261101130000"
V_ACCOUNT_LIFECYCLE = "20261102120000"
V_EVIDENCE_DELETION_AUDIT = "20261105100000"
V_DELETION_REQUEST_MANIFEST = "20261105110000"
V_ACCOUNT_DELETION_WORKER = "20261105120000"
V_ACCOUNT_APPLICATION_PURGE = "20261105130000"
V_ACCOUNT_EVIDENCE_OWNERSHIP = "20261105140000"
V_DELETION_RESTORE_RECONCILIATION = "20261105150000"
V_PERSONAL_WORKSPACE_DELETION = "20261108120000"
V_ACCOUNT_TYPE = "20261109120000"
V_ORGANIZATIONS = "20261109130000"
V_GROUP_DELETION = "20261109140000"
V_PERSONAL_API_KEYS = "20261110230000"
V_INDIVIDUAL_ACCOUNT_TERMINAL = "20261108130000"
V_AUDIT_FK_DROP = "20261108140000"
V_ACCEPT_V2 = "20261108150000"
V_ACCEPT_V3 = "20261108170000"
V_AUDIT_ACTIONS_V2 = "20261108200000"
V_AUTH_IDENTIFIER_FKS = "20261108210000"
V_MARK_PURGED_V3 = "20261108220000"
V_ORG_WORKSPACE_PURGE = "20261111120000"
V_IDEMPOTENT_PREPARE = "20261111150000"
V_PHASE8_EXPORT = "20261111180000"
V_CONNECTORS = "20261111190000"
V_API_KEY_DOCUMENT = "20261112120000"
V_CONNECTOR_BRIDGE = "20261112140000"
V_CONNECTOR_EVIDENCE_RPC = "20261112160000"
V_TASK_RUN_LEDGER = "20261114210000"

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
        configured = os.environ.get("SUPABASE_PROJECT_REF") or ""
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
                "where n.nspname in ('public','private','auth') and not t.tgisinternal "
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
                "from pg_catalog.pg_roles where rolname like 'snapshot\\_%' "
                "or rolname like 'account\\_%' "
                "or rolname like 'personal\\_api\\_key\\_%' order by rolname",
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
                "and membership.member in (current_user::pg_catalog.regrole, 'postgres'::pg_catalog.regrole) "
                "and membership.admin_option "
                "and not membership.set_option "
                "and not membership.inherit_option"
                ")::pg_catalog.int8 as allowed_admin_memberships,"
                "count(membership.roleid) filter (where "
                "membership.roleid=role_row.oid "
                "and membership.member in (current_user::pg_catalog.regrole, 'postgres'::pg_catalog.regrole) "
                "and not membership.admin_option "
                "and membership.set_option "
                "and not membership.inherit_option"
                ")::pg_catalog.int8 as allowed_set_memberships "
                "from pg_catalog.pg_roles as role_row "
                "left join pg_catalog.pg_auth_members as membership "
                "on membership.roleid=role_row.oid or membership.member=role_row.oid "
                "where role_row.rolname like 'snapshot\\_%' or role_row.rolname like 'account\\_%' "
                "or role_row.rolname like 'personal\\_api\\_key\\_%' "
                "group by role_row.oid,role_row.rolname order by role_row.rolname",
                {
                    "name": str,
                    "membership_count": int,
                    "allowed_admin_memberships": int,
                    "allowed_set_memberships": int,
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
    if V_ACCOUNT_LIFECYCLE in present:
        roles.update({
            "account_lifecycle_provisioner",
            "account_lifecycle_transition_writer",
        })
    if V_ACCOUNT_DELETION_WORKER in present:
        roles.add("account_deletion_worker")
    if V_PERSONAL_API_KEYS in present:
        roles.update({"personal_api_key_manager", "personal_api_key_reader"})
    return roles


# The account migrations grant these isolated NOLOGIN roles to the migration
# actor with SET (never INHERIT) so ownership transfers and the test harness can
# assume them. That is one documented SET edge per role; any other edge is
# unexpected. Snapshot roles have no such grant.
SET_MEMBERSHIP_ROLES = frozenset({
    "account_lifecycle_provisioner",
    "account_lifecycle_transition_writer",
    "account_deletion_worker",
})


def expected_policy_identity(policy: str) -> str:
    schema, name = policy.split(".", 1)
    relation_prefixes = (
        ("source_snapshots_objects_", "storage", "objects"),
        ("workspace_memberships_", "public", "workspace_memberships"),
        ("workspaces_", "public", "workspaces"),
        ("evidence_records_", "public", "evidence_records"),
        ("evidence_relationships_", "public", "evidence_relationships"),
        ("source_snapshots_", "public", "source_snapshots"),
        ("source_grants_", "public", "source_grants"),
        ("account_lifecycle_", "public", "account_lifecycle"),
        ("accounts_", "public", "accounts"),
        ("organizations_", "public", "organizations"),
        ("organization_memberships_", "public", "organization_memberships"),
        ("evidence_deletion_audit_", "public", "evidence_deletion_audit"),
        ("data_deletion_requests_", "public", "data_deletion_requests"),
        ("data_deletion_manifest_", "public", "data_deletion_manifest"),
        ("data_deletion_work_set_", "public", "data_deletion_work_set"),
        ("data_deletion_storage_queue_", "public", "data_deletion_storage_queue"),
        ("snapshot_verifier_allowlist_audit_", "private", "snapshot_verifier_allowlist_audit"),
        ("snapshot_verifier_allowlist_", "private", "snapshot_verifier_allowlist"),
        ("personal_api_key_gateway_secrets_", "private", "personal_api_key_gateway_secrets"),
        ("personal_api_key_gateway_nonces_", "private", "personal_api_key_gateway_nonces"),
        ("personal_api_keys_", "public", "personal_api_keys"),
        ("connector_connections_", "public", "connector_connections"),
        ("connector_documents_", "public", "connector_documents"),
        ("task_run_ledger_", "public", "task_run_ledger"),
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
    evidence_links = V_EVIDENCE_SOURCE_LINKS in present
    relationships = V_EVIDENCE_RELATIONSHIPS in present
    account_lifecycle = V_ACCOUNT_LIFECYCLE in present
    deletion_audit = V_EVIDENCE_DELETION_AUDIT in present
    deletion_requests = V_DELETION_REQUEST_MANIFEST in present
    deletion_worker = V_ACCOUNT_DELETION_WORKER in present
    application_purge = V_ACCOUNT_APPLICATION_PURGE in present
    evidence_ownership = V_ACCOUNT_EVIDENCE_OWNERSHIP in present
    restore_reconciliation = V_DELETION_RESTORE_RECONCILIATION in present
    personal_deletion = V_PERSONAL_WORKSPACE_DELETION in present
    account_type = V_ACCOUNT_TYPE in present
    organizations = V_ORGANIZATIONS in present
    group_deletion = V_GROUP_DELETION in present
    api_keys = V_PERSONAL_API_KEYS in present
    individual_terminal = V_INDIVIDUAL_ACCOUNT_TERMINAL in present
    audit_fk_drop = V_AUDIT_FK_DROP in present
    accept_v2 = V_ACCEPT_V2 in present
    accept_v3 = V_ACCEPT_V3 in present
    audit_actions_v2 = V_AUDIT_ACTIONS_V2 in present
    auth_identifier_fks = V_AUTH_IDENTIFIER_FKS in present
    mark_purged_v3 = V_MARK_PURGED_V3 in present
    org_workspace_purge = V_ORG_WORKSPACE_PURGE in present
    idempotent_prepare = V_IDEMPOTENT_PREPARE in present
    phase8_export = V_PHASE8_EXPORT in present
    connectors = V_CONNECTORS in present
    api_key_document = V_API_KEY_DOCUMENT in present
    connector_bridge = V_CONNECTOR_BRIDGE in present
    connector_evidence_rpc = V_CONNECTOR_EVIDENCE_RPC in present
    task_run_ledger = V_TASK_RUN_LEDGER in present
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
    if relationships:
        tables["public.evidence_relationships"] = (True, True)
    if source:
        tables["public.source_snapshots"] = (True, True)
        tables["public.source_grants"] = (True, True)
    if verifier:
        tables["private.snapshot_verifier_allowlist"] = (True, True)
    if admin:
        tables["private.snapshot_verifier_read_policy_baseline"] = (False, False)
        tables["private.snapshot_verifier_allowlist_audit"] = (True, True)
    if account_lifecycle:
        tables["public.account_lifecycle"] = (True, True)
    if deletion_audit:
        tables["public.evidence_deletion_audit"] = (True, True)
    if deletion_requests:
        tables["public.data_deletion_requests"] = (True, True)
        tables["public.data_deletion_manifest"] = (True, True)
    if application_purge:
        tables["public.data_deletion_work_set"] = (True, True)
    if personal_deletion:
        tables["public.data_deletion_storage_queue"] = (True, True)
    if account_type:
        tables["public.accounts"] = (True, True)
    if organizations:
        tables["public.organizations"] = (True, True)
        tables["public.organization_memberships"] = (True, True)
    if api_keys:
        tables["public.personal_api_keys"] = (True, True)
        tables["private.personal_api_key_gateway_secrets"] = (True, True)
        tables["private.personal_api_key_gateway_nonces"] = (True, True)
    if phase8_export:
        tables["public.data_export_jobs"] = (True, True)
        tables["public.data_export_grants"] = (True, True)
    if connectors:
        tables["public.connector_connections"] = (True, True)
        tables["public.connector_documents"] = (True, True)
    if task_run_ledger:
        tables["public.task_run_ledger"] = (True, True)

    indexes: set[str] = set()
    if identity:
        indexes.update({
            "public.workspaces_pkey",
            "public.workspace_memberships_workspace_user_key",
            "public.workspace_memberships_user_status_workspace_idx",
        })
    if evidence:
        indexes.update({"public.evidence_records_pkey", "public.evidence_records_workspace_created_idx"})
    if evidence_links:
        indexes.add("public.evidence_records_source_link_idx")
    if relationships:
        indexes.update({
            "public.evidence_records_workspace_id_id_key",
            "public.evidence_relationships_pkey",
            "public.evidence_relationships_pair_type_unique",
            "public.evidence_relationships_workspace_idx",
        })
    if personal:
        indexes.add("public.workspaces_personal_owner_unique")
    if personal_deletion:
        indexes.update({
            "public.data_deletion_storage_queue_pkey",
            "public.data_deletion_storage_queue_workspace_object_key",
        })
    if account_type:
        indexes.add("public.accounts_pkey")
    if organizations:
        indexes.update({
            "public.organizations_pkey",
            "public.organizations_owner_user_unique",
            "public.organization_memberships_pkey",
            "public.organization_memberships_user_active_idx",
        })
    if api_keys:
        indexes.update({
            "public.personal_api_keys_pkey",
            "public.personal_api_keys_key_hash_key",
            "public.personal_api_keys_user_active_idx",
            "public.personal_api_keys_workspace_idx",
            "private.personal_api_key_gateway_secrets_pkey",
            "private.personal_api_key_gateway_nonces_pkey",
            "private.personal_api_key_gateway_nonces_expiry_idx",
        })
    if connectors:
        indexes.update({
            "public.connector_connections_pkey",
            "public.connector_connections_user_provider_key",
            "public.connector_connections_user_idx",
            "public.connector_documents_pkey",
            "public.connector_documents_revision_key",
            "public.connector_documents_connection_idx",
            "public.connector_documents_user_idx",
        })
    if connector_bridge:
        indexes.discard("public.connector_documents_revision_key")
        indexes.update({
            "public.connector_documents_external_id_key",
            "public.evidence_records_connector_external_key",
        })
    if task_run_ledger:
        indexes.update({
            "public.task_run_ledger_pkey",
            "public.task_run_ledger_workspace_created_idx",
        })
    if phase8_export:
        indexes.update({
            "public.data_export_jobs_pkey",
            "public.data_export_jobs_idempotency_key",
            "public.data_export_jobs_requester_created_idx",
            "public.data_export_jobs_expiry_idx",
            "public.data_export_grants_pkey",
            "public.data_export_grants_export_id_key",
            "public.data_export_grants_token_hash_key",
            "public.data_export_grants_requester_idx",
        })
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
    if account_lifecycle:
        indexes.update({
            "public.account_lifecycle_pkey",
            "public.account_lifecycle_transition_id_key",
        })
    if deletion_audit:
        indexes.add("public.evidence_deletion_audit_pkey")
        indexes.add("public.evidence_deletion_audit_request_action_key")
    if deletion_requests:
        indexes.update({
            "public.data_deletion_requests_pkey",
            "public.data_deletion_requests_open_scope_key",
            "public.data_deletion_requests_idempotency_key",
            "public.data_deletion_manifest_pkey",
            "public.data_deletion_manifest_request_id_ordinal_key",
            "public.data_deletion_manifest_request_id_target_type_target_id_key",
        })
    if application_purge:
        indexes.update({
            "public.data_deletion_work_set_pkey",
            "public.data_deletion_work_set_request_idx",
        })
        if not evidence_ownership:
            indexes.add("public.data_deletion_work_set_request_id_target_type_workspace_id_source_id_principal_id_key")
    if evidence_ownership:
        indexes.add("public.data_deletion_work_set_request_target_key")

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
        policies.add("public.evidence_records_select_active_members")
        if relationships:
            policies.update({
                "public.evidence_records_insert_active_owners",
                "public.evidence_records_update_active_owners",
                "public.evidence_records_delete_active_owners",
            })
        else:
            policies.update({
                "public.evidence_records_insert_active_members",
                "public.evidence_records_update_active_members",
                "public.evidence_records_delete_active_members",
            })
    if relationships:
        policies.update({
            "public.evidence_relationships_select_active_members",
            "public.evidence_relationships_insert_active_owners",
            "public.evidence_relationships_update_active_owners",
            "public.evidence_relationships_delete_active_owners",
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
    if task_run_ledger:
        policies.update({
            "public.task_run_ledger_select_active_member",
            "public.task_run_ledger_insert_active_member",
        })
    if account_lifecycle:
        policies.update({
            "public.account_lifecycle_insert_provisioner",
            "public.account_lifecycle_transition_writer_select",
            "public.account_lifecycle_transition_writer_update",
            "public.account_lifecycle_select_self",
        })
    if deletion_audit:
        policies.update({
            "public.evidence_deletion_audit_select_denied",
            "public.evidence_deletion_audit_insert_denied",
            "public.evidence_deletion_audit_update_denied",
            "public.evidence_deletion_audit_delete_denied",
        })
        policies.add("public.evidence_deletion_audit_insert_transition_writer")
    if deletion_requests:
        policies.update({
            "public.data_deletion_requests_insert_self",
            "public.data_deletion_requests_select_self",
            "public.data_deletion_requests_transition_writer_select",
            "public.data_deletion_requests_transition_writer_update",
            "public.data_deletion_manifest_select_self",
            "public.data_deletion_manifest_transition_writer_select",
            "public.data_deletion_manifest_transition_writer_insert",
        })
    if deletion_worker:
        policies.update({
            "public.data_deletion_requests_worker_select_claimable",
            "public.data_deletion_requests_worker_update_claim",
            "public.account_lifecycle_worker_select_bound_request",
            "public.account_lifecycle_worker_update_bound_request",
            "public.source_grants_delete_worker_bound_request",
            "public.source_grants_select_worker_bound_request",
            "public.evidence_deletion_audit_insert_worker",
        })
    if application_purge:
        policies.update({
            "public.data_deletion_work_set_worker_select",
            "public.data_deletion_work_set_worker_insert",
            "public.workspace_memberships_worker_select_member_rows",
            "public.workspace_memberships_worker_delete_member_rows",
            "public.evidence_deletion_audit_insert_worker_workset",
        })
    if evidence_ownership:
        policies.update({
            "public.workspace_memberships_worker_select_eligible_workspace",
            "public.evidence_records_worker_select_eligible_workspace",
            "public.evidence_records_worker_delete_eligible_workspace",
            "public.evidence_relationships_worker_select_eligible_workspace",
            "public.evidence_relationships_worker_delete_eligible_workspace",
        })
    if restore_reconciliation:
        policies.add("public.evidence_deletion_audit_insert_worker_reconciliation")
    if individual_terminal:
        policies.update({
            "public.workspaces_worker_select_owned",
            "public.workspaces_worker_delete_owned",
            "public.workspace_memberships_worker_delete_subject",
        })
    if account_type:
        policies.add("public.accounts_select_self")
    if organizations:
        policies.update({
            "public.organizations_select_member",
            "public.organization_memberships_select_self",
        })
    if connectors:
        policies.update({
            "public.connector_connections_owner_select",
            "public.connector_connections_owner_insert",
            "public.connector_connections_owner_update",
            "public.connector_connections_owner_delete",
            "public.connector_documents_owner_select",
            "public.connector_documents_owner_insert",
            "public.connector_documents_owner_update",
            "public.connector_documents_owner_delete",
        })
    if api_keys:
        policies.update({
            "public.personal_api_keys_select_own",
            "public.personal_api_keys_manager_select_own",
            "public.personal_api_keys_manager_insert_own",
            "public.personal_api_keys_manager_revoke_own",
            "public.personal_api_keys_reader_select_exact",
            "public.personal_api_keys_reader_update_exact",
            "public.workspace_memberships_personal_api_key_manager_select",
            "public.workspace_memberships_personal_api_key_reader_select",
            "public.account_lifecycle_personal_api_key_manager_select",
            "public.account_lifecycle_personal_api_key_reader_select",
            "public.evidence_records_personal_api_key_reader_select",
            "public.evidence_relationships_personal_api_key_reader_select",
            "private.personal_api_key_gateway_secrets_reader_select",
            "private.personal_api_key_gateway_nonces_reader_select",
            "private.personal_api_key_gateway_nonces_reader_insert",
            "private.personal_api_key_gateway_nonces_reader_delete",
        })

    triggers: set[str] = set()
    if evidence:
        triggers.add("public.evidence_records_protect_workspace_ownership")
    if relationships:
        triggers.add("public.evidence_relationships_protect_identity")
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
    if account_lifecycle:
        triggers.update({
            "public.account_lifecycle_validate_insert",
            "public.account_lifecycle_version_transition",
            "auth.account_lifecycle_after_auth_insert",
        })
    if account_type:
        triggers.add("auth.accounts_after_auth_insert")
    if deletion_audit:
        triggers.update({
            "public.evidence_records_audit_delete",
            "public.evidence_relationships_audit_delete",
            "public.evidence_deletion_audit_append_only",
        })
    if deletion_requests:
        triggers.update({
            "public.data_deletion_requests_validate_insert",
            "public.data_deletion_requests_audit_created",
            "public.data_deletion_manifest_append_only",
            "public.data_deletion_manifest_audit_frozen",
        })
    if deletion_worker:
        triggers.add("public.account_lifecycle_worker_transition")

    functions: set[str] = set()
    if rls_auto:
        functions.add("public.rls_auto_enable")
    if personal:
        functions.add("public.ensure_personal_workspace")
    if evidence:
        functions.add("private.protect_evidence_record")
    if relationships:
        functions.add("private.protect_evidence_relationship")
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
    if account_lifecycle:
        functions.update({
            "private.enforce_account_lifecycle_insert",
            "private.enforce_account_lifecycle_transition",
            "private.provision_account_lifecycle_for_auth_user",
        })
    if deletion_audit:
        functions.update({
            "private.audit_evidence_delete",
            "private.audit_relationship_delete",
            "private.reject_evidence_deletion_audit_mutation",
        })
    if deletion_requests:
        functions.update({
            "private.enforce_deletion_request_insert",
            "private.reject_deletion_manifest_mutation",
            "private.audit_deletion_request_created",
            "private.audit_deletion_manifest_frozen",
            "public.accept_account_deletion",
        })
    if deletion_worker:
        functions.update({
            "private.enforce_account_deletion_worker_transition",
            "private.run_account_deletion_slice",
        })
    if evidence_ownership:
        functions.add("private.deletion_workspace_is_sole_owner")
    if restore_reconciliation:
        functions.add("private.reconcile_account_deletion_slice")
    if personal_deletion:
        functions.update({
            "public.delete_personal_workspace_data",
            "public.delete_personal_workspace",
        })
    if individual_terminal:
        functions.update({
            "private.prepare_individual_account_purge",
            "private.mark_account_purged",
            "private.purge_snapshot_catalog_for_account",
        })
    if accept_v2:
        functions.add("public.accept_account_deletion_v2")
    if accept_v3:
        functions.update({
            "public.accept_account_deletion_v3",
            "private.delete_account_lifecycle_for_purge",
        })
    if audit_actions_v2:
        functions.update({
            "private.prepare_individual_account_purge_v2",
            "private.mark_account_purged_v2",
        })
    if mark_purged_v3:
        functions.add("private.mark_account_purged_v3")
    if group_deletion:
        functions.add("public.delete_group_workspace")
    if org_workspace_purge:
        functions.update({
            "private.deletion_workspace_is_account_owned",
            "private.prepare_individual_account_purge_v3",
        })
    if idempotent_prepare:
        functions.add("private.account_prepared_marker_exists")
    if phase8_export:
        functions.update({
            "public.finalize_account_export",
            "public.redeem_account_export_grant",
            "public.revoke_account_export",
            "private.cleanup_expired_exports",
        })
    if account_type:
        functions.update({
            "private.provision_account_type_for_auth_user",
            "public.create_organization_account",
        })
    if organizations:
        functions.update({
            "public.organization_overview",
            "public.create_group_workspace",
            "public.add_organization_member",
            "public.update_organization_member_role",
            "public.remove_organization_member",
        })
    if api_key_document:
        functions.add("public.read_personal_api_key_evidence_document")
    if connector_evidence_rpc:
        functions.add("public.upsert_connector_evidence")
    if api_keys:
        functions.update({
            "public.create_personal_api_key",
            "public.revoke_personal_api_key",
            "public.read_personal_api_key_evidence",
            "public.read_personal_api_key_relationships",
            "private.authorize_personal_api_key_read",
            "private.configure_personal_api_key_gateway_secret",
            "private.personal_api_key_subject",
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
    if relationships:
        foreign_keys.update({
            ("evidence_relationships_workspace_id_fkey", "public.evidence_relationships", "public.workspaces", "c"),
            ("evidence_relationships_evidence_a_fkey", "public.evidence_relationships", "public.evidence_records", "c"),
            ("evidence_relationships_evidence_b_fkey", "public.evidence_relationships", "public.evidence_records", "c"),
            ("evidence_relationships_created_by_fkey", "public.evidence_relationships", "auth.users", "a"),
            ("evidence_relationships_updated_by_fkey", "public.evidence_relationships", "auth.users", "a"),
        })
    if evidence_links:
        foreign_keys.add(("evidence_records_source_link_fkey", "public.evidence_records", "public.source_snapshots", "r"))
    if source:
        foreign_keys.update({
            ("source_snapshots_workspace_id_fkey", "public.source_snapshots", "public.workspaces", "r"),
            ("source_snapshots_created_by_fkey", "public.source_snapshots", "auth.users", "a"),
            ("source_grants_member_fkey", "public.source_grants", "public.workspace_memberships", "c"),
            ("source_grants_granted_by_fkey", "public.source_grants", "auth.users", "a"),
        })
    if account_lifecycle:
        foreign_keys.add((
            "account_lifecycle_subject_id_fkey",
            "public.account_lifecycle",
            "auth.users",
            "r",
        ))
    if account_type:
        foreign_keys.add((
            "accounts_user_id_fkey",
            "public.accounts",
            "auth.users",
            "c",
        ))
    if organizations:
        foreign_keys.update({
            ("organizations_owner_user_id_fkey", "public.organizations", "auth.users", "c"),
            ("organization_memberships_organization_id_fkey", "public.organization_memberships", "public.organizations", "c"),
            ("organization_memberships_user_id_fkey", "public.organization_memberships", "auth.users", "c"),
            ("accounts_organization_id_fkey", "public.accounts", "public.organizations", "c"),
            ("workspaces_organization_id_fkey", "public.workspaces", "public.organizations", "c"),
        })
    if phase8_export:
        foreign_keys.update({
            ("data_export_jobs_requester_user_id_fkey", "public.data_export_jobs", "auth.users", "c"),
            ("data_export_grants_export_id_fkey", "public.data_export_grants", "public.data_export_jobs", "c"),
            ("data_export_grants_requester_user_id_fkey", "public.data_export_grants", "auth.users", "c"),
        })
    if connectors:
        foreign_keys.update({
            ("connector_connections_user_id_fkey", "public.connector_connections", "auth.users", "c"),
            ("connector_documents_user_id_fkey", "public.connector_documents", "auth.users", "c"),
            ("connector_documents_connection_id_fkey", "public.connector_documents", "public.connector_connections", "c"),
        })
    if connector_bridge:
        foreign_keys.update({
            ("connector_connections_workspace_id_fkey", "public.connector_connections", "public.workspaces", "c"),
            ("evidence_records_connector_connection_id_fkey", "public.evidence_records", "public.connector_connections", "c"),
        })
    if api_keys:
        foreign_keys.update({
            ("personal_api_keys_user_id_fkey", "public.personal_api_keys", "auth.users", "c"),
            ("personal_api_keys_workspace_member_fkey", "public.personal_api_keys", "public.workspace_memberships", "c"),
        })
    if deletion_audit and not audit_fk_drop:
        audit_fk_action = "n" if personal_deletion else "r"
        foreign_keys.update({
            (
                "evidence_deletion_audit_actor_user_id_fkey",
                "public.evidence_deletion_audit",
                "auth.users",
                audit_fk_action,
            ),
            (
                "evidence_deletion_audit_workspace_id_fkey",
                "public.evidence_deletion_audit",
                "public.workspaces",
                audit_fk_action,
            ),
        })
    if deletion_requests:
        foreign_keys.update({
            (
                "account_lifecycle_request_id_fkey",
                "public.account_lifecycle",
                "public.data_deletion_requests",
                "r",
            ),
            (
                "data_deletion_manifest_request_id_fkey",
                "public.data_deletion_manifest",
                "public.data_deletion_requests",
                "r",
            ),
        })
        if not auth_identifier_fks:
            foreign_keys.update({
                (
                    "data_deletion_requests_requester_user_id_fkey",
                    "public.data_deletion_requests",
                    "auth.users",
                    "r",
                ),
                (
                    "data_deletion_requests_scope_id_fkey",
                    "public.data_deletion_requests",
                    "auth.users",
                    "r",
                ),
                (
                    "data_deletion_manifest_target_id_fkey",
                    "public.data_deletion_manifest",
                    "auth.users",
                    "r",
                ),
            })
    if application_purge:
        foreign_keys.add((
            "data_deletion_work_set_request_id_fkey",
            "public.data_deletion_work_set",
            "public.data_deletion_requests",
            "r",
        ))
        if not auth_identifier_fks:
            foreign_keys.add((
                "data_deletion_work_set_principal_id_fkey",
                "public.data_deletion_work_set",
                "auth.users",
                "r",
            ))
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
            set(row) == {"name", "membership_count", "allowed_admin_memberships", "allowed_set_memberships"},
            f"Unexpected role membership columns at index {index}",
        )
        require(
            isinstance(row["name"], str)
            and type(row["membership_count"]) is int
            and type(row["allowed_admin_memberships"]) is int
            and type(row["allowed_set_memberships"]) is int,
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
        admin_count = row["allowed_admin_memberships"]
        set_count = row["allowed_set_memberships"]
        expected_set = 1 if role_name in SET_MEMBERSHIP_ROLES else 0
        require(
            admin_count in (0, 1) and set_count in (0, 1) and set_count == expected_set
            and membership_count == admin_count + set_count,
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
    if V_EVIDENCE_RELATIONSHIPS in present:
        rows = api.query("select "
            "pg_catalog.has_table_privilege('authenticated','public.evidence_relationships','SELECT') as relationship_select, "
            "pg_catalog.has_table_privilege('authenticated','public.evidence_relationships','INSERT') as relationship_insert, "
            "pg_catalog.has_table_privilege('authenticated','public.evidence_relationships','DELETE') as relationship_delete, "
            "pg_catalog.has_column_privilege('authenticated','public.evidence_relationships','rationale','UPDATE') as relationship_content_update, "
            "not pg_catalog.has_column_privilege('authenticated','public.evidence_relationships','workspace_id','UPDATE') as relationship_workspace_immutable, "
            "not pg_catalog.has_column_privilege('authenticated','public.evidence_relationships','created_by','UPDATE') as relationship_creator_immutable, "
            "not pg_catalog.has_table_privilege('anon','public.evidence_relationships','SELECT') as anon_relationship_denied, "
            "not pg_catalog.has_table_privilege('service_role','public.evidence_relationships','SELECT') as service_relationship_denied",
            {
                "relationship_select": bool,
                "relationship_insert": bool,
                "relationship_delete": bool,
                "relationship_content_update": bool,
                "relationship_workspace_immutable": bool,
                "relationship_creator_immutable": bool,
                "anon_relationship_denied": bool,
                "service_relationship_denied": bool,
            })
        require(len(rows) == 1 and all(value is True for value in rows[0].values()), "Relationship grants differ from the migration manifest")
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


EXTERNAL_RECOVERY_PATH = ROOT / "supabase" / "recovery-attestation.json"
RECOVERY_MAX_AGE_SECONDS = 48 * 3600


def _is_recent(timestamp: str) -> bool:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return 0 <= age <= RECOVERY_MAX_AGE_SECONDS


def load_external_recovery(path: Path | None = None) -> dict | None:
    configured = os.environ.get("SUPABASE_EXTERNAL_RECOVERY_ATTESTATION", "")
    candidate = Path(configured) if configured else (path or EXTERNAL_RECOVERY_PATH)
    if not candidate.is_file():
        return None
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def external_recovery_is_recent(attestation: dict | None) -> bool:
    """A documented substitute for Supabase PITR: an uploaded, drilled dump.

    The attestation is repository-reviewed, expires after 48 hours, and must
    point at an existing drill report. It does not replace Supabase backups when
    one is available.
    """
    if not isinstance(attestation, dict):
        return False
    drill = attestation.get("drill")
    if not isinstance(drill, dict):
        return False
    evidence = drill.get("evidence")
    return bool(
        attestation.get("provider") == "r2"
        and attestation.get("bucket") == "evidence-layer-db-backups"
        and isinstance(attestation.get("object_key"), str)
        and re.fullmatch(r"dumps/[0-9A-Za-z._/-]+\.dump", attestation["object_key"])
        and isinstance(attestation.get("endpoint"), str)
        and re.fullmatch(r"https://[a-z0-9]+\.r2\.cloudflarestorage\.com", attestation["endpoint"])
        and isinstance(attestation.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", attestation["sha256"])
        and type(attestation.get("size_bytes")) is int
        and attestation["size_bytes"] > 0
        and isinstance(attestation.get("attested_by"), str)
        and bool(attestation["attested_by"].strip())
        and _is_recent(attestation.get("uploaded_at", ""))
        and drill.get("status") == "PASS"
        and isinstance(drill.get("target"), str)
        and "non-prod" in drill["target"].lower()
        and isinstance(evidence, str)
        and (ROOT / evidence).is_file()
        and _is_recent(drill.get("completed_at", ""))
    )


def build_plan(api: Supabase, inventory: list[dict[str, str]], require_recovery: bool) -> dict:
    api.target()
    local_versions = [item["version"] for item in inventory]
    remote_versions = api.history()
    require(remote_versions == local_versions[:len(remote_versions)], "Remote migration history is not a prefix of reviewed local migrations")
    assert_catalog(api.catalog(), remote_versions)
    assert_grants(api, remote_versions)
    backup = api.backup()
    external = load_external_recovery()
    external_ok = external_recovery_is_recent(external)
    if require_recovery:
        require(
            recovery_is_recent(backup) or external_ok,
            "No recent completed backup, PITR, or valid external recovery attestation verified",
        )
    commit = os.environ.get("GITHUB_SHA", "")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "GITHUB_SHA must identify the exact reviewed commit")
    plan = {"target_ref": PROJECT_REF, "target_name": PROJECT_NAME, "commit": commit,
            "files": inventory, "remote_applied": remote_versions,
            "pending": local_versions[len(remote_versions):], "recovery": backup,
            "external_recovery": external if external_ok else None}
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
