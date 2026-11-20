"""首页动态背景：播放 assets/home_loop.bin 中打包的逐帧动画。"""
import io
import math
import struct

import pygame

from utils import resource_path


class HomeScene:
    """视频式首屏背景，并在画面原有按钮位置上绘制悬停光效。"""

    def __init__(self):
        self.ok = False
        self.fps = 24
        self.size = (960, 540)
        self._data = b""
        self._frames = []
        self._cache = {}
        self._order = []
        self._last = -1
        self._t0 = pygame.time.get_ticks()
        self._hover = {}
        self.surface = pygame.Surface(self.size)
        self.surface.fill((228, 246, 251))
        try:
            with open(resource_path("assets/home_loop.bin"), "rb") as fp:
                data = fp.read()
            magic, count, fps, w, h = struct.unpack_from("<4sHHHH", data, 0)
            if magic != b"SNKV" or not count:
                raise ValueError("bad home_loop.bin")
            self._data = data
            self.fps = fps or 24
            self.size = (w, h)
            for i in range(count):
                off, length = struct.unpack_from("<II", data, 12 + i * 8)
                self._frames.append((off, length))
            self.surface = self._decode(0)
            self._last = 0
            self.ok = True
        except Exception:
            self.ok = False

    def _decode(self, index):
        surf = self._cache.get(index)
        if surf is not None:
            return surf
        off, length = self._frames[index]
        try:
            surf = pygame.image.load(io.BytesIO(self._data[off:off + length]))
            if surf.get_size() != self.size:
                surf = pygame.transform.smoothscale(surf, self.size)
            surf = surf.convert()
        except Exception:
            surf = pygame.Surface(self.size)
            surf.fill((228, 246, 251))
        self._cache[index] = surf
        self._order.append(index)
        if len(self._order) > 8:
            self._cache.pop(self._order.pop(0), None)
        return surf

    def update(self):
        """按 24fps 推进画面，循环播放"""
        if not self.ok:
            return
        elapsed = (pygame.time.get_ticks() - self._t0) / 1000.0
        index = int(elapsed * self.fps) % len(self._frames)
        if index != self._last:
            self.surface = self._decode(index)
            self._last = index

    def draw(self, screen):
        screen.blit(self.surface, (0, 0))

    def draw_hover(self, screen, rect, now_ms):
        """在视频原有按钮上叠柔和的青色高亮，保持画面原貌"""
        key = (rect.x, rect.y, rect.w, rect.h)
        pad = 9
        overlay = self._hover.get(key)
        if overlay is None:
            overlay = pygame.Surface((rect.w + pad * 2, rect.h + pad * 2),
                                     pygame.SRCALPHA)
            for i, alpha in ((0, 70), (3, 48), (6, 30)):
                pygame.draw.rect(overlay, (90, 230, 248, alpha),
                                 (pad - 3 + i, pad - 3 + i,
                                  rect.w + 6 - i * 2, rect.h + 6 - i * 2),
                                 2, 24)
            body = pygame.Rect(pad, pad, rect.w, rect.h)
            pygame.draw.rect(overlay, (255, 255, 255, 26), body, 0, 22)
            pygame.draw.rect(overlay, (90, 226, 248, 215), body, 2, 22)
            self._hover[key] = overlay
        pulse = 0.5 + 0.5 * math.sin(now_ms * 0.004)
        overlay.set_alpha(150 + int(70 * pulse))
        screen.blit(overlay, (rect.x - pad, rect.y - pad))
