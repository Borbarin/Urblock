"""Текстура Dear PyGui для превью камеры."""

from __future__ import annotations

import dearpygui.dearpygui as dpg
import numpy as np

TEXTURE_TAG = "camera_texture"
PREVIEW_TAG = "camera_preview"


class CameraTexture:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._front = np.zeros((height, width, 4), dtype=np.float32)
        self._back = np.zeros((height, width, 4), dtype=np.float32)
        self._front[:, :, 3] = 1.0
        self._back[:, :, 3] = 1.0
        self._upload = np.ascontiguousarray(self._front).ravel()
        self._ready = False

    def resize(self, width: int, height: int) -> None:
        if width == self.width and height == self.height and self._ready:
            return
        self.width = width
        self.height = height
        self._front = np.zeros((height, width, 4), dtype=np.float32)
        self._back = np.zeros((height, width, 4), dtype=np.float32)
        self._front[:, :, 3] = 1.0
        self._back[:, :, 3] = 1.0
        self._upload = np.ascontiguousarray(self._front).ravel()
        if self._ready:
            dpg.delete_item(TEXTURE_TAG)
            self._ready = False
        self.init()

    def init(self) -> None:
        if self._ready:
            return
        with dpg.texture_registry(show=False):
            dpg.add_dynamic_texture(
                self.width,
                self.height,
                self._upload.copy(),
                tag=TEXTURE_TAG,
            )
        self._ready = True

    def present(self, rgb: np.ndarray) -> None:
        """Атомарно подготавливает кадр и обновляет текстуру под mutex DPG."""
        h, w = rgb.shape[:2]
        if w != self.width or h != self.height:
            self.resize(w, h)

        np.multiply(rgb, 1.0 / 255.0, out=self._back[:, :, :3], casting="unsafe")
        self._back[:, :, 3] = 1.0
        self._front, self._back = self._back, self._front
        np.copyto(self._upload, self._front.ravel())

        with dpg.mutex():
            dpg.set_value(TEXTURE_TAG, self._upload)

    def configure_preview(self, display_width: int, display_height: int) -> None:
        if not dpg.does_item_exist(PREVIEW_TAG):
            return
        dpg.configure_item(
            PREVIEW_TAG,
            texture_tag=TEXTURE_TAG,
            width=display_width,
            height=display_height,
        )
