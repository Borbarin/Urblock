#!/usr/bin/env bash
# Включить биометрию на экране входа GDM + агент greeter (после disable-login.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Автовход GDM: GDM D-Bus (UserVerifier) + запасной ydotool:" >&2
echo "  sudo apt install gir1.2-gdm-1.0 ydotool xdotool" >&2
sudo "$ROOT/scripts/install-login.sh"
sudo "$ROOT/scripts/install-greeter-agent.sh"
if command -v ydotoold >/dev/null 2>&1; then
  sudo "$ROOT/scripts/install-ydotoold-system.sh"
fi
