from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_container_exposes_only_the_host_loopback_boundary():
    supervisor = (ROOT / "docker" / "supervisord.conf").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "--host 0.0.0.0 --port 8787" in supervisor
    assert '"127.0.0.1:${DOORLOCK_HTTP_PORT:-18125}:8787"' in compose
    assert "pidfile=/run/supervisord.pid" in supervisor
    assert "COPY src/doorlock_sentinel ./src/doorlock_sentinel" in dockerfile
    assert "COPY src ./src" not in dockerfile
    assert "rm -rf /app/src/*.egg-info" in dockerfile
    assert "adduser doorlock root" in dockerfile
    assert "      - KILL" in compose
    assert "      - DAC_OVERRIDE" not in compose
    assert dockerfile.index("-r requirements.lock") < dockerfile.index(
        "COPY src/doorlock_sentinel"
    )


def test_entrypoint_initializes_persistent_data_without_dac_override():
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "data_owner=$(stat -c '%u' /data)" in entrypoint
    assert "runuser -u doorlock -- mkdir -p /data/derived /data/exports" in entrypoint
    assert "chown doorlock:doorlock /data /data/derived" not in entrypoint
    assert entrypoint.index("chown doorlock:doorlock /data/derived /data/exports") < (
        entrypoint.index("chown doorlock:doorlock /data\n")
    )
    assert (
        'if [ "$(runuser -u doorlock -- stat -c \'%u\' "$directory")" != "10001" ]'
        in entrypoint
    )


def test_application_logs_use_literal_privacy_safe_codes_only():
    methods = {"debug", "info", "warning", "error", "critical", "exception"}
    for path in sorted((ROOT / "src" / "doorlock_sentinel").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (
                not isinstance(owner, ast.Name)
                or owner.id != "logger"
                or node.func.attr not in methods
            ):
                continue
            assert len(node.args) == 1, f"dynamic log arguments in {path.name}:{node.lineno}"
            assert isinstance(node.args[0], ast.Constant), (
                f"non-literal log message in {path.name}:{node.lineno}"
            )
            assert isinstance(node.args[0].value, str)
            assert "code=" in node.args[0].value, (
                f"log without fixed code in {path.name}:{node.lineno}"
            )
