#!/usr/bin/env bash
# Установка биометрического входа через PAM (Ubuntu/GDM).
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Запустите с sudo: sudo $0" >&2
  exit 1
fi

URBLOCK_PO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URBLOCK_GUI_ROOT="$(cd "$URBLOCK_PO_ROOT/../urblock_gui" && pwd)"
URBLOCK_DATA_DIR="$URBLOCK_GUI_ROOT/data"
PYTHON="$URBLOCK_GUI_ROOT/.venv/bin/python"
PAM_SNIPPET_SRC="$URBLOCK_PO_ROOT/pam/urblock-auth.conf"
PAM_SNIPPET_DST="/etc/pam.d/urblock-auth.conf"
PAM_MARK="# urblock auth stack"

INSTALL_BIN="/usr/local/bin/urblock-verify"
CONFIG_DIR="/etc/urblock"
CONFIG_FILE="$CONFIG_DIR/install.conf"

if [[ ! -d "$URBLOCK_GUI_ROOT" ]]; then
  echo "Не найден urblock_gui: $URBLOCK_GUI_ROOT" >&2
  exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "Не найден Python: $PYTHON" >&2
  exit 1
fi

mkdir -p "$CONFIG_DIR"
cat >"$CONFIG_FILE" <<EOF
URBLOCK_PO_ROOT="$URBLOCK_PO_ROOT"
URBLOCK_GUI_ROOT="$URBLOCK_GUI_ROOT"
URBLOCK_DATA_DIR="$URBLOCK_DATA_DIR"
URBLOCK_PYTHON="$PYTHON"
EOF
chmod 0644 "$CONFIG_FILE"

install -m 0644 "$PAM_SNIPPET_SRC" "$PAM_SNIPPET_DST"

cat >"$INSTALL_BIN" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
source /etc/urblock/install.conf
export URBLOCK_PO_ROOT URBLOCK_GUI_ROOT URBLOCK_DATA_DIR
export URBLOCK_OVERLAY_MODE="${URBLOCK_OVERLAY_MODE:-tk}"

export DISPLAY="${DISPLAY:-:0}"
if [[ -n "${PAM_USER:-}" ]]; then
  _uid="$(id -u "$PAM_USER" 2>/dev/null || true)"
  if [[ -n "$_uid" && -d "/run/user/$_uid" ]]; then
    export XDG_RUNTIME_DIR="/run/user/$_uid"
    if [[ -S "/run/user/$_uid/bus" ]]; then
      export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$_uid/bus"
    fi
    for _wl in /run/user/"$_uid"/wayland-*; do
      if [[ -S "$_wl" ]]; then
        export WAYLAND_DISPLAY="${_wl##*/}"
        break
      fi
    done
    _mutter=(/run/user/"$_uid"/.mutter-Xwaylandauth*)
    if [[ -f "${_mutter[0]:-}" ]]; then
      export XAUTHORITY="${_mutter[0]}"
    elif [[ -f "/run/user/$_uid/.Xauthority" ]]; then
      export XAUTHORITY="/run/user/$_uid/.Xauthority"
    fi
  fi
fi
if [[ -z "${XAUTHORITY:-}" ]]; then
  for _xauth in /var/lib/gdm/:0.Xauth /run/user/42/gdm/Xauthority; do
    if [[ -f "$_xauth" ]]; then
      export XAUTHORITY="$_xauth"
      break
    fi
  done
fi

{
  echo "$(date -Iseconds) wrapper start PAM_USER=${PAM_USER:-} uid=$(id -u)"
} >>/var/log/urblock-verify.log 2>/dev/null || true

exec "$URBLOCK_PYTHON" "$URBLOCK_PO_ROOT/login/verify.py" "$@"
WRAPPER
chmod 0755 "$INSTALL_BIN"

_patch_pam_file() {
  local pam_file="$1"
  [[ -f "$pam_file" ]] || return 0

  cp "$pam_file" "${pam_file}.bak.$(date +%Y%m%d%H%M%S)"
  sed -i '/urblock auth stack/d' "$pam_file"
  sed -i '/@include urblock-auth.conf/d' "$pam_file"
  sed -i '/urblock biometric/d' "$pam_file"
  sed -i '/pam_exec.so.*urblock-verify/d' "$pam_file"
  sed -i '/@include common-auth/d' "$pam_file"

  if grep -qF "$PAM_MARK" "$pam_file"; then
    echo "PAM: $pam_file уже настроен"
    return 0
  fi

  if grep -q 'pam_succeed_if.so' "$pam_file"; then
    sed -i "/^auth.*pam_succeed_if.so/a @include urblock-auth.conf $PAM_MARK" "$pam_file"
  else
    sed -i "1a @include urblock-auth.conf $PAM_MARK" "$pam_file"
  fi
  echo "PAM: $pam_file — вход и экран блокировки (лицо + пароль)"
}

# gdm-password: вход GDM и разблокировка экрана в Ubuntu/GNOME
_patch_pam_file "/etc/pam.d/gdm-password"
if ! grep -q 'urblock-auth.conf' /etc/pam.d/gdm-password 2>/dev/null; then
  echo "ОШИБКА: в /etc/pam.d/gdm-password нет urblock-auth — вход по лицу/паролю через Urblock не подключён." >&2
  exit 1
fi

# Убрать устаревшую одну строку pam_exec из login (без preflight)
for _old in /etc/pam.d/login; do
  [[ -f "$_old" ]] || continue
  if grep -q 'pam_exec.so.*urblock-verify' "$_old" && ! grep -q 'urblock-auth.conf' "$_old"; then
    cp "$_old" "${_old}.bak.urblock-clean.$(date +%Y%m%d%H%M%S)"
    sed -i '/pam_exec.so.*urblock-verify/d' "$_old"
    echo "PAM: $_old — удалена старая строка urblock (используйте gdm-password)"
  fi
done
mkdir -p /var/run/urblock-verify
chmod 0755 /var/run/urblock-verify 2>/dev/null || true
rm -f /var/run/urblock-verify.lock 2>/dev/null || true

touch /var/log/urblock-verify.log /var/log/urblock-overlay.log
chmod 0666 /var/log/urblock-verify.log /var/log/urblock-overlay.log 2>/dev/null || true
chown root:root /var/log/urblock-verify.log /var/log/urblock-overlay.log 2>/dev/null || true

if getent group video >/dev/null; then
  for dm_user in gdm sddm lightdm; do
    if id "$dm_user" &>/dev/null; then
      usermod -aG video "$dm_user" 2>/dev/null && echo "video: $dm_user" || true
    fi
  done
fi

if ! /usr/bin/python3 -c "import tkinter" 2>/dev/null; then
  echo ""
  echo "  ВНИМАНИЕ: для графического индикатора на экране входа/блокировки нужен tkinter:"
  echo "    sudo apt install python3-tk"
  echo "  Без него биометрия всё равно работает, но без окна статуса на экране."
fi

if ! /usr/bin/python3 -c "import gi; gi.require_version('Gdm','1.0'); from gi.repository import Gdm" 2>/dev/null; then
  echo ""
  echo "  ВНИМАНИЕ: для автовхода GDM без ручного Enter нужен GDM D-Bus:"
  echo "    sudo apt install gir1.2-gdm-1.0"
  echo "  Иначе останется только запасной вариант через ydotool."
fi

echo ""
echo "Готово."
echo "  Камера включается при выборе пользователя; справа — статус распознавания."
echo "  Вход по лицу или паролю (можно одновременно)."
echo "  В GUI включите «автономную детекцию» (auto_detect_enabled)."
echo "  Отключить: sudo ./scripts/disable-login.sh"
echo "  Логи: tail -f /var/log/urblock-verify.log /var/log/urblock-overlay.log"
echo ""
echo "  Камера при Win+L (блокировка):"
echo "    ./scripts/install-lock-agent.sh"
echo "  Камера после разлогина (экран входа GDM):"
echo "    sudo ./scripts/install-greeter-agent.sh"
