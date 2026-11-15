#!/usr/bin/env python3
"""Reproducible isolated restore drill for the Evidence Layer database.

This script is read-only against production: it creates a logical dump through
the verified pooler, restores it into a disposable PostgreSQL 17 container,
runs the deletion reconciliation verifier, writes a content-free evidence JSON,
and removes the container and local dump.

Environment: DATABASE_URL, SUPABASE_PROJECT_REF, optional
RESTORE_DRILL_POOLER_HOST (default aws-1-eu-central-1.pooler.supabase.com),
RESTORE_DRILL_POOLER_PORT (5432).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunparse

PG_IMAGE = "postgres:17"
CONTAINER_PREFIX = "evidence-layer-restore-drill"
TARGET_PASSWORD = "drillpass"


def run(command, *, env=None, input_text=None, timeout=600, allow_codes=(0,)):
    result = subprocess.run(
        command, env=env, input=input_text, capture_output=True, text=True,
        timeout=timeout,
    )
    if result.returncode not in allow_codes:
        detail = (result.stderr or "").strip()[-500:]
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)[:160]} | {detail}"
        )
    return result


def pooler_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    project_ref = os.environ.get("SUPABASE_PROJECT_REF")
    if not database_url or not project_ref:
        raise RuntimeError("DATABASE_URL and SUPABASE_PROJECT_REF are required")
    parsed = urlsplit(database_url)
    user = parsed.username or "postgres"
    if "." not in user:
        user = f"{user}.{project_ref}"
    password = parsed.password or ""
    host = os.environ.get(
        "RESTORE_DRILL_POOLER_HOST", "aws-1-eu-central-1.pooler.supabase.com"
    )
    port = os.environ.get("RESTORE_DRILL_POOLER_PORT", "5432")
    netloc = f"{user}:{quote(password, safe='')}@{host}:{port}"
    return urlunparse(
        (parsed.scheme or "postgresql", netloc, parsed.path or "/postgres", "", "sslmode=require", "")
    )


def wait_for_postgres(container: str) -> None:
    for _ in range(60):
        result = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", "postgres"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("restore target did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="evidence-restore-drill-"))
    dump_path = workdir / "evidence-layer.dump"
    schema_path = workdir / "schema.sql"
    container = f"{CONTAINER_PREFIX}-{uuid.uuid4().hex[:10]}"
    target_port = 55434
    evidence: dict = {
        "event": "restore_drill",
        "status": "failed",
        "dump_bytes": 0,
        "source_counts": {},
        "restored_counts": {},
        "verifier": None,
    }
    try:
        env = dict(os.environ, POOLER_URL=pooler_url())
        run([
            "docker", "run", "--rm", "-e", "POOLER_URL",
            "-v", f"{workdir}:/backup", PG_IMAGE, "sh", "-c",
            'pg_dump "$POOLER_URL" --format=custom '
            '--schema=public --schema=private --no-owner --no-privileges '
            '--file=/backup/evidence-layer.dump',
        ], env=env)
        evidence["dump_bytes"] = dump_path.stat().st_size

        counts_sql = (
            "select 'workspaces',count(*) from public.workspaces union all "
            "select 'evidence_records',count(*) from public.evidence_records union all "
            "select 'deletion_requests',count(*) from public.data_deletion_requests union all "
            "select 'audit',count(*) from public.evidence_deletion_audit union all "
            "select 'memberships',count(*) from public.workspace_memberships;"
        )
        source = run([
            "docker", "run", "--rm", "-i", "-e", "POOLER_URL", PG_IMAGE, "sh", "-c",
            'psql "$POOLER_URL" -At -F "|"',
        ], env=env, input_text=counts_sql)
        evidence["source_counts"] = dict(
            line.split("|") for line in source.stdout.splitlines() if "|" in line
        )

        run([
            "docker", "run", "-d", "--name", container,
            "-v", f"{workdir}:/backup",
            "-e", f"POSTGRES_PASSWORD={TARGET_PASSWORD}",
            "-p", f"127.0.0.1:{target_port}:5432", PG_IMAGE,
        ])
        wait_for_postgres(container)

        roles_result = run([
            "docker", "run", "--rm", "-i", "-e", "POOLER_URL", PG_IMAGE, "sh", "-c",
            'psql "$POOLER_URL" -At',
        ], env=env, input_text=(
            "select rolname from pg_roles where rolname not like 'pg\\_%' "
            "and rolname <> 'postgres' order by 1;"
        ))
        roles = [line for line in roles_result.stdout.splitlines() if line.strip()]
        role_sql = []
        for role in roles:
            escaped = role.replace("'", "''")
            role_sql.append(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
                f"'{escaped}') THEN EXECUTE 'CREATE ROLE ' || quote_ident('{escaped}') "
                "|| ' NOLOGIN'; END IF; END $$;"
            )
        stubs = """
create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key, email text,
  raw_user_meta_data jsonb, created_at timestamptz, updated_at timestamptz);
create or replace function auth.uid() returns uuid language sql stable as
  $$ select null::uuid $$;
create schema if not exists storage;
create table if not exists storage.buckets (id text primary key, name text,
  owner uuid, created_at timestamptz, updated_at timestamptz, public boolean,
  avif_autodetection boolean, file_size_limit bigint,
  allowed_mime_types text[], owner_id text);
create table if not exists storage.objects (id uuid primary key, bucket_id text,
  name text, owner uuid, created_at timestamptz, updated_at timestamptz,
  last_accessed_at timestamptz, metadata jsonb, path_tokens text[],
  version text, owner_id text, user_metadata jsonb);
"""
        run([
            "docker", "exec", "-i", "-e", f"PGPASSWORD={TARGET_PASSWORD}",
            container, "psql", "-U", "postgres", "-d", "postgres",
            "-v", "ON_ERROR_STOP=1",
        ], input_text="\n".join(role_sql) + "\n" + stubs)

        run([
            "docker", "run", "--rm", "-v", f"{workdir}:/backup", PG_IMAGE,
            "sh", "-c",
            "pg_restore --schema-only --no-owner --no-privileges "
            "-f /backup/schema.sql /backup/evidence-layer.dump",
        ])
        schema = schema_path.read_text()
        fk_pairs = re.findall(
            r"ALTER TABLE ONLY ([\w.]+)\s+ADD CONSTRAINT \w+ FOREIGN KEY "
            r"\(([^)]+)\) REFERENCES auth\.users",
            schema, re.I,
        )
        warnings = []
        for section in ("pre-data", "data"):
            result = run([
                "docker", "exec", "-e", f"PGPASSWORD={TARGET_PASSWORD}",
                container, "pg_restore", "--no-owner", "--no-privileges",
                f"--section={section}", "-d", "postgres", "-U", "postgres",
                "/backup/evidence-layer.dump",
            ], allow_codes=(0, 1))
            if result.stderr.strip():
                warnings.append(result.stderr.strip()[-300:])
        inserts = []
        for table, columns in fk_pairs:
            for column in columns.split(","):
                column = column.strip()
                inserts.append(
                    f"insert into auth.users (id) select distinct {column} from "
                    f"{table.strip()} where {column} is not null "
                    "on conflict (id) do nothing;"
                )
        run([
            "docker", "exec", "-i", "-e", f"PGPASSWORD={TARGET_PASSWORD}",
            container, "psql", "-U", "postgres", "-d", "postgres",
            "-v", "ON_ERROR_STOP=1",
        ], input_text="\n".join(inserts))
        result = run([
            "docker", "exec", "-e", f"PGPASSWORD={TARGET_PASSWORD}",
            container, "pg_restore", "--no-owner", "--no-privileges",
            "--section=post-data", "-d", "postgres", "-U", "postgres",
            "/backup/evidence-layer.dump",
        ], allow_codes=(0, 1))
        if result.stderr.strip():
            warnings.append(result.stderr.strip()[-300:])
        evidence["restore_warnings"] = warnings

        restored = run([
            "docker", "exec", "-e", f"PGPASSWORD={TARGET_PASSWORD}",
            container, "psql", "-U", "postgres", "-d", "postgres",
            "-At", "-F", "|", "-c", counts_sql,
        ])
        evidence["restored_counts"] = dict(
            line.split("|") for line in restored.stdout.splitlines() if "|" in line
        )

        verifier = subprocess.run(
            [sys.executable, "-m", "evidence_service.restore_reconciliation"],
            env=dict(
                os.environ,
                PYTHONPATH="services/evidence-service",
                RESTORE_VERIFY_DATABASE_URL=(
                    f"postgresql://postgres:{TARGET_PASSWORD}@127.0.0.1:{target_port}/postgres"
                ),
            ),
            capture_output=True, text=True, timeout=120,
        )
        if verifier.returncode not in (0, 2):
            raise RuntimeError("restore reconciliation verifier failed")
        evidence["verifier"] = json.loads(verifier.stdout)
        evidence["status"] = "clean" if verifier.returncode == 0 else "resurrected"

        output = json.dumps(evidence, indent=2, sort_keys=True)
        if args.evidence:
            Path(args.evidence).write_text(output + "\n")
        print(output)
        return 0
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"event": "restore_drill", "status": "error",
                          "error": type(error).__name__,
                          "message": str(error)[:800]}), file=sys.stderr)
        raise SystemExit(1)
