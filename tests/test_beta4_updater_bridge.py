from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path, Path]:
    project = tmp_path / "project"
    systemd = tmp_path / "systemd"
    bin_dir = tmp_path / "bin"
    cleanup_dir = tmp_path / "cleanup"
    env_file = tmp_path / "web.env"
    status_file = tmp_path / "status.json"
    (project / "deploy").mkdir(parents=True)
    (project / ".venv/bin").mkdir(parents=True)
    systemd.mkdir()
    bin_dir.mkdir()
    env_file.write_text("AWGPANEL_DB=/tmp/panel.db\n", encoding="utf-8")
    python = project / ".venv/bin/python"
    python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    for name in (
        "install-traffic-service.sh",
        "install-traffic-maintenance.sh",
        "install-routing-service.sh",
        "install-routing-maintenance.sh",
    ):
        target = project / "deploy" / name
        target.write_bytes((ROOT / "deploy" / name).read_bytes())
        target.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$FAKE_SYSTEMCTL_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    log = tmp_path / "systemctl.log"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "FAKE_SYSTEMCTL_LOG": str(log),
            "SG_AWG_PROJECT_DIR": str(project),
            "SG_AWG_SYSTEMD_DIR": str(systemd),
            "SG_AWG_ENV_FILE": str(env_file),
            "SG_AWG_PANEL_UPDATE_STATUS": str(status_file),
            "SG_AWG_LEGACY_CLEANUP_DIR": str(cleanup_dir),
            "SG_AWG_LEGACY_CLEANUP_SCRIPT": str(cleanup_dir / "cleanup-beta4-bridge.sh"),
        }
    )
    return env, project, systemd, status_file, cleanup_dir


def _run(path: Path, env: dict[str, str]) -> None:
    subprocess.run(["bash", str(path)], env=env, check=True, text=True, capture_output=True)


def test_beta4_updater_command_sequence_succeeds_and_bridge_is_removed(tmp_path):
    env, project, systemd, status, cleanup_dir = _fixture(tmp_path)
    _run(project / "deploy/install-routing-service.sh", env)
    _run(project / "deploy/install-routing-maintenance.sh", env)

    routing_unit = (systemd / "sg-awg-routing.service").read_text(encoding="utf-8")
    assert "Requires=" not in routing_unit
    assert "restart sg-awg-traffic.service" in routing_unit
    assert "is-active --quiet sg-awg-traffic.service" in routing_unit

    # Exact legacy commands executed by the already-running Beta 4 updater.
    for args in (
        ["enable", "sg-awg-routing.service", "sg-awg-recovery.service"],
        ["enable", "--now", "sg-awg-routing-schedule.timer", "sg-awg-routing-lists.timer", "sg-awg-clients-maintenance.timer"],
        ["start", "sg-awg-routing-lists.service"],
        ["restart", "sg-awg-routing.service"],
    ):
        subprocess.run(["systemctl", *args], env=env, check=True)

    assert (systemd / "sg-awg-traffic.service").exists()
    assert (systemd / "sg-awg-traffic-schedule.timer").exists()
    assert (systemd / "sg-awg-routing.service").exists()
    assert (systemd / "sg-awg-routing-lists.service").exists()

    status.write_text(json.dumps({"state": "success", "version": "v0.1.0-rc4"}), encoding="utf-8")
    _run(cleanup_dir / "cleanup-beta4-bridge.sh", env)

    assert (systemd / "sg-awg-traffic.service").exists()
    assert (systemd / "sg-awg-traffic-schedule.timer").exists()
    assert not list(systemd.glob("sg-awg-routing*"))
    assert not list(systemd.glob("sg-awg-legacy-upgrade-cleanup*"))
    assert not (cleanup_dir / "cleanup-beta4-bridge.sh").exists()


def test_beta4_updater_rollback_removes_new_traffic_units_after_project_restore(tmp_path):
    env, project, systemd, status, cleanup_dir = _fixture(tmp_path)
    _run(project / "deploy/install-routing-service.sh", env)
    _run(project / "deploy/install-routing-maintenance.sh", env)

    # Simulate the old updater restoring the Beta 4 project tree. The cleanup
    # executable remains outside /opt/sg-awg-panel and can still remove Beta 8 units.
    for child in project.iterdir():
        if child.is_dir():
            import shutil
            shutil.rmtree(child)
        else:
            child.unlink()

    status.write_text(json.dumps({"state": "rolled_back", "version": "v0.1.0-rc4"}), encoding="utf-8")
    _run(cleanup_dir / "cleanup-beta4-bridge.sh", env)

    assert not (systemd / "sg-awg-traffic.service").exists()
    assert not (systemd / "sg-awg-traffic-schedule.timer").exists()
    assert (systemd / "sg-awg-routing.service").exists()
    assert not (cleanup_dir / "cleanup-beta4-bridge.sh").exists()
