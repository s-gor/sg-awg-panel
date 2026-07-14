#!/usr/bin/env bash
# One-time compatibility bridge used only by an updater process that started
# from SG-AWG-Panel Beta 4. These units are removed after a successful update.
set -Eeuo pipefail

PROJECT_DIR="${SG_AWG_PROJECT_DIR:-/opt/sg-awg-panel}"
SYSTEMD_DIR="${SG_AWG_SYSTEMD_DIR:-/etc/systemd/system}"
CLEANUP_DIR="${SG_AWG_LEGACY_CLEANUP_DIR:-/usr/local/lib/sg-awg-panel}"
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

bash "$PROJECT_DIR/deploy/install-traffic-maintenance.sh"

cat > "$SYSTEMD_DIR/sg-awg-routing-schedule.service" <<'UNIT'
[Unit]
Description=Temporary SG-AWG-Panel Beta 4 schedule bridge
After=sg-awg-panel.service

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart sg-awg-traffic.service
ExecStart=/bin/systemctl is-active --quiet sg-awg-traffic.service
UNIT

cat > "$SYSTEMD_DIR/sg-awg-routing-schedule.timer" <<'UNIT'
[Unit]
Description=Temporary SG-AWG-Panel Beta 4 schedule timer bridge

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
Unit=sg-awg-routing-schedule.service

[Install]
WantedBy=timers.target
UNIT

cat > "$SYSTEMD_DIR/sg-awg-routing-lists.service" <<'UNIT'
[Unit]
Description=Temporary SG-AWG-Panel Beta 4 list refresh bridge

[Service]
Type=oneshot
ExecStart=/bin/true
UNIT

cat > "$SYSTEMD_DIR/sg-awg-routing-lists.timer" <<'UNIT'
[Unit]
Description=Temporary SG-AWG-Panel Beta 4 list timer bridge

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
Unit=sg-awg-routing-lists.service

[Install]
WantedBy=timers.target
UNIT

mkdir -p "$CLEANUP_DIR"
cat > "$CLEANUP_DIR/cleanup-beta4-bridge.sh" <<'CLEANUP'
#!/usr/bin/env bash
set -Eeuo pipefail
STATUS_FILE="${SG_AWG_PANEL_UPDATE_STATUS:-/var/www/sg-awg-update/status.json}"
SYSTEMD_DIR="${SG_AWG_SYSTEMD_DIR:-/etc/systemd/system}"
SELF="${SG_AWG_LEGACY_CLEANUP_SCRIPT:-/usr/local/lib/sg-awg-panel/cleanup-beta4-bridge.sh}"
[[ -f "$STATUS_FILE" ]] || exit 0
read -r STATE VERSION < <(python3 - "$STATUS_FILE" <<'PY'
import json,sys
try:
    data=json.load(open(sys.argv[1],encoding='utf-8'))
except Exception:
    print('', '')
else:
    print(data.get('state',''), data.get('version',''))
PY
)
case "$STATE:$VERSION" in
  success:v210|success:v208|success:v207|success:v206|success:v205|success:v204|success:v203|success:v202|success:v201|success:v0.1.0-rc4|success:v0.1.0-rc5-hotfix1|success:v0.1.0-rc5-hotfix2|success:v0.1.0-rc5-hotfix3)
    systemctl disable --now \
      sg-awg-routing-schedule.timer sg-awg-routing-lists.timer \
      sg-awg-routing-schedule.service sg-awg-routing-lists.service \
      sg-awg-routing.service 2>/dev/null || true
    rm -f \
      "$SYSTEMD_DIR/sg-awg-routing.service" \
      "$SYSTEMD_DIR/sg-awg-routing-schedule.service" \
      "$SYSTEMD_DIR/sg-awg-routing-schedule.timer" \
      "$SYSTEMD_DIR/sg-awg-routing-lists.service" \
      "$SYSTEMD_DIR/sg-awg-routing-lists.timer"
    systemctl enable --now sg-awg-traffic-schedule.timer >/dev/null 2>&1 || true
    systemctl restart sg-awg-traffic.service
    systemctl is-active --quiet sg-awg-traffic.service
    ;;
  rolled_back:*|rollback:*)
    systemctl disable --now \
      sg-awg-traffic-schedule.timer sg-awg-traffic-schedule.service \
      sg-awg-traffic.service 2>/dev/null || true
    rm -f \
      "$SYSTEMD_DIR/sg-awg-traffic.service" \
      "$SYSTEMD_DIR/sg-awg-traffic-schedule.service" \
      "$SYSTEMD_DIR/sg-awg-traffic-schedule.timer"
    ;;
  *)
    exit 0
    ;;
esac
systemctl disable --now sg-awg-legacy-upgrade-cleanup.timer 2>/dev/null || true
rm -f \
  "$SYSTEMD_DIR/sg-awg-legacy-upgrade-cleanup.service" \
  "$SYSTEMD_DIR/sg-awg-legacy-upgrade-cleanup.timer"
systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
rm -f "$SELF"
CLEANUP
chmod 0755 "$CLEANUP_DIR/cleanup-beta4-bridge.sh"

cat > "$SYSTEMD_DIR/sg-awg-legacy-upgrade-cleanup.service" <<'UNIT'
[Unit]
Description=Remove temporary Beta 4 update bridge
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/lib/sg-awg-panel/cleanup-beta4-bridge.sh
UNIT

cat > "$SYSTEMD_DIR/sg-awg-legacy-upgrade-cleanup.timer" <<'UNIT'
[Unit]
Description=Check whether the Beta 4 to RC Final update has finished

[Timer]
OnActiveSec=3min
OnUnitActiveSec=60s
Unit=sg-awg-legacy-upgrade-cleanup.service

[Install]
WantedBy=timers.target
UNIT

chmod 0644 \
  "$SYSTEMD_DIR/sg-awg-routing-schedule.service" \
  "$SYSTEMD_DIR/sg-awg-routing-schedule.timer" \
  "$SYSTEMD_DIR/sg-awg-routing-lists.service" \
  "$SYSTEMD_DIR/sg-awg-routing-lists.timer" \
  "$SYSTEMD_DIR/sg-awg-legacy-upgrade-cleanup.service" \
  "$SYSTEMD_DIR/sg-awg-legacy-upgrade-cleanup.timer"
systemctl daemon-reload
systemctl enable --now sg-awg-legacy-upgrade-cleanup.timer >/dev/null
