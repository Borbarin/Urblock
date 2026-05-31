#!/usr/bin/env bash
# Агент блокировки: камера сразу при Win+L (user systemd).
set -euo pipefail

URBLOCK_PO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_SRC="$URBLOCK_PO_ROOT/scripts/urblock-lock-agent"
UNIT_NAME="urblock-lock-agent.service"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$USER_UNIT_DIR/$UNIT_NAME"

if [[ ! -f "$AGENT_SRC" ]]; then
  echo "Не найден $AGENT_SRC" >&2
  exit 1
fi

chmod +x "$AGENT_SRC"

mkdir -p "$USER_UNIT_DIR"
cat >"$UNIT_PATH" <<EOF
[Unit]
Description=Urblock — проверка лица при блокировке экрана
After=graphical-session.target

[Service]
Type=simple
Environment=URBLOCK_PO_ROOT=$URBLOCK_PO_ROOT
Environment=URBLOCK_OVERLAY_MODE=tk
ExecStart=$AGENT_SRC
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"

echo "Установлено: $UNIT_PATH"
echo "Статус:  systemctl --user status $UNIT_NAME"
echo "Лог:     journalctl --user -u $UNIT_NAME -f"
echo "         tail -f /var/log/urblock-verify.log"
