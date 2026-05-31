import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import numpy as np

from auth.user import get_current_user_id
from config import (
    BIOMETRICS_DIR,
    DATA_DIR,
    DEFAULT_SETTINGS,
    EMBEDDINGS_DIR,
    EMBEDDING_VAULT_SUFFIX,
    FACES_FILE,
    FACES_VAULT_FILE,
    SETTINGS_FILE,
    SNAPSHOTS_DIR,
    SNAPSHOT_VAULT_SUFFIX,
    USERS_DIR,
    VAULT_SUFFIX,
)
from security.vault import (
    decrypt_bytes,
    decrypt_record,
    encrypt_bytes,
    encrypt_record,
    pack_biometric_payload,
    unpack_image_jpeg,
)


@dataclass(frozen=True)
class GalleryImage:
    entry_id: str
    name: str
    source: str
    image_path: Path | None = None
    vault_path: Path | None = None


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def user_data_dir(user_id: str | None = None) -> Path:
    uid = user_id or get_current_user_id()
    path = USERS_DIR / uid
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_biometrics_dir(user_id: str | None = None) -> Path:
    path = user_data_dir(user_id) / "biometrics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_embeddings_dir(user_id: str | None = None) -> Path:
    path = user_data_dir(user_id) / "embeddings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_snapshots_dir(user_id: str | None = None) -> Path:
    path = user_data_dir(user_id) / "snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _faces_vault_path(user_id: str | None = None) -> Path:
    return user_data_dir(user_id) / FACES_VAULT_FILE


def _vault_user(user_id: str | None = None) -> tuple[str, Path]:
    uid = user_id or get_current_user_id()
    return uid, user_data_dir(uid)


def _secure_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def snapshot_path(face_id: str, user_id: str | None = None) -> Path:
    return user_snapshots_dir(user_id) / f"{face_id}{SNAPSHOT_VAULT_SUFFIX}"


def _vault_path(bio_id: str, user_id: str | None = None) -> Path:
    return user_biometrics_dir(user_id) / f"{bio_id}{VAULT_SUFFIX}"


def load_json(path: Path, default: Any) -> Any:
    _ensure_data_dir()
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data: Any) -> None:
    _ensure_data_dir()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_settings() -> dict:
    stored = load_json(SETTINGS_FILE, {})
    settings = DEFAULT_SETTINGS.copy()
    settings.update(stored)
    if "preview_camera_index" not in stored and "camera_index" in stored:
        settings["preview_camera_index"] = int(stored["camera_index"])
    if "detect_camera_index" not in stored:
        idx = settings.get("preview_camera_index", 0)
        settings["detect_camera_index"] = int(stored.get("camera_index", idx))
    return settings


def save_settings(settings: dict) -> None:
    save_json(SETTINGS_FILE, settings)


def patch_settings(updates: dict) -> dict:
    settings = load_settings()
    settings.update(updates)
    save_settings(settings)
    return settings


def load_faces(user_id: str | None = None) -> list[dict]:
    uid, user_dir = _vault_user(user_id)
    vault_path = _faces_vault_path(uid)
    if vault_path.is_file():
        try:
            raw = decrypt_bytes(uid, user_dir, vault_path.read_bytes())
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, list):
                return data
        except (ValueError, OSError, json.JSONDecodeError):
            return []
    if FACES_FILE.is_file():
        legacy = load_json(FACES_FILE, [])
        if isinstance(legacy, list) and legacy:
            save_faces(legacy)
        return legacy if isinstance(legacy, list) else []
    return []


def save_faces(faces: list[dict]) -> None:
    uid, user_dir = _vault_user()
    vault_path = _faces_vault_path(uid)
    plaintext = json.dumps(faces, ensure_ascii=False, indent=2).encode("utf-8")
    vault_path.write_bytes(encrypt_bytes(uid, user_dir, plaintext))
    _secure_file(vault_path)
    if FACES_FILE.is_file():
        try:
            FACES_FILE.unlink()
        except OSError:
            pass


def delete_face_entry(face_id: str) -> None:
    faces = [f for f in load_faces() if f.get("id") != face_id]
    save_faces(faces)
    uid = get_current_user_id()
    for path in (
        snapshot_path(face_id, uid),
        SNAPSHOTS_DIR / f"{face_id}.jpg",
        user_snapshots_dir(uid) / f"{face_id}.jpg",
    ):
        if path.exists():
            path.unlink()
    delete_embedding(face_id)


def _load_biometric_record(bio_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    uid = user_id or get_current_user_id()
    path = _vault_path(bio_id, uid)
    if not path.is_file():
        return None
    try:
        record = decrypt_record(uid, user_data_dir(uid), path.read_bytes())
    except (ValueError, OSError):
        return None
    except Exception:
        # Повреждённый vault после ручного удаления или смены .vault_salt
        return None
    if record.get("user_id") != uid:
        return None
    return record


def load_biometric_image_bgr(bio_id: str, user_id: str | None = None) -> Any | None:
    """Декодирует JPEG лица из зашифрованной записи (BGR, OpenCV)."""
    import cv2

    record = _load_biometric_record(bio_id, user_id)
    if record is None:
        return None
    try:
        jpeg = unpack_image_jpeg(record)
    except (ValueError, OSError):
        return None
    arr = np.frombuffer(jpeg, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def user_has_gallery(user_id: str) -> bool:
    """Есть ли у пользователя записи для входа по лицу (GUI или PO)."""
    user_dir = USERS_DIR / user_id
    if not user_dir.is_dir():
        return False
    bio_dir = user_dir / "biometrics"
    if bio_dir.is_dir() and any(bio_dir.glob(f"*{VAULT_SUFFIX}")):
        return True
    snap_dir = user_dir / "snapshots"
    if snap_dir.is_dir() and any(snap_dir.glob(f"*{SNAPSHOT_VAULT_SUFFIX}")):
        return True
    return False


def iter_gallery_images(user_id: str | None = None) -> Iterator[GalleryImage]:
    """Все изображения для галереи сравнения: лица и биометрия пользователя."""
    uid = user_id or get_current_user_id()
    for face in load_faces(uid):
        face_id = face.get("id")
        if not face_id:
            continue
        enc = snapshot_path(face_id, uid)
        legacy = SNAPSHOTS_DIR / f"{face_id}.jpg"
        if enc.is_file() or legacy.is_file():
            yield GalleryImage(
                entry_id=face_id,
                name=str(face.get("name", "Без имени")),
                source="face",
                image_path=enc if enc.is_file() else legacy,
            )

    bio_dir = user_biometrics_dir(uid)
    for vault_path in sorted(bio_dir.glob(f"*{VAULT_SUFFIX}")):
        bio_id = vault_path.name[: -len(VAULT_SUFFIX)]
        # Имя без расшифровки vault — иначе каждый вход/кадр тратит секунды на PBKDF2+AES.
        yield GalleryImage(
            entry_id=bio_id,
            name=f"Биометрия {bio_id[:8]}",
            source="biometric",
            vault_path=vault_path,
        )


def embedding_path(entry_id: str, source: str = "face", user_id: str | None = None) -> Path:
    del source  # один каталог на пользователя; source нужен в API вызовов
    return user_embeddings_dir(user_id) / f"{entry_id}{EMBEDDING_VAULT_SUFFIX}"


def _legacy_embedding_paths(entry_id: str, user_id: str | None = None) -> list[Path]:
    uid = user_id or get_current_user_id()
    return [
        EMBEDDINGS_DIR / f"{entry_id}.npy",
        user_embeddings_dir(uid) / f"{entry_id}.npy",
    ]


def save_embedding(
    entry_id: str, feature: np.ndarray, source: str = "face", user_id: str | None = None
) -> None:
    uid, user_dir = _vault_user(user_id)
    path = embedding_path(entry_id, source, uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    np.save(buf, feature, allow_pickle=False)
    path.write_bytes(encrypt_bytes(uid, user_dir, buf.getvalue()))
    _secure_file(path)
    for legacy in _legacy_embedding_paths(entry_id, uid):
        if legacy.is_file():
            try:
                legacy.unlink()
            except OSError:
                pass


def load_embedding(
    entry_id: str, source: str = "face", user_id: str | None = None
) -> np.ndarray | None:
    uid, user_dir = _vault_user(user_id)
    path = embedding_path(entry_id, source, uid)
    if path.is_file():
        try:
            raw = decrypt_bytes(uid, user_dir, path.read_bytes())
            return np.load(io.BytesIO(raw), allow_pickle=False)
        except (ValueError, OSError):
            return None

    for legacy in _legacy_embedding_paths(entry_id, uid):
        if not legacy.is_file():
            continue
        try:
            feature = np.load(legacy, allow_pickle=False)
        except OSError:
            continue
        save_embedding(entry_id, feature, source=source, user_id=uid)
        return feature
    return None


def delete_embedding(entry_id: str, source: str = "face", user_id: str | None = None) -> None:
    uid = user_id or get_current_user_id()
    path = embedding_path(entry_id, source, uid)
    if path.exists():
        path.unlink()
    for legacy in _legacy_embedding_paths(entry_id, uid):
        if legacy.exists():
            legacy.unlink()


def repair_biometric_storage(user_id: str | None = None) -> int:
    """Удаляет повреждённые vault, которые нельзя расшифровать (без валидного эмбеддинга)."""
    uid = user_id or get_current_user_id()
    removed = 0
    bio_dir = user_biometrics_dir(uid)
    for vault_path in list(bio_dir.glob(f"*{VAULT_SUFFIX}")):
        bio_id = vault_path.stem
        if load_embedding(bio_id, source="biometric") is not None:
            continue
        if _load_biometric_record(bio_id, uid) is not None:
            continue
        try:
            vault_path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed


def delete_biometric_entry(bio_id: str, user_id: str | None = None) -> None:
    uid = user_id or get_current_user_id()
    path = _vault_path(bio_id, uid)
    if path.exists():
        path.unlink()
    delete_embedding(bio_id, source="biometric")


def add_face(name: str, snapshot_path: str | None = None) -> dict:
    faces = load_faces()
    entry = {
        "id": str(uuid4()),
        "name": name.strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if snapshot_path:
        entry["snapshot"] = snapshot_path
    faces.append(entry)
    save_faces(faces)
    return entry


def load_face_snapshot_bgr(face_id: str, user_id: str | None = None) -> Any | None:
    """Декодирует зашифрованный снимок лица (BGR, OpenCV)."""
    import cv2

    uid, user_dir = _vault_user(user_id)
    path = snapshot_path(face_id, uid)
    if path.is_file():
        try:
            jpeg = decrypt_bytes(uid, user_dir, path.read_bytes())
        except (ValueError, OSError):
            return None
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)

    legacy = SNAPSHOTS_DIR / f"{face_id}.jpg"
    if legacy.is_file():
        image_bgr = cv2.imread(str(legacy))
        if image_bgr is not None:
            save_face_snapshot(face_id, image_bgr, user_id=uid)
        return image_bgr
    return None


def save_face_snapshot(face_id: str, image_bgr, user_id: str | None = None) -> str | None:
    """Сохраняет кадр с камеры (BGR) в зашифрованный vault пользователя."""
    import cv2

    uid, user_dir = _vault_user(user_id)
    ok, encoded = cv2.imencode(".jpg", image_bgr)
    if not ok:
        return None
    path = snapshot_path(face_id, uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_bytes(uid, user_dir, encoded.tobytes()))
    _secure_file(path)
    legacy = SNAPSHOTS_DIR / f"{face_id}.jpg"
    if legacy.is_file():
        try:
            legacy.unlink()
        except OSError:
            pass
    return str(path.relative_to(user_data_dir(uid)))


def migrate_plaintext_biometrics(user_id: str | None = None) -> None:
    """Переносит открытые faces.json, .npy, .jpg в зашифрованное хранилище."""
    uid = user_id or get_current_user_id()
    legacy_faces_vault = user_data_dir(uid) / "faces.faces.vault"
    if legacy_faces_vault.is_file() and not _faces_vault_path(uid).is_file():
        try:
            legacy_faces_vault.rename(_faces_vault_path(uid))
        except OSError:
            pass
    load_faces()

    if EMBEDDINGS_DIR.is_dir():
        for npy in list(EMBEDDINGS_DIR.glob("*.npy")):
            try:
                feature = np.load(npy, allow_pickle=False)
                save_embedding(npy.stem, feature, source="face", user_id=uid)
            except OSError:
                pass

    emb_dir = user_embeddings_dir(uid)
    for npy in list(emb_dir.glob("*.npy")):
        vault = emb_dir / f"{npy.stem}{EMBEDDING_VAULT_SUFFIX}"
        if vault.is_file():
            try:
                npy.unlink()
            except OSError:
                pass
            continue
        try:
            feature = np.load(npy, allow_pickle=False)
            save_embedding(npy.stem, feature, source="biometric", user_id=uid)
        except OSError:
            pass

    if SNAPSHOTS_DIR.is_dir():
        import cv2

        for jpg in list(SNAPSHOTS_DIR.glob("*.jpg")):
            enc = snapshot_path(jpg.stem, uid)
            if enc.is_file():
                try:
                    jpg.unlink()
                except OSError:
                    pass
                continue
            image_bgr = cv2.imread(str(jpg))
            if image_bgr is not None:
                save_face_snapshot(jpg.stem, image_bgr, user_id=uid)


def save_biometric(crop_rgb, landmarks, score: float, user_id: str | None = None) -> str:
    """Сохраняет биометрию в зашифрованный vault, привязанный к пользователю."""
    import cv2

    uid = user_id or get_current_user_id()
    bio_id = str(uuid4())
    user_dir = user_data_dir(uid)
    bio_dir = user_biometrics_dir(uid)

    bgr = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode(".jpg", bgr)
    if not ok:
        raise RuntimeError("failed to encode biometric image")

    payload = pack_biometric_payload(
        bio_id=bio_id,
        user_id=uid,
        score=score,
        landmarks=landmarks.tolist(),
        image_jpeg=encoded.tobytes(),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    blob = encrypt_record(uid, user_dir, payload)
    vault_path = bio_dir / f"{bio_id}{VAULT_SUFFIX}"
    vault_path.write_bytes(blob)
    try:
        vault_path.chmod(0o600)
    except OSError:
        pass
    return bio_id


def register_biometric_embedding(
    bio_id: str, matcher, *, reload: bool = True, user_id: str | None = None
) -> bool:
    """Извлекает эмбеддинг для записи биометрии (вызов после save_biometric)."""
    image_bgr = load_biometric_image_bgr(bio_id, user_id)
    if image_bgr is None:
        return False
    return matcher.register_frame_image(bio_id, image_bgr, source="biometric", reload=reload)


def register_face_embedding(
    face_id: str, matcher, *, reload: bool = True, user_id: str | None = None
) -> bool:
    """Извлекает эмбеддинг для лица, сохранённого через GUI."""
    image_bgr = load_face_snapshot_bgr(face_id, user_id)
    if image_bgr is None:
        return False
    return matcher.register_frame_image(face_id, image_bgr, source="face", reload=reload)


def ensure_gallery_embeddings(matcher, user_id: str | None = None) -> tuple[int, int]:
    """Строит отсутствующие эмбеддинги для всей галереи; одна перезагрузка в конце."""
    uid = user_id or get_current_user_id()
    missing: list[tuple[str, str]] = []
    for item in iter_gallery_images(uid):
        if load_embedding(item.entry_id, source=item.source, user_id=uid) is None:
            missing.append((item.entry_id, item.source))

    built = 0
    if missing:
        matcher._ensure_recognizer()
        for entry_id, source in missing:
            try:
                if source == "biometric":
                    ok = register_biometric_embedding(
                        entry_id, matcher, reload=False, user_id=uid
                    )
                else:
                    ok = register_face_embedding(entry_id, matcher, reload=False, user_id=uid)
                if ok:
                    built += 1
            except Exception:
                if source == "biometric":
                    delete_biometric_entry(entry_id, uid)

    count = matcher.reload_gallery()
    return built, count


def ensure_biometric_embeddings(matcher) -> tuple[int, int]:
    """Обратная совместимость: строит эмбеддинги для всей галереи пользователя."""
    return ensure_gallery_embeddings(matcher)


def migrate_legacy_biometrics(user_id: str | None = None) -> int:
    """Переносит старые data/biometrics/*.jpg+.json в зашифрованные vault текущего пользователя."""
    if not BIOMETRICS_DIR.is_dir():
        return 0

    uid = user_id or get_current_user_id()
    migrated = 0
    for meta_path in BIOMETRICS_DIR.glob("*.json"):
        bio_id = meta_path.stem
        if _vault_path(bio_id, uid).exists():
            meta_path.unlink(missing_ok=True)
            (BIOMETRICS_DIR / f"{bio_id}.jpg").unlink(missing_ok=True)
            continue

        image_path = meta_path.with_suffix(".jpg")
        if not image_path.is_file():
            continue

        meta = load_json(meta_path, {})
        try:
            import cv2

            image_bgr = cv2.imread(str(image_path))
            if image_bgr is None:
                continue
            crop_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            landmarks_list = meta.get("landmarks", [])
            landmarks = np.array(landmarks_list, dtype=np.float32)
            score = float(meta.get("score", 0.0))

            new_id = save_biometric(crop_rgb, landmarks, score, user_id=uid)
            old_emb = EMBEDDINGS_DIR / f"{bio_id}.npy"
            if old_emb.is_file():
                try:
                    feature = np.load(old_emb, allow_pickle=False)
                    save_embedding(new_id, feature, source="biometric", user_id=uid)
                    old_emb.unlink(missing_ok=True)
                except OSError:
                    pass

            meta_path.unlink(missing_ok=True)
            image_path.unlink(missing_ok=True)
            migrated += 1
        except (OSError, ValueError, RuntimeError):
            continue

    return migrated
