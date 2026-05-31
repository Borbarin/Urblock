#!/usr/bin/env bash
# Восстановить PAM GDM + ydotoold + перезапуск агентов.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec sudo "$ROOT/scripts/install-login.sh" \
  && sudo "$ROOT/scripts/install-ydotoold-system.sh" \
  && sudo systemctl restart urblock-greeter-agent.service
