"""На GDM после лица автоматически нажимаем Enter (как вы вручную)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_LOG = Path("/var/log/urblock-verify.log")
_LOGIN_BUSY = Path("/var/run/urblock-greeter-login")
_LAST_OK_LOGIN: dict[str, float] = {}
_COOLDOWN_SEC = 3.0
_TOOL_DIRS = ("/usr/local/bin", "/usr/bin", "/bin", "/sbin")
_ENTER_SEQUENCES = (
    ["key", "56:1", "56:0", "28:1", "28:0"],
    ["key", "28:1", "28:0"],
)
_TAB_ENTER_SEQUENCES = (
    ["key", "15:1", "15:0", "28:1", "28:0"],
    ["key", "28:1", "28:0"],
)


def gdm_login_in_progress() -> bool:
    return _LOGIN_BUSY.is_file()


def _mark_login_busy(username: str) -> None:
    try:
        _LOGIN_BUSY.parent.mkdir(parents=True, exist_ok=True)
        _LOGIN_BUSY.write_text(username, encoding="utf-8")
    except OSError:
        pass


def _clear_login_busy() -> None:
    try:
        _LOGIN_BUSY.unlink(missing_ok=True)
    except OSError:
        pass


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _tool(name: str) -> str | None:
    for d in _TOOL_DIRS:
        p = Path(d) / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which(name)


def _ydotool_socket() -> str | None:
    for path in (
        Path("/tmp/.ydotool_socket"),
        Path("/run/ydotool/ydotool.sock"),
        Path("/run/ydotool/.ydotool_socket"),
    ):
        if path.exists():
            return str(path)
    return None


def _ydotoold_running() -> bool:
    if not shutil.which("systemctl"):
        return _ydotool_socket() is not None
    for unit in ("urblock-ydotoold", "ydotoold"):
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            timeout=2,
            check=False,
        )
        if r.stdout.strip() == b"active":
            return True
    return _ydotool_socket() is not None


def _run(cmd: list[str], env: dict[str, str] | None = None, *, timeout: float = 3.0):
    base = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root"}
    if env:
        base.update(env)
    try:
        return subprocess.run(
            cmd, env=base, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"gdm-login: {exc!r}")
        return None


def _run_greeter(run_as: str, cmd: list[str], env: dict[str, str], *, timeout: float = 3.0):
    keys = ("DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS")
    env_list = [f"{k}={env[k]}" for k in keys if env.get(k)]
    env_list.append("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if os.geteuid() == 0 and run_as and shutil.which("runuser"):
        full = ["runuser", "-u", run_as, "--", "env", *env_list, *cmd]
    else:
        full = ["env", *env_list, *cmd]
    merged = {"HOME": "/root", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    merged.update(env)
    try:
        return subprocess.run(
            full, env=merged, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"gdm-login: {exc!r}")
        return None


def _greeter_user() -> str | None:
    from display_env import greeter_session_user

    u = greeter_session_user()
    if u:
        return u
    for name in ("gdm-greeter", "Debian-gdm", "gdm"):
        try:
            import pwd

            pwd.getpwnam(name)
            return name
        except KeyError:
            continue
    return None


def _ydotool_keys(seq: list[str], *, label: str) -> bool:
    if not _ydotoold_running():
        return False
    ydotool = _tool("ydotool")
    sock = _ydotool_socket()
    if not ydotool or not sock:
        return False
    env = {"YDOTOOL_SOCKET": sock}
    proc = _run([ydotool, *seq], env)
    if proc is not None and proc.returncode == 0:
        _log(f"gdm-login: {label} (ydotool)")
        return True
    return False


def _press_enter_ydotool() -> bool:
    for seq in _ENTER_SEQUENCES:
        if _ydotool_keys(seq, label="Enter"):
            return True
    return False


def _press_enter_ydotool_as_greeter(run_as: str, username: str) -> bool:
    ydotool = _tool("ydotool")
    sock = _ydotool_socket()
    if not ydotool or not sock or not run_as:
        return False
    from display_env import resolve_greeter_overlay_env

    xenv = resolve_greeter_overlay_env(username)
    xenv["YDOTOOL_SOCKET"] = sock
    for seq in _ENTER_SEQUENCES:
        proc = _run_greeter(run_as, [ydotool, *seq], xenv)
        if proc is not None and proc.returncode == 0:
            _log(f"gdm-login: Enter (ydotool as {run_as})")
            return True
    return False


def _press_enter_xdotool(run_as: str, username: str) -> bool:
    xdotool = _tool("xdotool")
    if not xdotool:
        return False
    from display_env import resolve_greeter_overlay_env

    base = resolve_greeter_overlay_env(username)
    live = {}
    if run_as and shutil.which("runuser"):
        try:
            p = subprocess.run(
                ["runuser", "-u", run_as, "--", "printenv", "DISPLAY", "XAUTHORITY"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            for line in p.stdout.splitlines():
                k, _, v = line.partition("=")
                if v:
                    live[k] = v
        except (OSError, subprocess.TimeoutExpired):
            pass
    xenv = {**base, **live}
    disp = xenv.get("DISPLAY")
    if not disp:
        for d in (":1024", ":0", ":1"):
            if Path(f"/tmp/.X11-unix/X{d[1:].split('.')[0]}").exists():
                disp = d
                break
    if not disp:
        return False
    xenv["DISPLAY"] = disp
    click = _run_greeter(
        run_as,
        [xdotool, "mousemove", "--sync", "960", "540", "click", "1"],
        xenv,
    )
    if click is not None and click.returncode == 0:
        time.sleep(0.15)
    proc = _run_greeter(run_as, [xdotool, "key", "--clearmodifiers", "Return"], xenv)
    if proc is not None and proc.returncode == 0:
        _log(f"gdm-login: Enter (xdotool {disp})")
        return True
    return False


def _graphical_user_session_active(username: str) -> bool:
    """Активная пользовательская сессия на seat (не manager «-»)."""
    try:
        listed = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if listed.returncode != 0:
        return False
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[2] != username or parts[5] != "user":
            continue
        seat = parts[3]
        if seat in ("", "-"):
            continue
        show = subprocess.run(
            ["loginctl", "show-session", parts[0], "-p", "State", "-p", "Active"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if show.returncode != 0:
            continue
        state = active = ""
        for row in show.stdout.splitlines():
            if row.startswith("State="):
                state = row.split("=", 1)[1]
            elif row.startswith("Active="):
                active = row.split("=", 1)[1]
        if state == "active" and active == "yes":
            return True
    return False


def _pam_face_accepted(username: str) -> bool:
    """PAM принял лицо: VerifySession с result=ok исчезла во время gdm-login."""
    try:
        login_dir = Path(__file__).resolve().parent
        if str(login_dir) not in sys.path:
            sys.path.insert(0, str(login_dir))
        from verify_session import VerifySession

        sess = VerifySession.load(username)
    except Exception:
        return False
    if sess and sess.result == "ok":
        return False
    if gdm_login_in_progress():
        return True
    return sess is None or sess.result != "ok"


def _seat0_active_username() -> str | None:
    """Пользователь на активной сессии seat0 (не фоновая сессия в loginctl)."""
    if not shutil.which("loginctl"):
        return None
    try:
        seat = subprocess.run(
            ["loginctl", "show-seat", "seat0", "-p", "ActiveSession", "--value"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if seat.returncode != 0:
        return None
    sid = seat.stdout.strip()
    if not sid or sid == "0":
        return None
    try:
        show = subprocess.run(
            [
                "loginctl",
                "show-session",
                sid,
                "-p",
                "Name",
                "-p",
                "Class",
                "-p",
                "State",
                "-p",
                "Active",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if show.returncode != 0:
        return None
    props: dict[str, str] = {}
    for line in show.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    if props.get("Class") != "user":
        return None
    if props.get("State") != "active" or props.get("Active") != "yes":
        return None
    return props.get("Name") or None


def login_confirmed_for_user(username: str) -> bool:
    """Настоящий вход: PAM принял лицо и seat0 переключился на пользователя."""
    return _login_done(username)


def _login_done(username: str) -> bool:
    from display_env import greeter_screen_active

    if greeter_screen_active():
        return False
    if not _pam_face_accepted(username):
        return False
    return _seat0_active_username() == username


def _gdm_dbus_eligible(username: str) -> bool:
    """
    D-Bus reauth работает при смене пользователя (сессия уже есть).
    На чистом экране входа GDM отвечает AccessDenied — только Enter/xdotool.
    """
    return _graphical_user_session_active(username)


def _gdm_dbus_verify(username: str, *, run_as: str) -> bool:
    """GDM PAM через UserVerifier (от имени gdm-greeter, не root)."""
    if not _gdm_dbus_eligible(username):
        _log("gdm-dbus: пропуск (нет фоновой сессии — чистый экран входа)")
        return False

    script = Path(__file__).with_name("gdm_dbus_submit.py")
    py = _tool("python3") or shutil.which("python3")
    if not py or not script.is_file():
        _log("gdm-login: gdm_dbus_submit.py / python3 not found")
        return False

    from display_env import resolve_greeter_overlay_env

    home = "/tmp"
    try:
        import pwd

        home = pwd.getpwnam(run_as).pw_dir
    except KeyError:
        pass
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": home,
    }
    env.update(resolve_greeter_overlay_env(username))
    env_list = [f"{k}={v}" for k, v in env.items() if v]

    cmd: list[str]
    if os.geteuid() == 0 and run_as and shutil.which("runuser"):
        cmd = ["runuser", "-u", run_as, "--", "env", *env_list, py, str(script), username]
    else:
        cmd = ["env", *env_list, py, str(script), username]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=32, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"gdm-login: gdm-dbus subprocess {exc!r}")
        return False
    if proc.returncode == 0:
        _log("gdm-login: вход через GDM D-Bus (UserVerifier)")
        return True
    err = (proc.stderr or proc.stdout or "").strip()
    if err:
        _log(f"gdm-login: gdm-dbus: {err[:220]}")
    return False


def _focus_greeter_vt() -> None:
    """Переключить VT на greeter (чтобы xdotool/ydotool попали в GDM)."""
    if not shutil.which("loginctl") or not shutil.which("chvt"):
        return
    try:
        listed = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if listed.returncode != 0:
        return
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[5] != "greeter":
            continue
        show = subprocess.run(
            ["loginctl", "show-session", parts[0], "-p", "TTY", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if show.returncode != 0:
            continue
        tty = show.stdout.strip()
        if tty.startswith("tty") and tty[3:].isdigit():
            vt = tty[3:]
            subprocess.run(["chvt", vt], timeout=2, check=False)
            _log(f"gdm-login: chvt {vt} (greeter)")
            time.sleep(0.25)
            return


def _submit_gdm_keys(run_as: str, username: str) -> bool:
    """Enter на экран GDM: xdotool :1024 (XWayland greeter), затем ydotool."""
    _focus_greeter_vt()

    if _press_enter_xdotool(run_as, username):
        time.sleep(0.75)
        _press_enter_xdotool(run_as, username)
        time.sleep(0.75)
        _press_enter_xdotool(run_as, username)
        return True

    if _press_enter_ydotool():
        time.sleep(0.65)
        _press_enter_ydotool()
        time.sleep(0.65)
        _press_enter_ydotool()
        return True

    if _press_enter_ydotool_as_greeter(run_as, username):
        time.sleep(0.65)
        _press_enter_ydotool()
        return True

    for seq in _TAB_ENTER_SEQUENCES:
        if _ydotool_keys(seq, label="Tab+Enter"):
            time.sleep(0.5)
            _ydotool_keys(["key", "28:1", "28:0"], label="Enter")
            return True
    return False


def greeter_submit_login(username: str) -> bool:
    """
    Лицо совпало → несколько раз жмём Enter за пользователя.
    Возвращает True, если вход подтвердился (greeter ушёл, сессия на seat).
    """
    from display_env import greeter_screen_active

    if not greeter_screen_active():
        return False

    now = time.monotonic()
    if now - _LAST_OK_LOGIN.get(username, 0.0) < _COOLDOWN_SEC:
        return False

    run_as = _greeter_user() or "gdm-greeter"
    _mark_login_busy(username)
    try:
        for attempt in range(1, 4):
            if not greeter_screen_active():
                if _login_done(username):
                    _log(f"gdm-login: вход выполнен для {username}")
                    _LAST_OK_LOGIN[username] = time.monotonic()
                    return True
                if _pam_face_accepted(username):
                    for _ in range(24):
                        time.sleep(0.5)
                        if _login_done(username):
                            _log(f"gdm-login: вход выполнен для {username} (после PAM)")
                            _LAST_OK_LOGIN[username] = time.monotonic()
                            return True
                _log("gdm-login: экран входа не активен — отмена")
                return False
            if attempt > 1:
                _log(f"gdm-login: повтор {attempt}/3")
                time.sleep(1.0)

            if not greeter_screen_active():
                return False

            sent = _submit_gdm_keys(run_as, username)
            if not sent:
                _log("gdm-login: не удалось отправить Enter (ydotool/xdotool)")
                continue
            for tick in range(24):
                time.sleep(0.5)
                if _login_done(username):
                    _log(f"gdm-login: вход выполнен для {username} (Enter)")
                    _LAST_OK_LOGIN[username] = time.monotonic()
                    return True
                if tick == 11 and greeter_screen_active():
                    if not _pam_face_accepted(username):
                        _log("gdm-login: Enter отправлен, ждём PAM (auth ok ещё нет)")
                    else:
                        _log("gdm-login: Enter отправлен, экран входа ещё активен")

            if not greeter_screen_active():
                continue

            if _gdm_dbus_verify(username, run_as=run_as):
                for _ in range(16):
                    time.sleep(0.5)
                    if _login_done(username):
                        _log(f"gdm-login: вход выполнен для {username} (D-Bus)")
                        _LAST_OK_LOGIN[username] = time.monotonic()
                        return True

        _log("gdm-login: автовход не подтверждён — нажмите Enter вручную")
        return False
    finally:
        _clear_login_busy()
