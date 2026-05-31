#!/usr/bin/env bash
# Срочно: убрать зависшие verify, восстановить PAM, поставить исправленный стек.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Запустите: sudo $0" >&2
  exit 1
fi

URBLOCK_PO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "1. Останавливаем зависшие процессы urblock…"
pkill -f "urblock-verify|login/verify.py" 2>/dev/null || true
rm -f /var/run/urblock-verify.lock
rm -rf /var/run/urblock-verify/*.json /var/run/urblock-verify/*.tmp 2>/dev/null || true

echo "2. Переустанавливаем PAM и wrapper (исправленные коды возврата)…"
"$URBLOCK_PO_ROOT/scripts/install-login.sh"

echo "3. Агент экрана входа GDM (разлогин)…"
"$URBLOCK_PO_ROOT/scripts/install-greeter-agent.sh"

echo ""
echo "Готово. Проверка gdm-password:"
grep -E 'common-auth|urblock' /etc/pam.d/gdm-password || true
echo ""
echo "Войдите паролем или лицом. Лог: tail -f /var/log/urblock-verify.log"
