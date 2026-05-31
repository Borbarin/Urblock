#!/usr/bin/env bash
# Агент экрана входа GDM: камера после разлогина (системный systemd).
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Запустите: sudo $0" >&2
  exit 1
fi

URBLOCK_PO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_SRC="$URBLOCK_PO_ROOT/scripts/urblock-greeter-agent"
UNIT_PATH="/etc/systemd/system/urblock-greeter-agent.service"

if [[ ! -f "$AGENT_SRC" ]]; then
  echo "Не найден $AGENT_SRC" >&2
  exit 1
fi

chmod +x "$AGENT_SRC"

cat >"$UNIT_PATH" <<EOF
[Unit]
Description=Urblock — распознавание лица на экране входа GDM
After=gdm.service dbus.service
Wants=gdm.service

[Service]
Type=simple
Environment=URBLOCK_PO_ROOT=$URBLOCK_PO_ROOT
Environment=URBLOCK_OVERLAY_MODE=tk
ExecStart=$AGENT_SRC
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now urblock-greeter-agent.service

echo "Установлено: $UNIT_PATH"
echo "Статус:  systemctl status urblock-greeter-agent"
echo "Лог:     journalctl -u urblock-greeter-agent -f"
echo "         tail -f /var/log/urblock-verify.log"
