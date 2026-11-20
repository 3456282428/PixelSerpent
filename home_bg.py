"""首页高清背景：2048x1152 原画 1:1 直出 + 矢量动效（时间轴循环，画面永不静止）。

旧方案把首页烘焙成 960x540 的帧序列（assets/home_loop.bin），
在 960 逻辑画布上再被 SCALED 放大到窗口，等于"低分辨率源 + 二次放大"，整屏发糊。
这里改为：
  · 首页阶段使用 2048x1152 逻辑画布（与设计稿 1:1），由 GPU 直接缩放输出；
  · 底图取 assets/home_menu_original.jpg 原画，全程不经过降采样；
  · 动效（上浮气泡、光带扫过、星芒闪烁）全部用矢量实时绘制，按时间轴循环播放。
"""

import io
import math
import os
import random
import struct

import pygame

from config import WINDOW_WIDTH, WINDOW_HEIGHT
from utils import resource_path

HOME_SIZE = (WINDOW_WIDTH, WINDOW_HEIGHT)   # 首页画布 = 窗口逻辑尺寸（1920×1080）
K = HOME_SIZE[0] / 960.0        # 960×540 设计稿坐标 → 画布（=2）
K_Y = HOME_SIZE[1] / 540.0

_ART_FILES = (
    "assets/home_menu_original.jpg",
    "assets/home_menu_bg_clean.jpg",
    "assets/home_menu_bg.jpg",
)

# 首页高清循环视频（2048x1152 / 24fps / 243 帧，由 4K 源视频转出的逐帧 WebP）
_VIDEO_FILE = "assets/home_loop_hd.bin"


class _HdFrames:
    """SNKV 容器里的逐帧序列播放器（循环播放，只缓存最近几帧）"""

    def __init__(self, rel):
        self.ok = False
        self.fps = 24
        self.count = 0
        self.size = HOME_SIZE
        self.data = b""
        self.table = []
        self._cache = {}
        self._order = []
        try:
            with open(resource_path(rel), "rb") as fp:
                data = fp.read()
            magic, count, fps, w, h = struct.unpack_from("<4sHHHH", data, 0)
            if magic != b"SNKV" or not count:
                return
            self.data = data
            self.count = count
            self.fps = fps or 24
            self.size = (w, h)
            self.table = [struct.unpack_from("<II", data, 12 + i * 8)
                          for i in range(count)]
            self.ok = True
        except Exception:
            self.ok = False

    def _decode(self, index):
        surf = self._cache.get(index)
        if surf is not None:
            return surf
        off, length = self.table[index]
        try:
            surf = pygame.image.load(io.BytesIO(self.data[off:off + length]))
            # 帧序列按 2048×1152 打包，画布若不同则一次性缩放（每帧只做一次）
            if surf.get_size() != HOME_SIZE:
                surf = pygame.transform.smoothscale(surf, HOME_SIZE)
            try:
                surf = surf.convert()
            except pygame.error:
                pass
        except Exception:
            surf = pygame.Surface(HOME_SIZE)
            surf.fill((228, 246, 251))
        self._cache[index] = surf
        self._order.append(index)
        if len(self._order) > 6:          # 只留最近 6 帧，内存约 56 MB
            self._cache.pop(self._order.pop(0), None)
        return surf

    def frame(self, t):
        """按秒取帧，时间轴闭环 → 永远循环"""
        return self._decode(int(t * self.fps) % self.count)



def k(v):
    """960 坐标系数值 → 首页坐标系"""
    return int(round(v * K))


def ky(v):
    return int(round(v * K_Y))


def kf(size):
    """字号按首页画布放大"""
    return max(12, int(round(size * K)))


def _load_art():
    for rel in _ART_FILES:
        path = resource_path(rel)
        if not os.path.exists(path):
            continue
        try:
            img = pygame.image.load(path)
            try:
                img = img.convert()
            except pygame.error:
                pass
            if img.get_size() != HOME_SIZE:
                img = pygame.transform.smoothscale(img, HOME_SIZE)
            return img
        except Exception:
            continue
    return None


def _glow_sprite(radius, color=(255, 255, 255)):
    """柔光球：中心亮、边缘透明的圆形精灵（预渲染，按半径分档缓存）"""
    size = radius * 2 + 4
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size // 2
    steps = 10
    for i in range(steps, 0, -1):
        r = int(radius * i / steps)
        alpha = int(150 * (i / steps) ** 3)
        pygame.draw.circle(surf, (*color, alpha), (cx, cy), max(1, r))
    return surf


def _band_sprite(width, height, color=(16, 26, 34)):
    """斜向光带：横向软渐变，用于加法混合轻扫过画面。

    加法混合是逐通道相加、不受 alpha 影响，因此这里给出的必须是“要加上的亮度值”，
    数值很小才不会把画面洗白。
    """
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    for x in range(width):
        t = x / float(width - 1)
        f = math.sin(math.pi * t) ** 2
        rgb = tuple(int(c * f) for c in color)
        pygame.draw.line(surf, (*rgb, 0), (x, 0), (x, height))
    return surf


def _star_sprite(radius, color=(255, 250, 220)):
    """四芒星闪点"""
    size = radius * 4
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size // 2
    pygame.draw.polygon(surf, (*color, 120),
                        [(cx, cy - radius * 2), (cx + radius, cy),
                         (cx, cy + radius * 2), (cx - radius, cy)])
    pygame.draw.polygon(surf, (*color, 120),
                        [(cx - radius * 2, cy), (cx, cy - radius),
                         (cx + radius * 2, cy), (cx, cy + radius)])
    pygame.draw.circle(surf, (*color, 210), (cx, cy), max(1, radius // 2))
    return surf


class HomeBackdrop:
    """首页背景：原画 + 实时动效，替代 960x540 的 home_loop.bin 视频。"""

    def __init__(self, size=HOME_SIZE):
        self.size = (int(size[0]), int(size[1]))
        self.art = _load_art()
        self.video = _HdFrames(_VIDEO_FILE)      # 高清循环视频优先
        self.ok = self.art is not None or self.video.ok
        self._fallback = None
        self._t0 = pygame.time.get_ticks()
        self._hover_cache = {}
        self._history_layer = None
        self._history_src = None
        self._history_key = None
        if not self.ok:
            # 原画缺失时退回旧视频（放大到首页画布），保证首页不黑屏
            try:
                from home_scene import HomeScene
                self._fallback = HomeScene()
            except Exception:
                self._fallback = None

        rnd = random.Random(20261118)
        w, h = self.size
        # 气泡：竖向缓浮 + 横向正弦漂移
        self._bubbles = []
        for _ in range(30):
            r = rnd.choice((5, 7, 9, 12, 15, 19, 24))
            self._bubbles.append({
                "x": rnd.uniform(0, w),
                "y": rnd.uniform(0, h),
                "r": r,
                "speed": rnd.uniform(9, 26),
                "amp": rnd.uniform(8, 30),
                "w": rnd.uniform(0.25, 0.7),
                "ph": rnd.uniform(0, math.tau),
                "alpha": rnd.randint(46, 104),
            })
        self._bubble_sprites = {}
        # 星芒
        self._stars = []
        for _ in range(12):
            self._stars.append({
                "x": rnd.uniform(20, w - 20),
                "y": rnd.uniform(20, h - 20),
                "r": rnd.choice((3, 4, 5, 7)),
                "speed": rnd.uniform(0.9, 2.1),
                "ph": rnd.uniform(0, math.tau),
            })
        self._star_sprites = {}
        # 光带（加法混合，数值很小，只做轻微提亮）
        self._band = _band_sprite(k(360), h, (14, 24, 30))
        self._band2 = _band_sprite(k(520), h, (10, 20, 16))

    # ---------- 动效层 ----------

    def _bubble_sprite(self, r):
        spr = self._bubble_sprites.get(r)
        if spr is None:
            spr = _glow_sprite(r)
            self._bubble_sprites[r] = spr
        return spr

    def _star_sprite(self, r):
        spr = self._star_sprites.get(r)
        if spr is None:
            spr = _star_sprite(r)
            self._star_sprites[r] = spr
        return spr

    def _draw_bubbles(self, surf, t, w, h):
        span = h + 260
        for b in self._bubbles:
            y = (b["y"] - b["speed"] * t) % span - 130
            x = b["x"] + math.sin(t * b["w"] + b["ph"]) * b["amp"]
            # 上下边缘淡入淡出，循环衔接不见跳动
            edge = min(y + 130, h - y, 130)
            a = max(0.0, min(1.0, edge / 130.0))
            spr = self._bubble_sprite(b["r"])
            spr.set_alpha(int(b["alpha"] * a))
            surf.blit(spr, (int(x) - spr.get_width() // 2,
                            int(y) - spr.get_height() // 2))

    def _draw_stars(self, surf, t):
        for s in self._stars:
            v = 0.5 + 0.5 * math.sin(t * s["speed"] + s["ph"])
            spr = self._star_sprite(s["r"])
            spr.set_alpha(int(40 + 150 * v))
            surf.blit(spr, (int(s["x"]) - spr.get_width() // 2,
                            int(s["y"]) - spr.get_height() // 2))

    def _draw_bands(self, surf, t, w):
        # 两条光带以正弦往复缓慢扫过，周期闭环 → 无缝循环
        x1 = w * 0.5 + math.sin(t * 0.055) * w * 0.62 - self._band.get_width() / 2
        x2 = w * 0.5 + math.sin(t * 0.037 + 1.9) * w * 0.72 - self._band2.get_width() / 2
        surf.blit(self._band, (int(x1), 0), special_flags=pygame.BLEND_RGBA_ADD)
        surf.blit(self._band2, (int(x2), 0), special_flags=pygame.BLEND_RGBA_ADD)

    # ---------- 对外接口 ----------

    def update(self):
        if self._fallback is not None:
            self._fallback.update()

    def draw(self, screen, now_ms=None):
        """按当前时间绘制一帧动态背景"""
        if now_ms is None:
            now_ms = pygame.time.get_ticks()
        t = (now_ms - self._t0) / 1000.0
        w, h = self.size
        if self.video.ok:
            # 有高清视频时直接播视频帧（视频本身已含光点与漂浮动效）
            screen.blit(self.video.frame(t), (0, 0))
            return
        if self.art is not None:
            screen.blit(self.art, (0, 0))
        elif self._fallback is not None:
            self._fallback.update()
            frame = self._fallback.surface
            if frame.get_size() != self.size:
                frame = pygame.transform.smoothscale(frame, self.size)
            screen.blit(frame, (0, 0))
        else:
            screen.fill((228, 246, 251))
        self._draw_bands(screen, t, w)
        self._draw_bubbles(screen, t, w, h)
        self._draw_stars(screen, t)

    def draw_hover(self, screen, rect, now_ms=None, scale=None):
        """按钮悬停光效；rect 为 960 坐标系，按画布宽度自动换算"""
        if now_ms is None:
            now_ms = pygame.time.get_ticks()
        if scale is None:
            scale = screen.get_width() / 960.0
        if scale <= 0:
            return
        key = (rect.x, rect.y, rect.w, rect.h, round(scale, 3))
        overlay = self._hover_cache.get(key)
        if overlay is None:
            pad = int(9 * scale)
            w = int(rect.w * scale) + pad * 2
            h = int(rect.h * scale) + pad * 2
            overlay = pygame.Surface((w, h), pygame.SRCALPHA)
            radius = max(8, int(24 * scale))
            for i, alpha in ((0, 74), (int(3 * scale), 52), (int(6 * scale), 32)):
                body = pygame.Rect(pad - int(3 * scale) + i, pad - int(3 * scale) + i,
                                   int(rect.w * scale) + int(6 * scale) - i * 2,
                                   int(rect.h * scale) + int(6 * scale) - i * 2)
                pygame.draw.rect(overlay, (90, 230, 248, alpha), body,
                                 max(1, int(2 * scale)), radius)
            self._hover_cache[key] = overlay
        pulse = 0.72 + 0.28 * math.sin((now_ms % 1600) / 1600.0 * math.tau)
        overlay.set_alpha(int(255 * pulse))
        screen.blit(overlay, (int(rect.x * scale) - int(9 * scale),
                              int(rect.y * scale) - int(9 * scale)))

    def upscale_layer(self, surf):
        """把 960 空间的弹层（历史成绩等）放大到首页画布，按对象缓存"""
        key = id(surf)
        if self._history_key != key or self._history_layer is None:
            self._history_key = key
            self._history_src = surf          # 持有引用，避免 id 复用导致缓存串图
            self._history_layer = pygame.transform.smoothscale(surf, self.size)
        return self._history_layer
