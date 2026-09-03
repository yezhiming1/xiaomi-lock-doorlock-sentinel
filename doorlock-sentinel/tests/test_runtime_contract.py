from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

from doorlock_sentinel import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_surfaces_are_consistent():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    package = json.loads(
        (ROOT / "services" / "wecom-bot" / "package.json").read_text(encoding="utf-8")
    )
    package_lock = json.loads(
        (ROOT / "services" / "wecom-bot" / "package-lock.json").read_text(
            encoding="utf-8"
        )
    )

    assert version == "0.0.6"
    assert __version__ == version
    assert package["version"] == version
    assert package_lock["version"] == version
    assert package_lock["packages"][""]["version"] == version
    assert f"image: doorlock-sentinel:{version}" in (
        ROOT / "compose.yaml"
    ).read_text(encoding="utf-8")
    assert f'org.opencontainers.image.version="{version}"' in (
        ROOT / "Dockerfile"
    ).read_text(encoding="utf-8")
    upgrade_dockerfile = (ROOT / "Dockerfile.upgrade").read_text(encoding="utf-8")
    upgrade_script = (ROOT / "scripts" / "build_upgrade_image.sh").read_text(
        encoding="utf-8"
    )
    assert "ARG PREDECESSOR_IMAGE=doorlock-sentinel:0.0.5" in upgrade_dockerfile
    assert f'org.opencontainers.image.version="{version}"' in upgrade_dockerfile
    assert "PREDECESSOR_IMAGE=doorlock-sentinel:0.0.5" in upgrade_script
    assert f"TARGET_IMAGE=doorlock-sentinel:{version}" in upgrade_script
    index = (ROOT / "src" / "doorlock_sentinel" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert f"/app.css?v={version}" in index
    assert f"/time-format.js?v={version}" in index
    assert f"/app.js?v={version}" in index
    assert f"<title>门锁观察簿 V{version}</title>" in index
    assert f"门锁观察簿 <span class=\"app-version\">V{version}</span>" in index


def test_browser_time_contract_is_explicitly_beijing_time():
    static = ROOT / "src" / "doorlock_sentinel" / "static"
    helper = (static / "time-format.js").read_text(encoding="utf-8")
    app = (static / "app.js").read_text(encoding="utf-8")

    assert 'BEIJING_TIME_ZONE = "Asia/Shanghai"' in helper
    assert "timeZone: BEIJING_TIME_ZONE" in helper
    assert "const { dayLabel, formatDate } = globalThis.DoorlockTime" in app
    assert "按北京时间排列" in app


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
