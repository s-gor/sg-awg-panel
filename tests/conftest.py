from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="sg-awg-panel-tests-"))
atexit.register(shutil.rmtree, _TEST_ROOT, True)

_TEST_PATHS = {
    "AWGPANEL_DB": _TEST_ROOT / "panel.db",
    "AWGPANEL_DATA_DIR": _TEST_ROOT / "data",
    "AWGPANEL_BACKUP_DIR": _TEST_ROOT / "backups",
    "AWGPANEL_ACCESS_JOBS_DIR": _TEST_ROOT / "access-jobs",
    "AWGPANEL_OPERATION_JOBS_DIR": _TEST_ROOT / "operation-jobs",
    "AWGPANEL_TRAFFIC_STATE_DIR": _TEST_ROOT / "traffic-rules",
    "AWGPANEL_TRAFFIC_LOCK": _TEST_ROOT / "traffic.lock",
    "AWGPANEL_OUTBOUND_CONFIG_DIR": _TEST_ROOT / "outbounds",
    "AWGPANEL_AWG_CONFIG_DIR": _TEST_ROOT / "amneziawg",
    "AWGPANEL_PLACEHOLDER_PATH": _TEST_ROOT / "placeholder" / "index.html",
    "AWGPANEL_ENV_FILE": _TEST_ROOT / "web.env",
    "AWGPANEL_UPDATE_STATUS": _TEST_ROOT / "update" / "status.json",
    "AWGPANEL_UPDATE_LOG": _TEST_ROOT / "update" / "update.log",
    "AWGPANEL_DNSMASQ_CONFIG": _TEST_ROOT / "dnsmasq" / "sg-awg-traffic.conf",
    "AWGPANEL_TRAFFIC_SCHEDULE_STATE": _TEST_ROOT / "traffic-rules" / "schedule-state.json",
}

for name, path in _TEST_PATHS.items():
    os.environ.setdefault(name, str(path))

os.environ.setdefault("AWGPANEL_SECRET_KEY", "test-secret-key-for-sg-awg-panel")
