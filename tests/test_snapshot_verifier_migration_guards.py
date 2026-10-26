"""Offline source-contract checks for Snapshot Verifier migrations.

These checks inspect migration text only. They do not execute SQL or prove
PostgreSQL, Supabase Storage, or remote-policy behavior.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CATALOG = (ROOT / "supabase/migrations/20261022100000_source_snapshot_catalog.sql").read_text()
STORAGE = (ROOT / "supabase/migrations/20261022110000_source_snapshot_storage_policies.sql").read_text()
IDENTITY = (ROOT / "supabase/migrations/20261022120000_snapshot_verifier_identity.sql").read_text()
CORRECTIVE = (ROOT / "supabase/migrations/20261022130000_snapshot_verifier_fail_closed_guards.sql").read_text()
ADMIN = (ROOT / "supabase/migrations/20261023150000_snapshot_verifier_admin_audit_guards.sql").read_text()
IDENTITY_SQL_TEST = (ROOT / "supabase/tests/snapshot_verifier_identity.test.sql").read_text()
CATALOG_SQL_TEST = (ROOT / "supabase/tests/source_snapshots.test.sql").read_text()
STORAGE_SQL_TEST = (ROOT / "supabase/tests/source_snapshot_storage.test.sql").read_text()


def test_role_guard_fails_closed_on_attributes_memberships_and_existing_objects():
    for attribute in (
        "rolcanlogin",
        "rolinherit",
        "rolsuper",
        "rolbypassrls",
        "rolcreaterole",
        "rolcreatedb",
        "rolreplication",
    ):
        assert attribute in CATALOG
    for membership_option in ("set_option", "inherit_option", "admin_option"):
        assert f"membership.{membership_option}" in CATALOG
    assert "pg_catalog.pg_has_role" in CATALOG
    assert "pg_catalog.pg_shdepend" in CATALOG
    assert "dependency.deptype in ('o', 'a')" in CATALOG
    assert "private.assert_snapshot_role_safe('snapshot_verifier')" in CATALOG
    assert "private.assert_snapshot_role_safe('snapshot_publisher_owner')" in IDENTITY
    assert "private.assert_snapshot_api_role_cannot_assume" in CORRECTIVE
    assert "noreplication" in IDENTITY
    assert "revoke snapshot_publisher_owner from %I" in IDENTITY
    assert "temporary snapshot_publisher_owner membership remains after revoke" in IDENTITY
    assert "could not remove the temporary snapshot_publisher_owner membership" not in IDENTITY


def test_bucket_helper_never_updates_existing_configuration():
    helper_start = STORAGE.index("create or replace function private.ensure_snapshot_bucket")
    helper_end = STORAGE.index("$function$;", helper_start) + len("$function$;")
    helper = STORAGE[helper_start:helper_end].lower()
    assert "on conflict (id) do nothing" in helper
    assert "do update" not in helper
    for field in ("name", "public", "file_size_limit", "allowed_mime_types"):
        assert f"existing_{field} is distinct from p_{field}" in helper
    assert "snapshot bucket name is already used by another bucket" in helper
    assert "snapshot bucket has conflicting name or security settings" in helper
    assert "security definer" not in helper
    assert "snapshot bucket" in STORAGE_SQL_TEST
    for expected_case in (
        "a missing test bucket is created",
        "rerunning with a compatible bucket",
        "a conflicting bucket name fails closed",
        "a public bucket conflicts",
        "a different size limit fails closed",
        "a different MIME allowlist fails closed",
        "a bucket name already owned by another id fails clearly",
    ):
        assert expected_case in STORAGE_SQL_TEST


def test_published_reads_require_the_new_binding_and_old_rows_are_hidden():
    assert "alter policy source_snapshots_select_granted_published_or_own_staged" in IDENTITY
    assert "source_snapshots.verified_by is not null" in IDENTITY
    assert "source_snapshots.storage_object_id is not null" in IDENTITY
    assert "source_snapshots.storage_object_version is not null" in IDENTITY
    assert "snapshot.storage_object_id = objects.id" in IDENTITY
    assert "snapshot.storage_object_version = objects.version" in IDENTITY
    assert "objects.owner_id = snapshot.created_by::text" in IDENTITY
    assert "objects.metadata ->> 'size'" in IDENTITY
    assert "drop policy if exists source_snapshots_select_verifier" in IDENTITY
    assert "drop policy if exists source_snapshots_objects_select_verifier" in IDENTITY
    for missing_field in ("verifier", "object_id", "object_version"):
        assert f"legacy_missing_{missing_field}_catalog" in STORAGE_SQL_TEST
        assert f"legacy_missing_{missing_field}_storage" in STORAGE_SQL_TEST
    assert "not valid" in IDENTITY.lower()
    assert "do not fabricate verifier identity or object binding" in IDENTITY.lower()


def test_security_definer_execute_acl_and_role_ownership_are_audited():
    assert "private.assert_snapshot_verifier_function_acl_safe" in IDENTITY
    assert "pg_catalog.aclexplode" in IDENTITY
    assert "acl.grantee not in (owner_role, authenticated_role)" in IDENTITY
    assert "acl.is_grantable" in IDENTITY
    assert "authenticated-only allowlist" in IDENTITY
    assert "fn.prosecdef" in IDENTITY
    assert "search_path=\"\"" in IDENTITY
    assert "pg_catalog.pg_shdepend" in IDENTITY
    assert "snapshot_publisher_owner owns an unapproved object" in IDENTITY
    assert "an unapproved EXECUTE grant causes the migration guard to fail" in IDENTITY_SQL_TEST
    assert "an extra table owned by the function owner is rejected" in IDENTITY_SQL_TEST


def test_snapshot_read_policy_inventory_and_restrictive_fences_are_fail_closed():
    assert "policy.polcmd in ('r', '*')" in CORRECTIVE
    assert "unapproved SELECT or ALL policy exists" in CORRECTIVE
    assert "policy.polroles = array[0::oid]" in CORRECTIVE
    assert "perform private.assert_snapshot_read_policy_inventory(false)" in CORRECTIVE
    assert "perform private.assert_snapshot_read_policy_inventory(true)" in CORRECTIVE
    assert "snapshot read restrictive fences are only partially installed" in CORRECTIVE
    assert "source_snapshots_select_verifier_role_gate_restrictive_fence" in CORRECTIVE
    assert "source_snapshots_objects_verifier_role_gate_restrictive_fence" in CORRECTIVE
    assert "source_snapshots_select_authenticated_restrictive_fence" in CORRECTIVE
    assert "source_snapshots_select_publisher_owner_restrictive_fence" in CORRECTIVE
    assert "as restrictive" in CORRECTIVE.lower()
    # PostgreSQL checks table privileges in every CASE branch, not only in the
    # branch that is taken, so the fences must be role scoped instead of
    # dispatching on current_user inside a single "to public" predicate.
    assert "case current_user" not in CORRECTIVE.lower()
    assert "to authenticated" in CORRECTIVE.lower()
    assert "to snapshot_publisher_owner" in CORRECTIVE.lower()
    assert "snapshot_api_membership_chain_bridge" in IDENTITY_SQL_TEST
    assert "rejects a transitive role-assumption path" in IDENTITY_SQL_TEST
    assert "a later PUBLIC SELECT policy cannot expose snapshot catalog rows" in CATALOG_SQL_TEST
    assert "a later PUBLIC ALL policy cannot expose snapshot catalog rows" in CATALOG_SQL_TEST
    assert "a later PUBLIC SELECT policy cannot expose source-snapshot objects" in STORAGE_SQL_TEST
    assert "a later PUBLIC ALL policy cannot expose source-snapshot objects" in STORAGE_SQL_TEST
    assert "snapshot_publisher_owner'::pg_catalog.name" in CORRECTIVE
    assert "cannot give the publisher owner reads from other buckets" in STORAGE_SQL_TEST


def test_sql_regression_scenarios_are_present():
    assert (
        "alter policy source_snapshots_select_verifier_role_gate_restrictive_fence"
        in CATALOG_SQL_TEST.lower()
    )
    assert (
        "alter policy source_snapshots_objects_verifier_role_gate_restrictive_fence"
        in STORAGE_SQL_TEST.lower()
    )
    assert "using (true)" in CATALOG_SQL_TEST.lower()
    assert "using (true)" in STORAGE_SQL_TEST.lower()
    assert "unknown restrictive policy" in CATALOG_SQL_TEST.lower()
    assert "unknown restrictive policy" in STORAGE_SQL_TEST.lower()
    assert "remains unchanged for operator review" in CATALOG_SQL_TEST.lower()
    assert "remains unchanged for operator review" in STORAGE_SQL_TEST.lower()
    assert "without a source grant cannot read catalog rows" in CATALOG_SQL_TEST.lower()
    assert "without a source grant cannot read snapshot objects" in STORAGE_SQL_TEST.lower()


def test_allowlist_shape_and_atomic_owner_transfer_are_checked_by_corrective_migration():
    for field in ("user_id", "workspace_id", "added_at"):
        assert f"attribute.attname = '{field}'" in CORRECTIVE
    assert "snapshot verifier allowlist has an incompatible primary key" in CORRECTIVE
    assert "snapshot verifier allowlist has incompatible foreign keys" in CORRECTIVE
    assert "snapshot verifier allowlist has incompatible constraints" in CORRECTIVE
    assert "attribute.attacl is not null" in CORRECTIVE
    assert "relforcerowsecurity" in CORRECTIVE
    assert "snapshot verifier allowlist has incompatible row policies" in CORRECTIVE

    transfer = CORRECTIVE.split("do $atomic_owner_transfer$", 1)[1].split(
        "$atomic_owner_transfer$;", 1
    )[0].lower()
    for operation in ("grant snapshot_publisher_owner", "alter function", "revoke snapshot_publisher_owner"):
        assert operation in transfer
    assert "temporary snapshot publisher membership or create privilege remains" in transfer
    assert "a simulated REVOKE failure is caught only after the owner transfer occurred" in IDENTITY_SQL_TEST


def test_admin_audit_migration_checks_privileged_state_without_claiming_human_identity():
    assert "rolcreaterole" in ADMIN
    assert "has_createrole_privilege" not in ADMIN
    assert "snapshot_verifier_allowlist_owner" in ADMIN
    assert "snapshot_verifier_allowlist_manager" in ADMIN
    assert "snapshot_verifier_audit_owner" in ADMIN
    assert "revoke all on table private.snapshot_verifier_read_policy_baseline" in ADMIN
    assert "snapshot read policy baseline has an unexpected owner or ACL" in ADMIN
    assert "snapshot guard function ownership or EXECUTE ACL is unsafe" in ADMIN
    assert "current_user" in ADMIN
    # All membership edges to these roles fail closed, except the automatic
    # ADMIN-only edge that PostgreSQL records for the non-superuser CREATEROLE
    # creator (current_user), which is tolerated without weakening the checks.
    assert "membership.member = current_user::pg_catalog.regrole" in ADMIN
    assert "not membership.set_option" in ADMIN
    assert "not membership.inherit_option" in ADMIN
    assert "pg_catalog.pg_auth_members" in ADMIN
    assert "pg_catalog.pg_shdepend" in ADMIN
    assert "pg_catalog.aclexplode" in ADMIN
    assert "pg_catalog.pg_get_expr(actual.polqual, actual.polrelid, true)" in ADMIN
    assert "snapshot_verifier_read_policy_baseline" in ADMIN
    assert "snapshot_verifier_allowlist_audit_select_manager" in ADMIN
    assert "session_user" in ADMIN
    assert "source_snapshots_published_verifier_binding" in ADMIN
    assert not re.search(r"^[ \t]*validate\s+constraint\b", ADMIN, re.IGNORECASE | re.MULTILINE)
    assert "manager cannot delete audit records" in IDENTITY_SQL_TEST
    assert "audit records the database session identity" in IDENTITY_SQL_TEST
    assert "an unapproved allowlist policy is rejected" in IDENTITY_SQL_TEST
    assert "the rejected allowlist policy remains in place unchanged" in IDENTITY_SQL_TEST
    assert "assert_snapshot_read_policy_inventory_v2(true)" in CATALOG_SQL_TEST
    assert "assert_snapshot_read_policy_inventory_v2(true)" in STORAGE_SQL_TEST


def test_admin_migration_refuses_partial_state_and_groups_finalization_in_do_block():
    assert "partial prior application" in ADMIN
    assert "only the verified current table owner or a superuser" in ADMIN
    assert "do $atomic_admin_install$" in ADMIN.lower()
    assert "revoke snapshot_verifier_allowlist_owner from" in ADMIN
    assert "temporary migration membership remains" in ADMIN
    assert "source_snapshots_protect_immutability" in ADMIN
    assert "source_snapshots_reject_delete" in ADMIN
    assert "source_snapshots_require_verification_binding" in ADMIN
    assert "source_snapshots_published_verifier_binding" in ADMIN


def test_pgtap_plan_counts_match_declared_assertions():
    assertion = re.compile(
        r"^[ \t]*select\s+(?:ok|is|isnt|not_ok|lives_ok|throws_ok|throws_like|"
        r"throws_matching|cmp_ok|like|unlike)\s*\(",
        re.IGNORECASE | re.MULTILINE,
    )
    expected_counts = (
        (IDENTITY_SQL_TEST, 119),
        (CATALOG_SQL_TEST, 86),
        (STORAGE_SQL_TEST, 68),
    )
    for sql, expected_count in expected_counts:
        declared = int(re.search(r"select\s+plan\((\d+)\)", sql, re.IGNORECASE).group(1))
        assert declared == expected_count
        assert len(assertion.findall(sql)) == declared


def test_rls_policy_names_are_pinned_nominatively():
    # A count alone stays green if one policy is renamed or swapped for another
    # of the same shape, so both pgTAP suites must assert every policy by name.
    catalog_policies = (
        "source_snapshots_insert_active_owners",
        "source_snapshots_select_granted_published_or_own_staged",
        "source_snapshots_select_publisher_owner",
        "source_snapshots_update_publisher_owner",
        "source_snapshots_select_verifier_role_gate_restrictive_fence",
        "source_snapshots_select_authenticated_restrictive_fence",
        "source_snapshots_select_publisher_owner_restrictive_fence",
    )
    storage_policies = (
        "source_snapshots_objects_insert_staged_by_owner",
        "source_snapshots_objects_select_granted_published",
        "source_snapshots_objects_select_allowlisted_verifier",
        "source_snapshots_objects_select_publisher_owner",
        "source_snapshots_objects_verifier_role_gate_restrictive_fence",
        "source_snapshots_objects_select_authenticated_restrictive_fence",
        "source_snapshots_objects_publisher_owner_restrictive_fence",
    )
    for policy_name in catalog_policies:
        assert (
            f"pg_temp.policy_signature('public.source_snapshots', '{policy_name}')"
            in CATALOG_SQL_TEST
        )
    for policy_name in storage_policies:
        assert (
            f"pg_temp.policy_signature('storage.objects', '{policy_name}')"
            in STORAGE_SQL_TEST
        )


def test_role_guard_tolerates_only_the_postgres17_admin_auto_grant():
    # PostgreSQL 16+ automatically grants a newly created role to its
    # non-superuser CREATEROLE creator (the migration actor, current_user) with
    # ADMIN OPTION (no SET, no INHERIT). The guard must tolerate exactly that
    # automatic edge while still rejecting every other membership.
    assert "membership.member = current_user::pg_catalog.regrole" in CATALOG
    assert "not membership.set_option" in CATALOG
    assert "not membership.inherit_option" in CATALOG
    assert "other_role.oid <> current_user::pg_catalog.regrole" in CATALOG
