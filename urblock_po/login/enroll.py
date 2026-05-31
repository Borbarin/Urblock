#!/usr/bin/env python3
"""Регистрация биометрии для входа в систему (из активной сессии пользователя)."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _bootstrap_gui_path() -> None:
    env_root = os.environ.get("URBLOCK_GUI_ROOT")
    if env_root:
        root = Path(env_root)
    else:
        po_root = Path(__file__).resolve().parents[1]
        root = po_root.parent / "urblock_gui"
    sys.path.insert(0, str(root))


def _crop_face_rgb(frame_bgr, face):
    import cv2
    import numpy as np

    h, w = frame_bgr.shape[:2]
    pad = int(0.12 * max(face.w, face.h))
    x1 = max(0, face.x - pad)
    y1 = max(0, face.y - pad)
    x2 = min(w, face.x + face.w + pad)
    y2 = min(h, face.y + face.h + pad)
    if x2 <= x1 or y2 <= y1:
        return None
    crop_bgr = frame_bgr[y1:y2, x1:x2]
    if crop_bgr.size == 0:
        return None
    return cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)


def enroll(user: str | None, samples: int) -> int:
    import cv2

    from auth.user import get_current_user_id
    from config import FRAME_HEIGHT, FRAME_WIDTH
    from storage import load_settings, register_biometric_embedding, save_biometric, user_biometrics_dir
    from vision.face_detector import YuNetDetector
    from vision.face_matcher import FaceMatcher

    uid = user or get_current_user_id()
    os.environ["URBLOCK_USER"] = uid

    settings = load_settings()
    camera_index = int(settings.get("detect_camera_index", settings.get("preview_camera_index", 0)))

    detector = YuNetDetector()
    matcher = FaceMatcher()
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"Не удалось открыть камеру #{camera_index}", file=sys.stderr)
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    saved = 0
    print(f"Регистрация биометрии для «{uid}» ({samples} снимков). Смотрите в камеру…")

    try:
        while saved < samples:
            ok, frame_bgr = cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            faces = detector.detect(frame_bgr)
            face = YuNetDetector.largest(faces)
            if face is None or face.score < 0.6:
                continue
            crop_rgb = _crop_face_rgb(frame_bgr, face)
            if crop_rgb is None:
                continue
            bio_id = save_biometric(crop_rgb, face.landmarks, face.score, user_id=uid)
            if register_biometric_embedding(bio_id, matcher, reload=False):
                saved += 1
                print(f"  [{saved}/{samples}] сохранено {bio_id[:8]}…")
                time.sleep(2.0)
        matcher.reload_gallery()
    finally:
        if cap.isOpened():
            cap.release()
        print("Камера отключена.")

    bio_dir = user_biometrics_dir(uid)
    print(f"Готово: {saved} образцов в {bio_dir}")
    return 0 if saved else 1


def main() -> int:
    _bootstrap_gui_path()
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="логин (по умолчанию текущий)")
    parser.add_argument("--samples", type=int, default=3, help="число снимков")
    args = parser.parse_args()
    return enroll(args.user, args.samples)


if __name__ == "__main__":
    raise SystemExit(main())
