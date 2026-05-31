#!/usr/bin/env bash
# Срочно вернуть обычный вход по паролю (без биометрии).
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Запустите: sudo $0" >&2
  exit 1
fi

LOCK="/var/run/urblock-verify.lock"
rm -f "$LOCK" 2>/dev/null || true
pkill -f "urblock-verify|login/verify.py|greeter_agent|lock_agent" 2>/dev/null || true
systemctl stop urblock-greeter-agent.service 2>/dev/null || true
systemctl disable urblock-greeter-agent.service 2>/dev/null || true
rm -rf /var/run/urblock-verify/*.fifo /var/run/urblock-verify/*.json 2>/dev/null || true

_patch_file() {
  local pam_file="$1"
  [[ -f "$pam_file" ]] || return 0
  cp "$pam_file" "${pam_file}.bak.disable.$(date +%Y%m%d%H%M%S)"
  sed -i '/urblock auth stack/d' "$pam_file"
  sed -i '/@include urblock-auth.conf/d' "$pam_file"
  sed -i '/urblock biometric/d' "$pam_file"
  sed -i '/pam_exec.so.*urblock-verify/d' "$pam_file"
  if [[ "$pam_file" == *gdm-password* ]]; then
    if ! grep -q '@include common-auth' "$pam_file"; then
      sed -i '/^auth.*pam_succeed_if.so/a @include common-auth' "$pam_file"
      echo "  $pam_file: восстановлен @include common-auth"
    fi
  fi
  echo "  очищен: $pam_file"
}

_patch_file "/etc/pam.d/gdm-password"
_patch_file "/etc/pam.d/login"

echo ""
echo "Биометрия отключена. Вход только по паролю."
echo "Проверка:"
grep -E 'common-auth|urblock' /etc/pam.d/gdm-password /etc/pam.d/login 2>/dev/null || true
