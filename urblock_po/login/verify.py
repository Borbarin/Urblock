#!/usr/bin/env python3
"""Проверка лица и пароля при входе (PAM pam_exec, expose_authtok)."""

from __future__ import annotations

import argparse
import atexit
import fcntl
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

LOG_PATH = Path("/var/log/urblock-verify.log")

LOGIN_FRAME_WIDTH = 480
LOGIN_FRAME_HEIGHT = 360
LOGIN_DETECT_MAX_WIDTH = 320
LOGIN_WARMUP_FRAMES = 2
LOGIN_GREETER_WARMUP_FRAMES = 0
LOGIN_DEFAULT_TIMEOUT = 4.0
LOGIN_HARD_MAX_TIMEOUT = 5.0
LOGIN_DAEMON_TIMEOUT = 45.0
LOGIN_FRAME_INTERVAL = 0.04
LOGIN_CAMERA_OPEN_TIMEOUT = 4.0
LOGIN_STALE_LOCK_SEC = 45.0
def _lock_path() -> Path:
    """PAM (root) — /var/run; ручной тест от пользователя — runtime или /tmp."""
    if os.geteuid() == 0:
        return Path("/var/run/urblock-verify.lock")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "urblock-verify.lock"
    return Path("/tmp") / f"urblock-verify-{os.getuid()}.lock"

_LOGIN_DIR = Path(__file__).resolve().parent
if str(_LOGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_LOGIN_DIR))

from camera_session import CameraSession, close_active  # noqa: E402
from overlay import LoginOverlay  # noqa: E402
from password_check import verify_password  # noqa: E402
from gdm_login import greeter_submit_login  # noqa: E402
from session_unlock import unlock_user_session  # noqa: E402
from verify_session import VerifySession  # noqa: E402

# Коды выхода = коды возврата PAM (pam_exec передаёт exit status напрямую).
PAM_SUCCESS = 0
PAM_AUTH_ERR = 7
PAM_IGNORE = 25  # не 2: exit 2 = PAM_SYMBOL_ERR и ломает стек аутентификации
STATUS_THROTTLE_SEC = 0.75

_verified = False
_lock_fd: int | None = None


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, file=sys.stderr)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    sys.stderr.flush()


def _bootstrap_gui_path() -> Path:
    env_root = os.environ.get("URBLOCK_GUI_ROOT")
    if env_root:
        root = Path(env_root)
    else:
        po_root = Path(__file__).resolve().parents[1]
        root = po_root.parent / "urblock_gui"
    if not root.is_dir():
        _log(f"urblock-verify: urblock_gui not found: {root}")
        sys.exit(1)
    path = str(root)
    if path not in sys.path:
        sys.path.insert(0, path)
    return root


def _autonomous_enabled() -> bool:
    from storage import load_settings

    settings = load_settings()
    return bool(settings.get("auto_detect_enabled", False))


def _greeter_auth_context(user: str) -> bool:
    """Экран входа GDM (в т.ч. смена пользователя), не Win+L."""
    svc = os.environ.get("PAM_SERVICE", "")
    if svc in ("gdm-password", "gdm-autologin", "gdm-launch-environment"):
        return True
    from display_env import greeter_screen_active

    return greeter_screen_active()


def _auto_unlock_enabled() -> bool:
    if os.environ.get("URBLOCK_AUTO_UNLOCK", "1") == "0":
        return False
    from storage import load_settings

    settings = load_settings()
    if "auto_unlock_on_face" in settings:
        return bool(settings["auto_unlock_on_face"])
    return _autonomous_enabled()


def _auto_greeter_login_enabled() -> bool:
    if os.environ.get("URBLOCK_AUTO_GREETER_LOGIN", "1") == "0":
        return False
    from storage import load_settings

    settings = load_settings()
    if "auto_login_on_face_gdm" in settings:
        return bool(settings["auto_login_on_face_gdm"])
    return _auto_unlock_enabled()


def _pam_password() -> str | None:
    """Пароль с экрана входа (pam_exec expose_authtok → PAM_AUTHTOK)."""
    if "PAM_AUTHTOK" not in os.environ:
        return None
    return os.environ.get("PAM_AUTHTOK", "")


def _target_user(explicit: str | None) -> str:
    user = explicit or os.environ.get("PAM_USER") or os.environ.get("URBLOCK_USER")
    if not user:
        _log("urblock-verify: PAM_USER is not set")
        sys.exit(1)
    os.environ["URBLOCK_USER"] = user
    return user


def _prepare_gallery(matcher, user: str) -> int:
    from storage import ensure_gallery_embeddings, user_has_gallery

    if not user_has_gallery(user):
        return 0

    built, count = ensure_gallery_embeddings(matcher, user_id=user)
    if built:
        _log(f"built {built} embedding file(s) → {count} templates")
    return count


def _break_stale_lock(lock_path: Path) -> None:
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return
    if age < LOGIN_STALE_LOCK_SEC:
        return
    try:
        lock_path.unlink()
        _log(f"removed stale lock ({age:.0f}s)")
    except OSError:
        pass


def _acquire_verify_lock() -> bool:
    global _lock_fd
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _break_stale_lock(lock_path)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        _log("another verify already running — skip")
        return False
    _lock_fd = fd

    def _release() -> None:
        global _lock_fd
        if _lock_fd is None:
            return
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None

    atexit.register(_release)
    return True


def _install_signal_handlers() -> None:
    def _shutdown(signum, _frame) -> None:
        _log(f"signal {signum}: releasing camera")
        close_active(_log)
        raise SystemExit(0 if _verified else 1)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, _shutdown)
        except (OSError, ValueError):
            pass

    atexit.register(lambda: close_active(_log))


def _update_overlay_status(
    overlay: LoginOverlay,
    session: VerifySession | None,
    status: str,
    *,
    last_sent: list[float],
) -> None:
    now = time.monotonic()
    if status in ("no_face", "no_match") and last_sent:
        if now - last_sent[0] < STATUS_THROTTLE_SEC:
            return
        last_sent[0] = now
    if status == "no_face":
        overlay.set_no_face()
    elif status == "no_match":
        overlay.set_no_match()
    elif status == "scanning":
        overlay.set_scanning()
    if session is not None:
        session.set_status(status)


def _run_face_loop(
    *,
    matcher,
    user: str,
    threshold: float,
    timeout_sec: float,
    interval_sec: float,
    preferred: int,
    overlay: LoginOverlay,
    stop: threading.Event,
    outcome: dict[str, object],
    session: VerifySession | None = None,
) -> None:
    import cv2

    if stop.is_set():
        return

    camera = CameraSession(_log)
    opened = threading.Event()

    def _open_cam() -> None:
        if camera.open(cv2, preferred, LOGIN_FRAME_WIDTH, LOGIN_FRAME_HEIGHT):
            opened.set()

    threading.Thread(target=_open_cam, daemon=True).start()
    if not opened.wait(LOGIN_CAMERA_OPEN_TIMEOUT):
        _log(f"camera open timeout ({LOGIN_CAMERA_OPEN_TIMEOUT}s)")
        camera.close()
        return
    try:

        warmup = (
            LOGIN_GREETER_WARMUP_FRAMES
            if os.environ.get("URBLOCK_GREETER") == "1"
            else LOGIN_WARMUP_FRAMES
        )
        for _ in range(warmup):
            if stop.is_set():
                return
            camera.read()

        overlay.set_camera_ok()
        if session is not None:
            session.set_status("camera_ok")
        _log(f"face thread camera={camera.index}")

        deadline = time.monotonic() + timeout_sec
        frames_checked = 0
        last_status = [0.0]

        while not stop.is_set() and time.monotonic() < deadline:
            ok, frame_bgr = camera.read()
            if not ok or frame_bgr is None:
                time.sleep(interval_sec)
                continue

            frames_checked += 1
            faces, match = matcher.match_frame(
                frame_bgr,
                threshold=threshold,
                detect_max_width=LOGIN_DETECT_MAX_WIDTH,
            )
            if match is not None and match.is_match:
                outcome["ok"] = True
                outcome["via"] = "face"
                outcome["score"] = match.score
                outcome["frames"] = frames_checked
                stop.set()
                if session is not None:
                    session.set_result("ok")
                    session.set_status("success")
                    session.save()

                if _greeter_auth_context(user):
                    overlay.set_success()
                    overlay._notify("success", force=True)
                    _log(
                        f"face ok score={match.score:.3f} — "
                        "ожидание Enter (GDM)"
                    )
                    return

                unlocked = _auto_unlock_enabled() and unlock_user_session(user)
                overlay.set_success()
                overlay._notify("success", force=True)
                if unlocked:
                    outcome["unlocked"] = True
                    _log(f"face ok score={match.score:.3f} — разблокировка без Enter")
                else:
                    _log(
                        f"face ok score={match.score:.3f} — лицо совпало, "
                        "нажмите Enter (разблокировка не сработала)"
                    )
                return

            if not faces:
                _update_overlay_status(overlay, session, "no_face", last_sent=last_status)
            elif match is not None:
                overlay.set_match_progress(match.score * 100)
                _update_overlay_status(overlay, session, "no_match", last_sent=last_status)
            else:
                _update_overlay_status(overlay, session, "scanning", last_sent=last_status)

            if interval_sec > 0:
                time.sleep(interval_sec)

        outcome["face_frames"] = frames_checked
        if session is not None and not outcome.get("ok"):
            session.set_result("fail")
    finally:
        camera.close()


def _face_ready(user: str) -> tuple[bool, object | None, int]:
    from config import FACE_MODEL_PATH, MATCH_THRESHOLD_DEFAULT, SFACE_MODEL_PATH
    from vision.face_matcher import FaceMatcher

    if not FACE_MODEL_PATH.is_file() or not SFACE_MODEL_PATH.is_file():
        _log(f"ONNX models missing under {FACE_MODEL_PATH.parent}")
        return False, None, 0
    matcher = FaceMatcher()
    count = _prepare_gallery(matcher, user)
    if count == 0:
        _log(f"gallery empty for {user}")
        return False, None, 0
    _log(f"gallery ready: {count} templates")
    return True, matcher, count


def preflight_login(user: str) -> int:
    """Запускает фоновую проверку лица до ввода пароля (PAM optional → PAM_IGNORE)."""
    if not _autonomous_enabled():
        _log("preflight: face disabled in settings")
        return PAM_IGNORE

    ready, _, _ = _face_ready(user)
    if not ready:
        return PAM_IGNORE

    from daemon_launcher import verify_lock_held

    existing = VerifySession.load(user)
    if existing:
        if existing.result == "ok" and _greeter_auth_context(user):
            _log("preflight: face already verified — keep session for Enter (GDM)")
            return PAM_IGNORE
        if existing.result == "ok":
            _log("preflight: clearing stale face ok (lock/unlock)")
            VerifySession.clear(user)
            existing = None
        if existing is not None and existing.is_process_alive():
            _log(f"preflight: daemon already running pid={existing.pid}")
            return PAM_IGNORE
        if existing is not None:
            _log(f"preflight: stale daemon pid={existing.pid}, restarting")
            existing.terminate()
            VerifySession.clear(user)

    if verify_lock_held():
        _log("preflight: verify lock held — skip duplicate daemon")
        return PAM_IGNORE

    from display_env import daemon_child_env

    script = Path(__file__).resolve()
    env = daemon_child_env(user)
    env["URBLOCK_PREFLIGHT"] = "1"
    if env.get("URBLOCK_GREETER") == "1" and not VerifySession.load(user):
        try:
            from overlay import greeter_push_notify

            greeter_push_notify(user, "scanning")
        except Exception:
            pass
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--daemon", "--user", user],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        _log(f"preflight: cannot spawn daemon: {exc!r}")
        return PAM_IGNORE

    VerifySession(user=user, pid=proc.pid).save()
    _log(f"preflight: daemon pid={proc.pid}")
    return PAM_IGNORE


def verify_daemon(
    user: str,
    *,
    timeout_sec: float = LOGIN_HARD_MAX_TIMEOUT,
    interval_sec: float = LOGIN_FRAME_INTERVAL,
) -> int:
    """Фоновая проверка лица (между preflight и нажатием Enter)."""
    import cv2

    from config import MATCH_THRESHOLD_DEFAULT
    from storage import load_settings

    from display_env import daemon_child_env

    cv2.setNumThreads(2)
    _install_signal_handlers()
    if not _acquire_verify_lock():
        return 1

    for key, val in daemon_child_env(user).items():
        if val:
            os.environ[key] = str(val)

    settings = load_settings()
    preferred = int(settings.get("detect_camera_index", settings.get("preview_camera_index", 0)))
    threshold = float(settings.get("match_threshold", MATCH_THRESHOLD_DEFAULT))
    timeout_sec = min(
        float(settings.get("login_daemon_timeout", LOGIN_DAEMON_TIMEOUT)),
        60.0,
    )
    use_overlay = os.environ.get("URBLOCK_LOGIN_OVERLAY", "1") != "0"

    ready, matcher, _ = _face_ready(user)
    if not ready or matcher is None:
        return 1

    session = VerifySession.load(user) or VerifySession(user=user, pid=os.getpid())
    session.pid = os.getpid()
    session.save()

    outcome: dict[str, object] = {"ok": False}
    stop = threading.Event()
    overlay = LoginOverlay(username=user)
    overlay_started = use_overlay and overlay.start()
    if overlay_started:
        _log("overlay started (daemon)")

    face_thread = threading.Thread(
        target=_run_face_loop,
        kwargs={
            "matcher": matcher,
            "user": user,
            "threshold": threshold,
            "timeout_sec": timeout_sec,
            "interval_sec": interval_sec,
            "preferred": preferred,
            "overlay": overlay,
            "stop": stop,
            "outcome": outcome,
            "session": session,
        },
        name="urblock-face-daemon",
        daemon=True,
    )
    face_thread.start()

    try:
        face_thread.join(timeout=timeout_sec + 2.0)
    finally:
        stop.set()
        face_thread.join(timeout=2.0)

    if overlay_started:
        if outcome.get("ok"):
            overlay._notify("success", force=True)
            time.sleep(0.35)
        elif os.environ.get("URBLOCK_GREETER") == "1":
            overlay._notify("failed", force=True)
            time.sleep(0.6)
        overlay.stop()
        if outcome.get("ok") and _greeter_auth_context(user):
            time.sleep(0.45)

    if outcome.get("ok"):
        if outcome.get("unlocked"):
            VerifySession.clear(user)
            _log(f"daemon: face ok, session unlocked (user={user})")
            return 0
        session.set_result("ok")
        session.set_status("success")
        session.pid = 0
        session.save()
        if _greeter_auth_context(user):
            if _auto_greeter_login_enabled():
                if greeter_submit_login(user):
                    outcome["gdm_login"] = True
            _log(f"daemon: face ok — Enter для входа (user={user})")
        else:
            _log(f"daemon: face ok — нажмите Enter (user={user})")
        return 0

    VerifySession.clear(user)
    _log(f"daemon: face not matched in {timeout_sec:.0f}s")
    return 1


def _wait_for_daemon_result(
    user: str,
    *,
    timeout_sec: float,
    stop: threading.Event,
) -> bool:
    deadline = time.monotonic() + timeout_sec
    while not stop.is_set() and time.monotonic() < deadline:
        session = VerifySession.load(user)
        if session and session.result == "ok":
            return True
        if session and not session.is_process_alive() and session.result == "ok":
            return True
        time.sleep(0.05)
    return False


def verify_login(
    user: str,
    *,
    password: str | None = None,
    timeout_sec: float = LOGIN_DEFAULT_TIMEOUT,
    interval_sec: float = LOGIN_FRAME_INTERVAL,
    face_enabled: bool = True,
) -> int:
    from config import MATCH_THRESHOLD_DEFAULT
    from storage import load_settings

    _install_signal_handlers()

    session = VerifySession.load(user)
    _log(
        f"verify_login user={user} PAM_SERVICE={os.environ.get('PAM_SERVICE', '-')} "
        f"session_ok={bool(session and session.result == 'ok')} greeter={_greeter_auth_context(user)}"
    )

    has_password = password is not None and password != ""
    # До lock: демон verify --daemon держит flock, но лицо уже в сессии — PAM должен принять.
    if session and session.result == "ok" and _greeter_auth_context(user):
        _log("auth ok via=face (preflight daemon, GDM)")
        VerifySession.clear(user)
        if session.is_process_alive():
            session.terminate()
        return 0

    daemon_running = bool(session and session.is_process_alive())
    if not daemon_running and not _acquire_verify_lock():
        _log("verify lock busy — skip (PAM_IGNORE)")
        return PAM_IGNORE

    settings = load_settings()
    preferred = int(settings.get("detect_camera_index", settings.get("preview_camera_index", 0)))
    threshold = float(settings.get("match_threshold", MATCH_THRESHOLD_DEFAULT))
    timeout_sec = min(
        float(settings.get("login_verify_timeout", timeout_sec)),
        LOGIN_HARD_MAX_TIMEOUT,
    )
    use_overlay = os.environ.get("URBLOCK_LOGIN_OVERLAY", "1") != "0"
    if session and session.result == "ok":
        _log("verify: clearing stale face ok (lock/unlock)")
        VerifySession.clear(user)
        session = None
        daemon_running = False
    if _greeter_auth_context(user) and not daemon_running and not has_password:
        _log("verify: greeter — нет daemon/пароля, не блокируем PAM")
        return PAM_IGNORE
    if not face_enabled and not has_password:
        _log("no password and face disabled — skip")
        return PAM_IGNORE

    t0 = time.monotonic()
    _log(
        f"verify start user={user} parallel password={has_password} face={face_enabled} "
        f"timeout={timeout_sec}s"
    )

    outcome: dict[str, object] = {"ok": False}
    stop = threading.Event()

    def password_worker() -> None:
        if not has_password or stop.is_set():
            return
        if verify_password(user, password or ""):
            outcome["ok"] = True
            outcome["via"] = "password"
            stop.set()
            _log("password ok")
        else:
            outcome["password_bad"] = True
            _log("password mismatch (face still allowed)")

    pw_thread = threading.Thread(target=password_worker, name="urblock-password", daemon=True)
    pw_thread.start()

    face_thread: threading.Thread | None = None
    overlay = LoginOverlay(username=user)
    overlay_started = False

    def daemon_watcher() -> None:
        if not daemon_running or stop.is_set():
            return
        if _wait_for_daemon_result(user, timeout_sec=timeout_sec + 1.0, stop=stop):
            outcome["ok"] = True
            outcome["via"] = "face"
            stop.set()
            _log("face ok (preflight daemon)")

    daemon_thread: threading.Thread | None = None
    if daemon_running:
        _log(f"attach to preflight daemon pid={session.pid}")
        daemon_thread = threading.Thread(target=daemon_watcher, name="urblock-daemon-wait", daemon=True)
        daemon_thread.start()
    elif face_enabled:
        overlay_started = use_overlay and overlay.start()
        if overlay_started:
            _log("overlay started")
        ready, matcher, _ = _face_ready(user)
        if ready and matcher is not None:
            face_thread = threading.Thread(
                target=_run_face_loop,
                kwargs={
                    "matcher": matcher,
                    "user": user,
                    "threshold": threshold,
                    "timeout_sec": timeout_sec,
                    "interval_sec": interval_sec,
                    "preferred": preferred,
                    "overlay": overlay,
                    "stop": stop,
                    "outcome": outcome,
                },
                name="urblock-face",
                daemon=True,
            )
            face_thread.start()

    try:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline and not stop.is_set():
            if outcome.get("password_bad") and not face_enabled and not daemon_running:
                break
            time.sleep(0.03)
    finally:
        stop.set()
        if session and session.is_process_alive():
            session.terminate()
        VerifySession.clear(user)
        if face_thread is not None:
            face_thread.join(timeout=2.0)
        if daemon_thread is not None:
            daemon_thread.join(timeout=1.0)
        pw_thread.join(timeout=1.0)

    global _verified
    elapsed = time.monotonic() - t0

    if outcome.get("ok"):
        _verified = True
        via = outcome.get("via", "?")
        extra = ""
        if via == "face":
            extra = f" score={outcome.get('score', 0):.3f} frames={outcome.get('frames', 0)}"
        _log(f"auth ok via={via}{extra} elapsed={elapsed:.2f}s")
        if overlay_started:
            time.sleep(0.2)
            overlay.stop()
        return 0

    _log(f"auth failed ({elapsed:.1f}s) — PAM_IGNORE (пароль проверяет pam_unix)")
    if overlay_started:
        overlay.set_failed()
        overlay.stop()
    # Никогда не возвращаем 1: optional-модуль не должен ломать вход по паролю.
    return PAM_IGNORE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Urblock login (password + face in parallel)")
    parser.add_argument("--user", help="OS username (default: PAM_USER)")
    parser.add_argument("--password", help="Password (default: PAM_AUTHTOK from pam_exec)")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="PAM: start background face scan before password prompt",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=LOGIN_DEFAULT_TIMEOUT,
        help="Max seconds for parallel auth",
    )
    parser.add_argument(
        "--face-only",
        action="store_true",
        help="Skip password check (manual test)",
    )
    args = parser.parse_args(argv)

    try:
        _bootstrap_gui_path()
        user = _target_user(args.user)
        if args.preflight:
            return preflight_login(user)
        if args.daemon:
            return verify_daemon(user, timeout_sec=args.timeout)
        face_on = _autonomous_enabled()
        if args.face_only:
            face_on = True
        password = args.password if args.password is not None else _pam_password()
        return verify_login(
            user,
            password=password,
            timeout_sec=args.timeout,
            face_enabled=face_on,
        )
    except Exception as exc:
        import traceback

        _log(f"fatal: {exc!r} — PAM_IGNORE (не блокируем пароль)")
        if os.environ.get("URBLOCK_VERIFY_DEBUG"):
            traceback.print_exc(file=sys.stderr)
        return PAM_IGNORE


if __name__ == "__main__":
    raise SystemExit(main())
