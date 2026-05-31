#!/usr/bin/env bash
# ydotoold от root — для автовхода GDM (urblock-verify работает не в user-сессии).
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Запустите: sudo $0" >&2
  exit 1
fi

if [[ ! -x /usr/bin/ydotoold ]]; then
  echo "Сначала: sudo apt install ydotool" >&2
  exit 1
fi

UNIT="/etc/systemd/system/urblock-ydotoold.service"
cat >"$UNIT" <<'EOF'
[Unit]
Description=ydotoold for Urblock GDM face login
After=multi-user.target

[Service]
Type=simple
RuntimeDirectory=ydotool
RuntimeDirectoryMode=0755
Environment=XDG_RUNTIME_DIR=/run/ydotool
Environment=YDOTOOL_SOCKET=/run/ydotool/ydotool.sock
ExecStart=/usr/bin/ydotoold -p /run/ydotool/ydotool.sock
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl restart urblock-ydotoold.service 2>/dev/null || true
systemctl enable --now urblock-ydotoold.service
echo "Установлено: $UNIT"
systemctl status urblock-ydotoold.service --no-pager || true
echo ""
echo "Проверка:"
echo "  systemctl is-active urblock-ydotoold"
echo "  ls -la /run/ydotool/ydotool.sock"
echo "Затем: sudo systemctl restart urblock-greeter-agent.service"
