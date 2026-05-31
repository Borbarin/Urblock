from __future__ import annotations

from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

import i18n.ru as ru
from camera.opencv_capture import CameraDevice, list_cameras
from storage import patch_settings

if TYPE_CHECKING:
    from main import UrblockApp

COMBO_PREVIEW = "settings_preview_camera"
COMBO_DETECT = "settings_detect_camera"
CHECK_AUTO_DETECT = "settings_auto_detect"
COLOR_BOX = "settings_face_box_color"
STATUS_TAG = "settings_status"


class SettingsController:
    def __init__(self, app: UrblockApp) -> None:
        self.app = app
        self._devices: list[CameraDevice] = []
        self._syncing_ui = False

    def _index_from_combo(self, combo_tag: str, fallback_key: str) -> int:
        label = dpg.get_value(combo_tag)
        for device in self._devices:
            if device.label == label:
                return device.index
        if self._devices:
            return self._devices[0].index
        return int(self.app.settings.get(fallback_key, 0))

    def _apply(self, **updates) -> None:
        self.app.settings = patch_settings(updates)

    def _fill_combo(self, combo_tag: str, selected_index: int, fallback_key: str) -> int:
        """Заполняет список и возвращает фактический индекс выбранной камеры."""
        if not self._devices:
            dpg.configure_item(combo_tag, items=[ru.SETTINGS_NO_CAMERA_ITEM], enabled=False)
            dpg.set_value(combo_tag, ru.SETTINGS_NO_CAMERA_ITEM)
            return selected_index

        labels = [d.label for d in self._devices]
        dpg.configure_item(combo_tag, items=labels, enabled=True)
        selected_label = next(
            (d.label for d in self._devices if d.index == selected_index),
            labels[0],
        )
        dpg.set_value(combo_tag, selected_label)
        return self._index_from_combo(combo_tag, fallback_key)

    def _active_camera_index(self) -> int | None:
        capture = self.app.camera._capture
        if capture.is_open and capture.device_index is not None:
            return capture.device_index
        return None

    def refresh_camera_list(self, status_message: str | None = None) -> None:
        self._syncing_ui = True
        try:
            self._devices = list_cameras(active_index=self._active_camera_index())

            preview_idx = int(self.app.settings.get("preview_camera_index", 0))
            detect_idx = int(self.app.settings.get("detect_camera_index", 0))

            if self._devices:
                preview_idx = self._fill_combo(COMBO_PREVIEW, preview_idx, "preview_camera_index")
                detect_idx = self._fill_combo(COMBO_DETECT, detect_idx, "detect_camera_index")
                self._apply(
                    preview_camera_index=preview_idx,
                    detect_camera_index=detect_idx,
                )
            else:
                self._fill_combo(COMBO_PREVIEW, preview_idx, "preview_camera_index")
                self._fill_combo(COMBO_DETECT, detect_idx, "detect_camera_index")

            self.app.camera.restart_preview()

            if status_message is None:
                if self._devices:
                    status_message = ru.SETTINGS_CAMERAS_FOUND.format(count=len(self._devices))
                else:
                    status_message = ru.SETTINGS_CAMERAS_NONE

            dpg.set_value(STATUS_TAG, status_message)
        finally:
            self._syncing_ui = False

    def apply_to_ui(self) -> None:
        s = self.app.settings
        self._syncing_ui = True
        try:
            dpg.set_value(CHECK_AUTO_DETECT, s.get("auto_detect_enabled", False))
            color = s.get("face_box_color", [0, 255, 0])
            dpg.set_value(COLOR_BOX, [color[0], color[1], color[2], 255])
        finally:
            self._syncing_ui = False
        self.refresh_camera_list(status_message="")

    def on_preview_camera_changed(self) -> None:
        if self._syncing_ui:
            return
        if not self._devices:
            self.refresh_camera_list()
            return
        index = self._index_from_combo(COMBO_PREVIEW, "preview_camera_index")
        self._apply(preview_camera_index=index)
        self.app.camera.restart_preview()
        dpg.set_value(STATUS_TAG, ru.CAMERA_ACTIVE.format(index=index))

    def on_detect_camera_changed(self) -> None:
        if self._syncing_ui:
            return
        if not self._devices:
            self.refresh_camera_list()
            return
        index = self._index_from_combo(COMBO_DETECT, "detect_camera_index")
        self._apply(detect_camera_index=index)

    def on_auto_detect(self) -> None:
        if self._syncing_ui:
            return
        self._apply(auto_detect_enabled=bool(dpg.get_value(CHECK_AUTO_DETECT)))

    def on_box_color(self) -> None:
        if self._syncing_ui:
            return
        c = dpg.get_value(COLOR_BOX)
        self._apply(face_box_color=[int(c[0]), int(c[1]), int(c[2])])


def build_settings_tab(ctrl: SettingsController) -> None:
    dpg.add_text(ru.SETTINGS_TITLE, color=(180, 200, 255))
    dpg.add_spacer(height=12)

    dpg.add_button(
        label=ru.SETTINGS_BTN_REFRESH,
        callback=lambda: ctrl.refresh_camera_list(),
    )
    dpg.add_spacer(height=8)
    dpg.add_combo(
        label=ru.SETTINGS_PREVIEW_CAMERA,
        tag=COMBO_PREVIEW,
        items=[ru.SETTINGS_SCANNING],
        default_value=ru.SETTINGS_SCANNING,
        width=420,
        callback=lambda: ctrl.on_preview_camera_changed(),
    )
    dpg.add_spacer(height=6)
    dpg.add_combo(
        label=ru.SETTINGS_DETECT_CAMERA,
        tag=COMBO_DETECT,
        items=[ru.SETTINGS_SCANNING],
        default_value=ru.SETTINGS_SCANNING,
        width=420,
        callback=lambda: ctrl.on_detect_camera_changed(),
    )
    dpg.add_spacer(height=12)

    dpg.add_checkbox(
        label=ru.SETTINGS_AUTO_DETECT,
        tag=CHECK_AUTO_DETECT,
        callback=lambda: ctrl.on_auto_detect(),
    )
    dpg.add_spacer(height=12)

    dpg.add_color_edit(
        label=ru.SETTINGS_FACE_BOX_COLOR,
        tag=COLOR_BOX,
        default_value=(0, 255, 0, 255),
        no_alpha=True,
        width=200,
        callback=lambda: ctrl.on_box_color(),
    )
    dpg.add_spacer(height=10)
    dpg.add_text("", tag=STATUS_TAG, color=(120, 200, 120))
