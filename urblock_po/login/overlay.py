"""Графический индикатор биометрии на экране GDM и блокировки (не терминал)."""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from display_env import (
    greeter_session_user,
    overlay_run_user,
    prefer_notify_overlay,
    resolve_display_env,
    resolve_greeter_overlay_env,
)

_SESSION_ENV_KEYS = (
    "DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "WAYLAND_DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
)

_OVERLAY_OPEN_TIMEOUT = 6.0
_LOG_PATH = Path("/var/log/urblock-overlay.log")
_STATUS_DIR = Path("/var/run/urblock-verify")

_NOTIFY_TEXT = {
    "starting": "Urblock: подготовка камеры…",
    "camera_ok": "Urblock: камера включена",
    "scanning": "Urblock: ищем лицо…",
    "no_face": "Urblock: лицо не видно",
    "no_match": "Urblock: лицо не распознано",
    "success": "Urblock: лицо распознано",
    "failed": "Urblock: не удалось войти",
    "stop": "",
}

# (заголовок, текст) для экрана входа GDM
_GREETER_NOTIFY: dict[str, tuple[str, str]] = {
    "starting": ("Urblock", "Включаем камеру…"),
    "camera_ok": ("Urblock", "Идёт распознавание лица…"),
    "scanning": ("Urblock", "Идёт распознавание лица…"),
    "no_face": ("Urblock", "Повернитесь к камере"),
    "no_match": ("Не распознано", "Лицо не совпало с эталоном. Введите пароль."),
    "success": ("Успешно", "Лицо распознано. Нажмите Enter для входа."),
    "failed": ("Не распознано", "Вход по лицу не выполнен. Введите пароль."),
    "stop": ("", ""),
}


def _log(msg: str) -> None:
    line = f"{msg}\n"
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _display_env(username: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    session = resolve_display_env(username)
    env.update(session)
    return env


def _python_has_tkinter(python: str) -> bool:
    if not Path(python).is_file():
        return False
    try:
        proc = subprocess.run(
            [python, "-c", "import tkinter"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _is_greeter() -> bool:
    return os.environ.get("URBLOCK_GREETER") == "1"


def greeter_push_notify(username: str, status: str) -> None:
    """Сразу показать уведомление на экране GDM (до открытия камеры)."""
    prev = os.environ.get("URBLOCK_GREETER")
    os.environ["URBLOCK_GREETER"] = "1"
    os.environ.setdefault("URBLOCK_OVERLAY_MODE", "notify")
    try:
        LoginOverlay(username=username)._push_notify_only(status)
    finally:
        if prev is None:
            os.environ.pop("URBLOCK_GREETER", None)
        else:
            os.environ["URBLOCK_GREETER"] = prev


def _tk_display_works(env: dict[str, str], run_as: str | None) -> bool:
    ui_py = _ui_python()
    if not _python_has_tkinter(ui_py):
        return False
    env_list = [f"{k}={env[k]}" for k in _SESSION_ENV_KEYS if env.get(k)]
    cmd = [ui_py, "-c", "import tkinter; r=tkinter.Tk(); r.withdraw(); r.destroy()"]
    if run_as and shutil.which("runuser"):
        cmd = ["runuser", "-u", run_as, "--", "env", *env_list, *cmd]
    elif env_list:
        cmd = ["env", *env_list, *cmd]
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            timeout=6,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _ui_python() -> str:
    candidates: list[str] = []
    for key in ("URBLOCK_OVERLAY_PYTHON", "URBLOCK_PYTHON"):
        if os.environ.get(key):
            candidates.append(os.environ[key])
    candidates.extend(["/usr/bin/python3", "/bin/python3", sys.executable])
    seen: set[str] = set()
    for py in candidates:
        if py in seen:
            continue
        seen.add(py)
        if _python_has_tkinter(py):
            return py
    return "/usr/bin/python3"


class LoginOverlay:
    def __init__(self, username: str | None = None) -> None:
        self._username = username or os.environ.get("PAM_USER")
        self._proc: subprocess.Popen[bytes] | None = None
        self._pipe_path: Path | None = None
        self._pipe_fd: int | None = None
        self._status_file: Path | None = None
        self._mode = "off"
        self._run_env: dict[str, str] = {}
        self._run_as: str | None = None
        self._last_notify_status = ""
        self._notify_method_logged = ""

    def _safe_name(self) -> str:
        return "".join(
            c if c.isalnum() or c in "._-" else "_" for c in (self._username or "overlay")
        )

    def _write_status_file(self, status: str) -> None:
        if not self._status_file:
            return
        try:
            self._status_file.parent.mkdir(parents=True, exist_ok=True)
            self._status_file.write_text(status + "\n", encoding="utf-8")
        except OSError:
            pass

    def _session_env(self) -> dict[str, str]:
        env = dict(self._run_env)
        if self._username:
            env.update(resolve_display_env(self._username))
        return env

    def _wrap_user_cmd(self, cmd: list[str], env: dict[str, str]) -> list[str]:
        env_list = [f"{k}={env[k]}" for k in _SESSION_ENV_KEYS if env.get(k)]
        run_as = self._run_as
        if run_as and shutil.which("runuser"):
            return ["runuser", "-u", run_as, "--", "env", *env_list, *cmd]
        if env_list:
            return ["env", *env_list, *cmd]
        return cmd

    def _run_user_cmd(self, cmd: list[str], env: dict[str, str], *, timeout: float = 4) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                self._wrap_user_cmd(cmd, env),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log(f"overlay: cmd error {exc!r} cmd={cmd[:2]!r}")
            return None

    def _write_user_runtime_status(self, text: str) -> None:
        if not self._username:
            return
        try:
            uid = pwd.getpwnam(self._username).pw_uid
            path = Path(f"/run/user/{uid}/urblock-lock-status.txt")
            path.write_text(text + "\n", encoding="utf-8")
            path.chmod(0o644)
        except OSError:
            pass

    def _notify_gdbus(self, title: str, body: str, env: dict[str, str]) -> bool:
        if not shutil.which("gdbus") or not env.get("DBUS_SESSION_BUS_ADDRESS"):
            return False
        timeout_ms = 15000 if _is_greeter() else 4000
        if _is_greeter() and title.startswith("Успешно"):
            hints = "{'urgency': <byte 1>, 'transient': <false>}"
            timeout_ms = 20000
        elif _is_greeter() and title.startswith("Не распознано"):
            hints = "{'urgency': <byte 2>, 'transient': <false>}"
            timeout_ms = 20000
        elif _is_greeter():
            hints = "{'urgency': <byte 1>, 'transient': <false>, 'resident': <true>}"
        else:
            hints = "{}"
        proc = self._run_user_cmd(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.Notifications",
                "--object-path",
                "/org/freedesktop/Notifications",
                "--method",
                "org.freedesktop.Notifications.Notify",
                "Urblock",
                "4242",
                "camera-web",
                title,
                body,
                "[]",
                hints,
                str(timeout_ms),
            ],
            env,
        )
        if proc is None:
            return False
        if proc.returncode == 0:
            return True
        err = (proc.stderr or proc.stdout or "").strip()
        if err and err != self._notify_method_logged:
            self._notify_method_logged = err
            _log(f"overlay: gdbus failed: {err[:120]}")
        return False

    def _notify_send(self, title: str, body: str, env: dict[str, str]) -> bool:
        if not shutil.which("notify-send"):
            return False
        proc = self._run_user_cmd(
            ["notify-send", "-a", "Urblock", "-t", "4000", title, body],
            env,
        )
        if proc is None:
            return False
        if proc.returncode == 0:
            return True
        err = (proc.stderr or "").strip()
        if err and err != self._notify_method_logged:
            self._notify_method_logged = err
            _log(f"overlay: notify-send: {err[:120]}")
        return False

    def _push_notify_only(self, status: str) -> None:
        if _is_greeter():
            env = resolve_greeter_overlay_env(self._username)
            self._run_as = greeter_session_user() or overlay_run_user(self._username)
        else:
            env = _display_env(self._username)
            self._run_as = overlay_run_user(self._username)
        self._run_env = env
        self._mode = "notify"
        self._notify(status, force=True)

    def _notify(self, status: str, *, force: bool = False) -> None:
        if _is_greeter() and status.startswith("match_progress:"):
            return
        if not force and status == self._last_notify_status:
            return
        self._last_notify_status = status

        if _is_greeter():
            pair = _GREETER_NOTIFY.get(status)
            if not pair or not pair[0]:
                return
            title, body = pair
            text = f"{title}: {body}"
        else:
            text = _NOTIFY_TEXT.get(status, "")
            if status.startswith("match_progress:"):
                try:
                    percent = float(status.split(":", 1)[1])
                    text = f"Urblock: сравнение {percent:.0f}%"
                except ValueError:
                    text = "Urblock: сравнение лица…"
            if not text:
                return
            title, _, body = text.partition(": ")
            if not body:
                title, body = "Urblock", text

        self._write_user_runtime_status(text)
        env = self._session_env()

        if self._notify_gdbus(title, body, env):
            return
        if self._notify_send(title, body, env):
            return
        if self._notify_method_logged != "lock-screen-blocked":
            self._notify_method_logged = "lock-screen-blocked"
            _log(
                "overlay: уведомления на экране блокировки недоступны "
                f"(см. /run/user/*/urblock-lock-status.txt); DBUS={env.get('DBUS_SESSION_BUS_ADDRESS', '-')}"
            )

    def start(self) -> bool:
        if _is_greeter():
            env = resolve_greeter_overlay_env(self._username)
            self._run_as = greeter_session_user() or overlay_run_user(self._username)
        else:
            env = _display_env(self._username)
            self._run_as = overlay_run_user(self._username)

        if "WAYLAND_DISPLAY" not in env and "DISPLAY" not in env:
            _log(f"overlay: no session display for user={self._username!r} greeter={_is_greeter()}")
            if _is_greeter() and env.get("DBUS_SESSION_BUS_ADDRESS"):
                self._run_env = env
                self._mode = "notify"
                self._send("starting")
                return True
            return False

        self._run_env = env
        _STATUS_DIR.mkdir(parents=True, exist_ok=True)
        safe = self._safe_name()
        self._status_file = _STATUS_DIR / f"{safe}.overlay-status"

        xauth = env.get("XAUTHORITY", "")
        ui_py = _ui_python()
        has_tk = _python_has_tkinter(ui_py)
        tk_ok = has_tk and _tk_display_works(env, self._run_as)

        if _is_greeter():
            _log(
                f"overlay: greeter → уведомления "
                f"(run_as={self._run_as} WAYLAND={env.get('WAYLAND_DISPLAY', '-')})"
            )
            self._mode = "notify"
            return True

        if prefer_notify_overlay(env, has_tkinter=has_tk) or not tk_ok:
            if not has_tk:
                reason = "нет python3-tk"
            elif env.get("WAYLAND_DISPLAY"):
                reason = "Wayland"
            else:
                reason = "режим notify"
            _log(
                f"overlay: notify mode ({reason}) "
                f"WAYLAND={env.get('WAYLAND_DISPLAY', '-')} DISPLAY={env.get('DISPLAY', '-')}"
            )
            self._mode = "notify"
            self._send("starting")
            return True

        self._mode = "tk"
        ui_script = Path(__file__).with_name("overlay_ui.py")
        pipe_path = str(_STATUS_DIR / f"{safe}.fifo")
        try:
            if Path(pipe_path).exists():
                Path(pipe_path).unlink()
        except OSError:
            pass
        os.mkfifo(pipe_path, mode=0o666)
        self._pipe_path = Path(pipe_path)

        env_list: list[str] = []
        for key in (
            "DISPLAY",
            "XAUTHORITY",
            "XDG_RUNTIME_DIR",
            "WAYLAND_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
        ):
            val = env.get(key, "")
            if val:
                env_list.append(f"{key}={val}")

        ui_cmd = [
            ui_py,
            str(ui_script),
            "--pipe",
            pipe_path,
            "--status-file",
            str(self._status_file),
        ]
        if _is_greeter():
            ui_cmd.append("--greeter")
        if self._run_as and shutil.which("runuser"):
            ui_cmd = ["runuser", "-u", self._run_as, "--", "env", *env_list, *ui_cmd]
        elif env_list:
            ui_cmd = ["env", *env_list, *ui_cmd]

        _log(
            f"overlay: tk run_as={self._run_as} python={ui_py} "
            f"DISPLAY={env.get('DISPLAY')} XAUTH={xauth[:60] if xauth else '(none)'}"
        )

        try:
            self._proc = subprocess.Popen(
                ui_cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            if self._proc.stderr:
                threading.Thread(
                    target=self._drain_stderr,
                    args=(self._proc.stderr,),
                    daemon=True,
                ).start()

            opened = threading.Event()
            open_err: list[OSError] = []

            def _open_pipe() -> None:
                try:
                    self._pipe_fd = os.open(pipe_path, os.O_WRONLY)
                    opened.set()
                except OSError as exc:
                    open_err.append(exc)

            threading.Thread(target=_open_pipe, daemon=True).start()
            if not opened.wait(_OVERLAY_OPEN_TIMEOUT):
                _log("overlay: fifo timeout — fallback notify")
                self.stop()
                self._mode = "notify"
                self._send("starting")
                return True
            if open_err:
                _log(f"overlay: fifo error {open_err[0]!r} — fallback notify")
                self.stop()
                self._mode = "notify"
                self._send("starting")
                return True
            self._send("starting")
            return True
        except OSError as exc:
            _log(f"overlay: tk start failed {exc!r} — notify")
            self._mode = "notify"
            self._send("starting")
            return True

    @staticmethod
    def _drain_stderr(pipe) -> None:
        try:
            for line in pipe:
                _log(f"overlay-ui: {line.decode(errors='replace').rstrip()}")
        except OSError:
            pass

    def _send(self, status: str) -> None:
        self._write_status_file(status)
        if self._mode == "notify":
            if status != "stop":
                self._notify(status)
            return
        if self._pipe_fd is None:
            return
        try:
            os.write(self._pipe_fd, f"{status}\n".encode())
        except OSError:
            pass

    def set_starting(self) -> None:
        self._send("starting")

    def set_camera_ok(self) -> None:
        self._send("camera_ok")

    def set_scanning(self) -> None:
        self._send("scanning")

    def set_success(self) -> None:
        self._send("success")

    def set_failed(self) -> None:
        self._send("failed")

    def set_no_face(self) -> None:
        self._send("no_face")

    def set_no_match(self) -> None:
        self._send("no_match")

    def set_match_progress(self, percent: float) -> None:
        self._send(f"match_progress:{percent:.0f}")

    def stop(self) -> None:
        if self._mode == "notify":
            self._write_status_file("")
            return
        self._send("stop")
        if self._pipe_fd is not None:
            try:
                os.close(self._pipe_fd)
            except OSError:
                pass
            self._pipe_fd = None
        if self._pipe_path is not None:
            try:
                self._pipe_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._pipe_path = None
        if self._proc is not None:
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
