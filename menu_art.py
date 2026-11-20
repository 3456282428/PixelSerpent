"""二级菜单插画底图：按设计稿逐像素呈现，交互由透明热区承载。

三张 2048x1152 的菜单插画在缩放到 960x540 后直接铺满窗口，
标题与按钮文字都取自画面本身，因此这里只提供两样东西：
  · surface(key) —— 缩放好的底图
  · rects(key)   —— 与画面胶囊对齐的点击热区（顺序 = 按钮顺序）
"""

import os

import pygame

from config import WINDOW_WIDTH, WINDOW_HEIGHT
from utils import resource_path, get_chinese_font, render_text

KEY_SKY = "sky"              # 天空贪吃蛇二级菜单
KEY_CLASSIC = "classic"      # 经典贪吃蛇二级菜单
KEY_DIFF = "diff"            # 人机对战 · 选择难度（带“限时”）
KEY_DIFF_SOLO = "diff_solo"  # 经典单人 · 选择难度
KEY_DIFF_SKY = "diff_sky"    # 天空人机 · 选择难度

_FILES = {
    KEY_SKY: "assets/menu_sky_art.png",
    KEY_CLASSIC: "assets/menu_classic_art.png",
    # 此图底部“限时：不限时”已抹除，改由代码按当前选项绘制
    KEY_DIFF: "assets/menu_diff_art_clean.png",
    KEY_DIFF_SOLO: "assets/menu_diff_solo_art.png",
    KEY_DIFF_SKY: "assets/menu_diff_sky_art.png",
}

# 2048x1152 设计稿各胶囊的像素位置（左,上,右,下），换算出 960x540 热区
_PILLS_2048 = {
    KEY_SKY: [(675, 533, 1365, 628), (675, 638, 1365, 733),
              (675, 743, 1365, 838), (675, 848, 1365, 943)],
    KEY_CLASSIC: [(757, 475, 1285, 575), (757, 598, 1285, 700),
                  (757, 740, 1285, 845), (757, 868, 1285, 970)],
    KEY_DIFF: [(752, 428, 1302, 530), (752, 565, 1302, 665),
               (752, 700, 1302, 800), (752, 832, 1302, 935)],
}

# 另两版难度页是在同一构图上换标题生成的，胶囊略大，按实测 960 坐标直接记录
_PILLS_960 = {
    KEY_DIFF_SOLO: [(349, 199, 610, 254), (349, 261, 610, 316),
                    (349, 325, 610, 380), (349, 390, 610, 445)],
    KEY_DIFF_SKY: [(349, 199, 610, 254), (349, 261, 610, 316),
                   (349, 325, 610, 380), (349, 390, 610, 445)],
}

_SCALE = 540.0 / 1152.0
# 人机对战页底部“限时”热区（对应设计稿 x 897..1154 / y 971..1014）
TIME_RECT = pygame.Rect(int(880 * _SCALE), int(955 * _SCALE),
                        int(290 * _SCALE), int(70 * _SCALE))
TIME_CENTER = (int(1024 * _SCALE), int(993 * _SCALE))
TIME_COLOR = (39, 65, 91)

_native = {}     # 原始尺寸（2048x1152）底图缓存
_scaled = {}     # 按目标画布尺寸缩放后的底图缓存


def _load_native(key):
    """加载设计稿原图，不做任何缩放"""
    if key in _native:
        return _native[key]
    img = None
    path = resource_path(_FILES[key])
    if os.path.exists(path):
        try:
            img = pygame.image.load(path)
            try:
                img = img.convert()       # 无显示设备时退回未转换表面
            except pygame.error:
                pass
        except Exception:
            img = None
    _native[key] = img
    return img


def surface_for(key, size):
    """取指定画布尺寸的底图。

    高清阶段画布就是 2048x1152，与设计稿原图一致 → 直接 1:1 直出，不再降采样；
    960 画布下仍按原逻辑平滑缩放。资源缺失时返回 None，调用方回退旧版绘制。
    """
    size = (int(size[0]), int(size[1]))
    ck = (key, size[0], size[1])
    if ck in _scaled:
        return _scaled[ck]
    img = _load_native(key)
    if img is not None and img.get_size() != size:
        img = pygame.transform.smoothscale(img, size)
    _scaled[ck] = img
    return img


def surface(key):
    """960x540 画布下的底图（兼容旧调用）"""
    return surface_for(key, (WINDOW_WIDTH, WINDOW_HEIGHT))


def rects(key):
    """按钮热区，顺序与 design 中自上而下的按钮一致"""
    if key in _PILLS_960:
        return [pygame.Rect(x0, y0, x1 - x0, y1 - y0)
                for x0, y0, x1, y1 in _PILLS_960[key]]
    return [pygame.Rect(round(x0 * _SCALE), round(y0 * _SCALE),
                        round((x1 - x0) * _SCALE), round((y1 - y0) * _SCALE))
            for x0, y0, x1, y1 in _PILLS_2048[key]]


def apply_rects(buttons, key):
    """把按钮热区对到当前底图的胶囊上（三版难度页构图略有差异）"""
    for btn, r in zip(buttons, rects(key)):
        btn.rect = r.copy()
        btn.original_rect = r.copy()
        btn.hover = False


# ---------------- 底部“限时”文字 ----------------

_time_cache = {}


def time_label_surface(text, scale=1.0):
    """按设计稿风格绘制“限时：xx”（深蓝字 + 白描边，字宽对齐原图 121px）

    scale=1.0 为 960 画布；高清画布传入实际缩放系数后按大字号重新渲染，不做位图放大。
    """
    ck = (text, round(scale, 3))
    surf = _time_cache.get(ck)
    if surf is not None:
        return surf
    target_w = int(258 * _SCALE * scale)
    font = get_chinese_font(max(12, int(round(18 * scale))))
    for size in (22, 21, 20, 19, 18):
        probe = render_text(get_chinese_font(max(12, int(round(size * scale)))), text, TIME_COLOR)
        if probe.get_width() <= target_w:
            font = get_chinese_font(max(12, int(round(size * scale))))
            break
    base = render_text(font, text, TIME_COLOR)
    pad = max(1, int(round(scale)))
    out = pygame.Surface((base.get_width() + pad * 2, base.get_height() + pad * 2),
                         pygame.SRCALPHA)
    white = render_text(font, text, (255, 255, 255))
    for dx in range(-pad, pad + 1):
        for dy in range(-pad, pad + 1):
            if dx or dy:
                out.blit(white, (pad + dx, pad + dy))
    out.blit(base, (pad, pad))
    _time_cache[ck] = out
    return out
