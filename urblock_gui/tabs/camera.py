from __future__ import annotations

from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

import i18n.ru as ru
from camera.opencv_capture import OpenCVCamera
from config import FRAME_HEIGHT, FRAME_WIDTH
from storage import add_face, save_face_snapshot
from ui.camera_texture import PREVIEW_TAG, TEXTURE_TAG, CameraTexture
from vision.face_detector import YuNetDetector
from vision.face_pipeline import FacePipeline

if TYPE_CHECKING:
    from main import UrblockApp

STATUS_TAG = "camera_status"
MATCH_TAG = "camera_match"
PROFILE_TAG = "camera_profile"
BTN_CAPTURE = "camera_btn_capture"
NAME_INPUT = "camera_face_name"


class CameraController:
    def __init__(self, app: UrblockApp) -> None:
        self.app = app
        self._capture = OpenCVCamera()
        self._texture = CameraTexture(FRAME_WIDTH, FRAME_HEIGHT)
        self._faces = FacePipeline()
        self._running = False
        self._model_warned = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def width(self) -> int:
        return FRAME_WIDTH

    @property
    def height(self) -> int:
        return FRAME_HEIGHT

    def init_texture(self) -> None:
        self._texture.init()
        self._texture.configure_preview(self.width, self.height)

    def start(self) -> bool:
        if self._running:
            self.stop()
        index = int(self.app.settings.get("preview_camera_index", 0))
        if not self._capture.open(index, self.width, self.height):
            dpg.set_value(STATUS_TAG, ru.CAMERA_OPEN_FAILED.format(index=index))
            return False

        self._running = True
        dpg.set_value(STATUS_TAG, ru.CAMERA_ACTIVE.format(index=index))
        dpg.configure_item(BTN_CAPTURE, enabled=True)
        return True

    def stop(self) -> None:
        self._running = False
        self._capture.close()
        dpg.set_value(STATUS_TAG, ru.CAMERA_STOPPED)
        dpg.set_value(MATCH_TAG, "")
        dpg.configure_item(BTN_CAPTURE, enabled=False)

    def restart_preview(self) -> None:
        self.stop()
        if self.app.active_tab == TAB_CAMERA_TAG:
            self.start()

    def release(self) -> None:
        self.stop()

    def refresh_profile_label(self) -> None:
        if not dpg.does_item_exist(PROFILE_TAG):
            return
        profile = self.app.profile
        if profile.locked:
            dpg.set_value(PROFILE_TAG, ru.PROFILE_LOCKED)
        else:
            dpg.set_value(
                PROFILE_TAG,
                ru.PROFILE_UNLOCKED.format(
                    name=profile.owner_name,
                    percent=profile.match_score * 100,
                ),
            )

    def _update_match_label(self, match) -> None:
        if match is None:
            count = self._faces.matcher.gallery_size
            if count == 0:
                dpg.set_value(MATCH_TAG, ru.CAMERA_NO_GALLERY)
            else:
                dpg.set_value(MATCH_TAG, ru.CAMERA_NO_FACE)
            return
        if match.is_match:
            dpg.set_value(
                MATCH_TAG,
                ru.CAMERA_MATCH_OK.format(name=match.name, percent=match.score * 100),
            )
        else:
            dpg.set_value(
                MATCH_TAG,
                ru.CAMERA_MATCH_FAIL.format(percent=match.score * 100),
            )

    def tick(self, update_preview: bool = True) -> None:
        if not self._running:
            return
        frame = self._capture.take_frame()
        if frame is None:
            return
        if not update_preview:
            return

        if not self._faces.is_ready:
            if not self._model_warned:
                dpg.set_value(STATUS_TAG, ru.CAMERA_MODEL_MISSING)
                self._model_warned = True
            self._texture.present(frame)
            return

        if self._model_warned:
            self._model_warned = False
            idx = self._capture.device_index
            if idx is not None:
                dpg.set_value(STATUS_TAG, ru.CAMERA_ACTIVE.format(index=idx))

        frame, match = self._faces.process(frame, self.app.settings)
        self._update_match_label(match)
        self.refresh_profile_label()
        self._texture.present(frame)

    def capture_face(self) -> None:
        name = dpg.get_value(NAME_INPUT).strip()
        if not name:
            dpg.set_value(STATUS_TAG, ru.CAMERA_NAME_REQUIRED)
            return
        if not self._running:
            dpg.set_value(STATUS_TAG, ru.CAMERA_NOT_RUNNING)
            return

        frame_bgr = self._capture.last_frame_bgr
        entry = add_face(name)
        registered = False
        try:
            if frame_bgr is not None:
                save_face_snapshot(entry["id"], frame_bgr)
                matcher = self._faces.matcher
                registered = matcher.register_frame_image(entry["id"], frame_bgr)
                if not registered:
                    faces = matcher._detector.detect(frame_bgr)
                    face = YuNetDetector.largest(faces)
                    if face is not None:
                        registered = matcher.register_detection(
                            entry["id"], frame_bgr, face
                        )
            if registered:
                self._faces.reload_gallery()
        except Exception:
            dpg.set_value(STATUS_TAG, ru.CAMERA_FACE_ERROR)
            self.app.faces.refresh_table()
            dpg.set_value(NAME_INPUT, "")
            return

        if registered:
            dpg.set_value(STATUS_TAG, ru.CAMERA_FACE_ADDED.format(name=name))
        elif frame_bgr is not None:
            dpg.set_value(STATUS_TAG, ru.CAMERA_FACE_NO_EMBED.format(name=name))
        else:
            dpg.set_value(STATUS_TAG, ru.CAMERA_FACE_ADDED.format(name=name))

        dpg.set_value(NAME_INPUT, "")
        self.app.faces.refresh_table()


TAB_CAMERA_TAG = "tab_camera"


def build_camera_tab(ctrl: CameraController) -> None:
    dpg.add_text(ru.CAMERA_PREVIEW_TITLE, color=(180, 200, 255))
    dpg.add_spacer(height=6)
    dpg.add_image(
        TEXTURE_TAG,
        tag=PREVIEW_TAG,
        width=ctrl.width,
        height=ctrl.height,
    )
    dpg.add_spacer(height=6)
    dpg.add_text("", tag=PROFILE_TAG, color=(255, 200, 120))
    dpg.add_text("", tag=MATCH_TAG, color=(180, 220, 255))
    dpg.add_spacer(height=8)
    dpg.add_text(ru.CAMERA_SAVE_TITLE, color=(200, 200, 200))
    with dpg.group(horizontal=True):
        dpg.add_input_text(
            hint=ru.CAMERA_NAME_HINT,
            tag=NAME_INPUT,
            width=200,
        )
        dpg.add_button(
            label=ru.CAMERA_BTN_ADD,
            tag=BTN_CAPTURE,
            callback=lambda: ctrl.capture_face(),
            enabled=False,
        )
    dpg.add_spacer(height=6)
    dpg.add_text(ru.CAMERA_STATUS_IDLE, tag=STATUS_TAG, color=(160, 160, 160))
