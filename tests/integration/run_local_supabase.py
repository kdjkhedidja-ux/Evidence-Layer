from __future__ import annotations

import os
import random
import json
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
PROJECT_PREFIX = "evidence-layer-integration-"


def _docker_snapshot() -> dict[str, dict]:
    containers_result = _run(
        ["docker", "ps", "-aq", "--no-trunc"]
    )
    volumes_result = _run(["docker", "volume", "ls", "--format", "{{.Name}}"])
    networks_result = _run(
        ["docker", "network", "ls", "--no-trunc", "--format", "{{.ID}}|{{.Name}}"]
    )
    if any(
        result.returncode
        for result in (containers_result, volumes_result, networks_result)
    ):
        raise RuntimeError("Could not verify the Docker isolation boundary.")

    container_ids = [
        item.strip()
        for item in containers_result.stdout.splitlines()
        if item.strip()
    ]
    containers: dict[str, dict[str, str | set[str]]] = {}
    if container_ids:
        inspected = _run(
            [
                "docker",
                "inspect",
                "--format",
                '{{.Id}}|{{.Name}}|{{.State.Status}}|{{json .NetworkSettings.Ports}}',
                *container_ids,
            ]
        )
        if inspected.returncode:
            raise RuntimeError("Could not inspect Docker containers for isolation.")
        for line in inspected.stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) != 4:
                raise RuntimeError("Docker returned an invalid container inventory.")
            container_id, name, state, port_map = parts
            try:
                mappings = json.loads(port_map)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Docker returned invalid published-port metadata.") from exc
            host_ports = {
                binding["HostPort"]
                for bindings in (mappings or {}).values()
                if bindings
                for binding in bindings
                if isinstance(binding.get("HostPort"), str)
            }
            containers[container_id] = {
                "name": name.lstrip("/"),
                "state": state,
                "ports": host_ports,
            }

    networks: dict[str, str] = {}
    for line in networks_result.stdout.splitlines():
        network_id, separator, name = line.partition("|")
        if not separator:
            raise RuntimeError("Docker returned an invalid network inventory.")
        networks[name] = network_id

    return {
        "containers": containers,
        "volumes": {
            item.strip()
            for item in volumes_result.stdout.splitlines()
            if item.strip()
        },
        "networks": networks,
    }


def _assert_no_project_collision(snapshot: dict[str, dict], project_id: str) -> None:
    resources = [
        str(container["name"])
        for container in snapshot["containers"].values()
    ]
    resources.extend(snapshot["volumes"])
    resources.extend(snapshot["networks"])
    collisions = [name for name in resources if project_id in name]
    if collisions:
        raise RuntimeError(
            "The generated project id already exists in Docker resources; refusing to start."
        )


def _assert_ports_unclaimed(port: int, snapshot: dict[str, dict]) -> None:
    requested = set(range(port, port + 8))
    docker_ports = {
        int(host_port)
        for container in snapshot["containers"].values()
        for host_port in container["ports"]
    }
    collisions = requested & docker_ports
    if collisions:
        raise RuntimeError(
            "A selected isolated Supabase port is already published by Docker; refusing to start."
        )
    probes: list[socket.socket] = []
    try:
        for candidate in requested:
            probe = socket.socket()
            probes.append(probe)
            probe.bind(("0.0.0.0", candidate))
    except OSError as exc:
        raise RuntimeError(
            "A selected isolated Supabase port is already in use; refusing to start."
        ) from exc
    finally:
        for probe in probes:
            probe.close()


def _assert_baseline_preserved(before: dict[str, dict], after: dict[str, dict]) -> None:
    for container_id, container in before["containers"].items():
        current = after["containers"].get(container_id)
        if current is None or current["state"] != container["state"]:
            raise RuntimeError(
                "An existing Docker container changed during isolated integration testing."
            )
    if not before["volumes"].issubset(after["volumes"]):
        raise RuntimeError(
            "An existing Docker volume disappeared during isolated integration testing."
        )
    if any(after["networks"].get(name) != network_id for name, network_id in before["networks"].items()):
        raise RuntimeError(
            "An existing Docker network changed during isolated integration testing."
        )


def _assert_new_resources_owned(
    before: dict[str, dict], after: dict[str, dict], project_id: str
) -> None:
    pattern = re.compile(rf"^supabase_[a-z0-9_-]+_{re.escape(project_id)}$")
    new_containers = set(after["containers"]) - set(before["containers"])
    for container_id in new_containers:
        name = str(after["containers"][container_id]["name"])
        if not pattern.fullmatch(name):
            raise RuntimeError(
                "Supabase created a container outside the isolated project namespace."
            )
    new_volumes = after["volumes"] - before["volumes"]
    if any(not pattern.fullmatch(name) for name in new_volumes):
        raise RuntimeError(
            "Supabase created a volume outside the isolated project namespace."
        )
    new_networks = set(after["networks"]) - set(before["networks"])
    if any(not pattern.fullmatch(name) for name in new_networks):
        raise RuntimeError(
            "Supabase created a network outside the isolated project namespace."
        )


def _remove_owned_resources(
    before: dict[str, dict], project_id: str
) -> None:
    after = _docker_snapshot()
    _assert_baseline_preserved(before, after)
    pattern = re.compile(
        rf"^supabase_[a-z0-9_-]+_{re.escape(project_id)}$"
    )
    owned_containers: list[str] = []
    for container_id, container in after["containers"].items():
        name = str(container["name"])
        if project_id in name:
            if container_id in before["containers"] or not pattern.fullmatch(name):
                raise RuntimeError(
                    "A Supabase container did not match the isolated project's owned-resource boundary."
                )
            owned_containers.append(container_id)
    owned_volumes = [
        name
        for name in after["volumes"]
        if project_id in name
    ]
    owned_networks = [
        (name, network_id)
        for name, network_id in after["networks"].items()
        if project_id in name
    ]
    for name in [*owned_volumes, *(item[0] for item in owned_networks)]:
        if not pattern.fullmatch(name):
            raise RuntimeError(
                "A Docker resource did not match the isolated project's owned-resource boundary."
            )
    if any(name in before["volumes"] for name in owned_volumes) or any(
        name in before["networks"] for name, _ in owned_networks
    ):
        raise RuntimeError("Refusing to remove a pre-existing Docker resource.")

    if owned_containers:
        removed = _run(["docker", "rm", "--force", *owned_containers])
        if removed.returncode:
            raise RuntimeError("Could not remove the isolated integration containers.")
    if owned_volumes:
        removed = _run(["docker", "volume", "rm", *owned_volumes])
        if removed.returncode:
            raise RuntimeError("Could not remove the isolated integration volumes.")
    if owned_networks:
        removed = _run(
            ["docker", "network", "rm", *(network_id for _, network_id in owned_networks)]
        )
        if removed.returncode:
            raise RuntimeError("Could not remove the isolated integration networks.")

    final = _docker_snapshot()
    _assert_baseline_preserved(before, final)
    if any(project_id in str(item["name"]) for item in final["containers"].values()):
        raise RuntimeError("An isolated integration container remains after cleanup.")
    if any(project_id in name for name in final["volumes"]) or any(
        project_id in name for name in final["networks"]
    ):
        raise RuntimeError("An isolated integration volume or network remains after cleanup.")


# --- Scoped firewall management for the isolated bridge -----------------------
#
# On hosts where Docker programs iptables-legacy and the FORWARD policy is DROP
# (observed on this dev container and possible on CI self-hosted runners),
# containers on a newly created bridge cannot reach each other, so the storage
# migrate container times out connecting to the database and `supabase start`
# fails with DbSetupError. The runner adds exactly one scoped accept rule for
# the bridge it created, and removes exactly that rule during cleanup. It never
# lists, flushes, or modifies any other rule, and it never touches a rule it
# did not add itself.

_FIREWALL_RULE_JUMP = "ACCEPT"


def _legacy_forward_policy() -> str | None:
    result = _run(["sudo", "-n", "iptables-legacy", "-S", "FORWARD"])
    if result.returncode or not result.stdout:
        return None
    match = re.match(r"^-P FORWARD (\S+)", result.stdout.splitlines()[0])
    return match.group(1) if match else None


def _bridge_for_network(network: str) -> str | None:
    result = _run(
        [
            "docker",
            "network",
            "inspect",
            network,
            "--format",
            '{{index .Options "com.docker.network.bridge.name"}}|{{.Id}}',
        ]
    )
    if result.returncode:
        return None
    name, separator, network_id = result.stdout.strip().partition("|")
    if not separator:
        return None
    # Go templates render a missing option as "<no value>", not an empty string.
    if name and name != "<no value>":
        return name
    if len(network_id) >= 12:
        return f"br-{network_id[:12]}"
    return None


def _bridge_rule_arguments(bridge: str, operation: str) -> list[str]:
    return [
        "sudo",
        "-n",
        "iptables-legacy",
        operation,
        "FORWARD",
        "-i",
        bridge,
        "-o",
        bridge,
        "-j",
        _FIREWALL_RULE_JUMP,
    ]


def _ensure_bridge_rule(
    project_id: str, added_bridges: set[str], firewall: dict
) -> None:
    """Add the scoped bridge rule as soon as the isolated network exists.

    No-op until the network appears, once handled bridges, and whenever the
    host firewall does not drop forwarded traffic. Refuses to continue if the
    host drops forwarded traffic but the scoped rule cannot be added, because
    the stack would otherwise fail with an opaque database timeout.
    """
    if firewall.get("disabled"):
        return
    network = f"supabase_network_{project_id}"
    listing = _run(["docker", "network", "ls", "--format", "{{.Name}}"])
    if listing.returncode or network not in listing.stdout.split():
        return
    bridge = _bridge_for_network(network)
    if bridge is None:
        return
    handled = firewall.setdefault("handled", set())
    if bridge in handled or bridge in added_bridges:
        return
    handled.add(bridge)

    if "policy" not in firewall:
        firewall["policy"] = _legacy_forward_policy()
        if firewall["policy"] is None:
            # Cannot inspect the firewall (no sudo/iptables-legacy). Leave it
            # untouched; on hosts without a dropping policy the stack works.
            firewall["disabled"] = True
            return
    if firewall["policy"] != "DROP":
        firewall["disabled"] = True
        return

    if _run(_bridge_rule_arguments(bridge, "-C")).returncode == 0:
        # A rule for this exact bridge already exists. It cannot come from this
        # run (bridge names derive from fresh network ids), so it is foreign
        # and must be left alone; the needed accept behavior is already there.
        return
    added = _run(_bridge_rule_arguments(bridge, "-I"))
    if added.returncode:
        raise RuntimeError(
            "The host drops forwarded traffic (legacy FORWARD policy is DROP) "
            "and the scoped accept rule for the isolated bridge could not be "
            "added; refusing to let `supabase start` fail with an opaque "
            "database timeout. Grant passwordless sudo for iptables-legacy or "
            "fix the Docker firewall configuration."
        )
    added_bridges.add(bridge)


def _remove_bridge_rules(added_bridges: set[str]) -> None:
    """Remove exactly the rules this runner added, then prove they are gone."""
    for bridge in sorted(added_bridges):
        _run(_bridge_rule_arguments(bridge, "-D"))
    for bridge in sorted(added_bridges):
        if _run(_bridge_rule_arguments(bridge, "-C")).returncode == 0:
            raise RuntimeError(
                "The scoped firewall rule for the isolated bridge was not removed."
            )


def _start_stack(
    arguments: list[str],
    project_id: str,
    added_bridges: set[str],
    firewall: dict,
    log_path: Path,
) -> subprocess.CompletedProcess:
    """Run `supabase start`, installing the bridge rule before migration needs it.

    The CLI creates the network, starts the database, and only then runs the
    storage migrate container, so polling for the network while start runs
    installs the rule deterministically instead of racing the migration.
    """
    with open(log_path, "w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(
                arguments,
                cwd=REPOSITORY,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            raise RuntimeError(
                "Could not launch the Supabase CLI; verify it is installed."
            ) from exc
        deadline = time.monotonic() + 600
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        "Isolated `supabase start` exceeded its time budget."
                    )
                _ensure_bridge_rule(project_id, added_bridges, firewall)
                time.sleep(0.5)
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
    output = log_path.read_text(encoding="utf-8", errors="replace")
    return subprocess.CompletedProcess(arguments, process.returncode, output, "")


def _available_port_block(size: int = 8) -> int:
    candidates = list(range(55_000, 62_000 - size))
    random.SystemRandom().shuffle(candidates)
    for start in candidates:
        sockets: list[socket.socket] = []
        try:
            for port in range(start, start + size):
                sock = socket.socket()
                sock.bind(("127.0.0.1", port))
                sockets.append(sock)
            return start
        except OSError:
            continue
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("Could not reserve a free port range for isolated Supabase.")


def _run(
    arguments: list[str],
    *,
    capture: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            arguments,
            cwd=REPOSITORY,
            check=False,
            text=True,
            capture_output=capture,
            env=env,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"Could not run {Path(arguments[0]).name}; verify Supabase CLI and Docker availability."
        ) from exc


def _project_directory(root: Path, project_id: str, port: int) -> Path:
    project = root / "project"
    local = project / "supabase"
    local.mkdir(parents=True)
    shutil.copy2(REPOSITORY / "supabase/config.toml", local / "config.toml")
    for directory in ("migrations", "tests"):
        shutil.copytree(REPOSITORY / "supabase" / directory, local / directory)

    config = (local / "config.toml").read_text()
    config = re.sub(
        r'(?m)^project_id\s*=\s*"[^"]+"$',
        f'project_id = "{project_id}"',
        config,
        count=1,
    )
    config = re.sub(r"(?m)^port\s*=\s*54321$", f"port = {port + 1}", config, count=1)
    config = re.sub(r"(?m)^port\s*=\s*54322$", f"port = {port + 2}", config, count=1)
    config = re.sub(
        r"(?m)^shadow_port\s*=\s*54320$",
        f"shadow_port = {port}",
        config,
        count=1,
    )
    config += (
        "\n[inbucket]\n"
        "enabled = true\n"
        f"port = {port + 4}\n"
        f"smtp_port = {port + 5}\n"
        f"pop3_port = {port + 6}\n"
        "\n[analytics]\n"
        "enabled = false\n"
        "\n[edge_runtime]\n"
        "enabled = false\n"
        "\n[db.pooler]\n"
        "enabled = false\n"
        f"port = {port + 7}\n"
    )
    (local / "config.toml").write_text(config)
    parsed = tomllib.loads(config)
    if parsed.get("project_id") != project_id:
        raise RuntimeError("The isolated Supabase project id was not applied.")
    if (
        parsed.get("api", {}).get("port") != port + 1
        or parsed.get("db", {}).get("port") != port + 2
        or parsed.get("db", {}).get("shadow_port") != port
        or parsed.get("inbucket", {}).get("port") != port + 4
        or parsed.get("inbucket", {}).get("smtp_port") != port + 5
        or parsed.get("inbucket", {}).get("pop3_port") != port + 6
        or parsed.get("db", {}).get("pooler", {}).get("enabled") is not False
        or parsed.get("analytics", {}).get("enabled") is not False
        or parsed.get("edge_runtime", {}).get("enabled") is not False
    ):
        raise RuntimeError("The isolated Supabase ports and disabled services did not validate.")
    return project


def _status_environment(project: Path) -> dict[str, str]:
    result = _run(
        ["supabase", "--workdir", str(project), "status", "-o", "env"]
    )
    if result.returncode:
        raise RuntimeError("Could not read the isolated Supabase status.")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            values[name.strip()] = value.strip().strip("\"'")
    return values


def _database_container(project_id: str) -> str:
    result = _run(
        [
            "docker",
            "ps",
            "--filter",
            f"name=supabase_db_{project_id}",
            "--format",
            "{{.Names}}",
        ]
    )
    if result.returncode:
        raise RuntimeError("Could not identify the isolated Supabase database container.")
    expected = f"supabase_db_{project_id}"
    names = [name.strip() for name in result.stdout.splitlines() if name.strip()]
    if names != [expected]:
        raise RuntimeError("The isolated Supabase database container was not uniquely identified.")
    return expected


def _redact_line(line: str) -> str:
    line = re.sub(r"eyJ[A-Za-z0-9._-]+", "[redacted-token]", line)
    line = re.sub(r"(://[^:/\s]+:)[^@/\s]+(@)", r"\1[redacted]\2", line)
    line = re.sub(
        r"(?i)((?:password|secret|token|anon_key|service_role_key|jwt)[\"'=:\s]+)[^\s,}]+",
        r"\1[redacted]",
        line,
    )
    return line[:500]


def _safe_diagnostic(output: str) -> str:
    safe_lines = []
    for line in output.splitlines():
        if re.search(r"key|secret|password|token|credential", line, re.I):
            continue
        if re.search(r"error|failed|invalid|denied|timeout|not found|fatal", line, re.I):
            safe_lines.append(_redact_line(line))
    return "\n".join(safe_lines[-8:])


def _container_diagnostics(project_id: str) -> str:
    lines: list[str] = []
    ps = _run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name={project_id}",
            "--format",
            "{{.Names}} {{.Status}}",
        ]
    )
    if ps.returncode == 0 and ps.stdout.strip():
        lines.append("Isolated containers at failure:")
        lines.extend("  " + line for line in ps.stdout.splitlines())
    db_name = f"supabase_db_{project_id}"
    logs = _run(["docker", "logs", "--tail", "80", db_name])
    if logs.returncode == 0:
        raw = ((logs.stdout or "") + "\n" + (logs.stderr or "")).strip()
        if raw:
            redacted = [_redact_line(line) for line in raw.splitlines()]
            lines.append(f"Database container log tail ({db_name}, redacted):")
            lines.extend("  " + line for line in redacted[-25:])
    return "\n".join(lines)


def main() -> int:
    # Docker/Supabase CLI truncate resource names; keep the project id short so
    # the full id always appears in container, network, and volume names.
    project_id = PROJECT_PREFIX + secrets.token_hex(5)
    if len(project_id) > 40:
        raise RuntimeError("The isolated project id is too long for Docker resource names.")
    baseline = _docker_snapshot()
    _assert_no_project_collision(baseline, project_id)
    port = _available_port_block()
    _assert_ports_unclaimed(port, baseline)
    with tempfile.TemporaryDirectory(prefix=project_id + "-") as temporary:
        project = _project_directory(Path(temporary), project_id, port)
        workdir = project
        start_attempted = False
        test_return_code = 1
        cleanup_error: str | None = None
        added_bridges: set[str] = set()
        firewall: dict = {}
        try:
            start_attempted = True
            start_arguments = [
                "supabase",
                "--debug",
                "--workdir",
                str(workdir),
                "start",
                "--exclude",
                "studio,realtime,imgproxy,postgres-meta,edge-runtime,logflare,vector,supavisor",
            ]
            start_log = Path(temporary) / "supabase-start.log"
            start = _start_stack(
                start_arguments, project_id, added_bridges, firewall, start_log
            )
            if start.returncode:
                # A single retry gives the temporary PostgreSQL container time to
                # finish initializing on resource-constrained hosts.
                start = _start_stack(
                    start_arguments, project_id, added_bridges, firewall, start_log
                )
            if start.returncode:
                diagnostic = _safe_diagnostic(
                    (start.stdout or "") + "\n" + (start.stderr or "")
                )
                container_diagnostics = _container_diagnostics(project_id)
                message = (
                    "Isolated `supabase start` failed. The existing project stack was not targeted."
                )
                if diagnostic:
                    message += "\n" + diagnostic
                if container_diagnostics:
                    message += "\n" + container_diagnostics
                raise RuntimeError(message)
            after_start = _docker_snapshot()
            _assert_baseline_preserved(baseline, after_start)
            _assert_new_resources_owned(baseline, after_start, project_id)
            database_container = _database_container(project_id)
            reset = _run(
                ["supabase", "--workdir", str(workdir), "db", "reset", "--local"]
            )
            if reset.returncode:
                raise RuntimeError("Migrations failed on the isolated Supabase stack.")
            status = _status_environment(project)
            api_url = status.get("API_URL")
            mailpit_url = status.get("INBUCKET_URL") or status.get("MAILPIT_URL")
            # Newer CLI versions redact the legacy JWT anon key in status output;
            # the publishable key is the supported apikey for local services.
            publishable_key = status.get("PUBLISHABLE_KEY") or status.get("ANON_KEY")
            if not all((api_url, mailpit_url, publishable_key)):
                raise RuntimeError(
                    "Supabase status did not provide API_URL, a mail URL, and a publishable key."
                )
            environment = os.environ.copy()
            environment.update(
                {
                    "EVIDENCE_LAYER_LOCAL_STORAGE_URL": api_url,
                    "EVIDENCE_LAYER_LOCAL_STORAGE_PUBLISHABLE_KEY": publishable_key,
                    "EVIDENCE_LAYER_LOCAL_AUTH_MAILPIT_URL": mailpit_url,
                    "EVIDENCE_LAYER_LOCAL_DB_CONTAINER": database_container,
                    "EVIDENCE_LAYER_LOCAL_PROJECT_ID": project_id,
                    "EVIDENCE_LAYER_LOCAL_STACK_DISPOSABLE": "1",
                }
            )
            tests = _run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "tests/integration",
                ],
                capture=False,
                env=environment,
            )
            test_return_code = tests.returncode
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            test_return_code = 1
        finally:
            if start_attempted:
                try:
                    _remove_bridge_rules(added_bridges)
                except RuntimeError:
                    cleanup_error = (
                        "Could not prove removal of the isolated bridge firewall rule."
                    )
                try:
                    _remove_owned_resources(baseline, project_id)
                except RuntimeError:
                    cleanup_error = (
                        "Could not prove safe cleanup of the isolated Supabase resources."
                    )
        if cleanup_error:
            print(cleanup_error, file=sys.stderr)
            return 1
        return test_return_code


if __name__ == "__main__":
    raise SystemExit(main())
