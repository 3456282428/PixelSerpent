import os
import pygame
import random
import math
from config import *
from utils import render_text, key_is, resource_path, to_logical_pos, UI_SCALE

_BTN_TEXTURE = None


def _load_button_texture():
    """加载 AI 生成的按钮底纹（只读一次）"""
    global _BTN_TEXTURE
    if _BTN_TEXTURE is None:
        path = resource_path("assets/btn_texture.png")
        if os.path.exists(path):
            try:
                _BTN_TEXTURE = pygame.image.load(path).convert_alpha()
            except Exception:
                _BTN_TEXTURE = False
        else:
            _BTN_TEXTURE = False
    return _BTN_TEXTURE or None


def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def _shift_color(c, delta):
    return tuple(max(0, min(255, ch + delta)) for ch in c[:3])


def _rounded_gradient(size, radius, top, bottom, alpha=255):
    """圆角竖向渐变面片（用于按钮/面板，避免每帧重复绘制）"""
    w, h = int(size[0]), int(size[1])
    grad = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        c = _lerp_color(top, bottom, y / max(1, h - 1))
        pygame.draw.line(grad, (*c, alpha), (0, y), (w, y))
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h), 0, radius)
    grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return grad


def make_radial_glow(radius, color, alpha=90):
    """径向渐隐光晕（星空/极光背景用）"""
    surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    step = max(1, radius // 48)
    for r in range(radius, 0, -step):
        a = int(alpha * (1 - r / radius) ** 2)
        pygame.draw.circle(surf, (*color, a), (radius, radius), r)
    return surf


def create_menu_background(width, height):
    """首页背景：卡通蛇主题插画（assets/home_bg_wide.jpg），缺失时回退青色调渐变"""
    for name in ("assets/home_bg_wide.jpg", "assets/home_bg.jpg"):
        path = resource_path(name)
        if os.path.exists(path):
            try:
                img = pygame.image.load(path).convert()
                return pygame.transform.smoothscale(img, (width, height))
            except Exception:
                continue
    surf = pygame.Surface((width, height)).convert()
    stops = ((0.0, (240, 252, 254)), (0.5, (222, 245, 250)), (1.0, (204, 235, 244)))
    for y in range(height):
        t = y / max(1, height - 1)
        if t <= 0.5:
            c = _lerp_color(stops[0][1], stops[1][1], t / 0.5)
        else:
            c = _lerp_color(stops[1][1], stops[2][1], (t - 0.5) / 0.5)
        pygame.draw.line(surf, c, (0, y), (width, y))
    for cx, cy, rad, col, a in (
        (110, 90, 340, (255, 255, 255), 120),
        (520, 540, 380, (140, 220, 238), 70),
        (330, 280, 300, (205, 244, 252), 90),
    ):
        surf.blit(make_radial_glow(rad, col, a), (cx - rad, cy - rad))
    rng = random.Random(2026)
    for _ in range(120):
        x, y = rng.randint(0, width - 1), rng.randint(0, height - 1)
        b = rng.randint(150, 195)
        surf.set_at((x, y), (b - 30, b + 15, b + 32))
    return surf


def render_gradient_text(font, text, top_color, bottom_color):
    """竖向渐变文字（用白色文字的 alpha 作为遮罩）"""
    mask = font.render(text, True, (255, 255, 255))
    w, h = mask.get_size()
    grad = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        c = _lerp_color(top_color, bottom_color, y / max(1, h - 1))
        pygame.draw.line(grad, (*c, 255), (0, y), (w, y))
    out = pygame.Surface((w, h), pygame.SRCALPHA)
    out.blit(grad, (0, 0))
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return out


def render_cartoon_text(font, text, colors, stroke=5,
                        outline=(255, 255, 255), rim=(38, 76, 108),
                        shadow=(40, 96, 122)):
    """卡通描边标题：逐字渐变上色 + 白色粗描边 + 深色外轮廓 + 投影"""
    sizes = [font.size(ch) for ch in text]
    w = sum(s[0] for s in sizes)
    h = font.get_height()
    pad = stroke + 5
    surf = pygame.Surface((w + pad * 2, h + pad * 2 + 8), pygame.SRCALPHA)

    def blit_disc(glyph, x, y, radius):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    surf.blit(glyph, (x + dx, y + dy))

    x = pad
    for ch, (top, bottom) in zip(text, colors):
        mask = font.render(ch, True, (255, 255, 255))
        gw, gh = mask.get_size()
        grad = pygame.Surface((gw, gh), pygame.SRCALPHA)
        for yy in range(gh):
            c = _lerp_color(top, bottom, yy / max(1, gh - 1))
            pygame.draw.line(grad, (*c, 255), (0, yy), (gw, yy))
        grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        dark = font.render(ch, True, shadow)
        for dx in (-2, 0, 2):
            surf.blit(dark, (x + dx, pad + 7))
        blit_disc(font.render(ch, True, rim), x, pad, stroke + 2)
        blit_disc(font.render(ch, True, outline), x, pad, stroke)
        surf.blit(grad, (x, pad))
        x += gw
    return surf


def make_text_glow(font, text, color, spread=20, alpha=120):
    """柔光文字：多层偏移叠加出光晕，避免缩放模糊产生的暗色矩形"""
    base = font.render(text, True, color)
    w, h = base.get_size()
    surf = pygame.Surface((w + spread * 2, h + spread * 2), pygame.SRCALPHA)
    step = max(1, spread // 8)
    ghost = base.copy()
    for dy in range(-spread, spread + 1, step):
        for dx in range(-spread, spread + 1, step):
            d = math.hypot(dx, dy)
            if d > spread:
                continue
            a = int(alpha * (1.0 - d / max(1, spread)) ** 2 / 6)
            if a <= 0:
                continue
            ghost.set_alpha(a)
            surf.blit(ghost, (spread + dx, spread + dy))
    return surf


def draw_glass_panel(screen, rect, radius=16, fill=(14, 22, 44, 190),
                     border=(90, 190, 230, 90)):
    """半透明玻璃面板（用于主菜单统计卡）"""
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, fill, (0, 0, rect.width, rect.height), 0, radius)
    pygame.draw.rect(panel, border, (0, 0, rect.width, rect.height), 1, radius)
    screen.blit(panel, rect.topleft)


def draw_snake_emblem(screen, cx, cy, width=220, t_ms=0, scale=1.0):
    """标题下方的蛇形装饰：渐变蛇身 + 蛇头眼睛 + 尾部小苹果"""
    n = 18
    amp = 5.0 * scale
    phase = t_ms * 0.003
    spacing = width / (n - 1)
    start_x = cx - width / 2
    for i in range(n):
        x = start_x + i * spacing
        y = cy + math.sin(i * 0.55 + phase) * amp
        col = _lerp_color((110, 255, 170), (70, 205, 255), i / (n - 1))
        rad = (5.0 if i < n - 1 else 8.0) * scale
        pygame.draw.circle(screen, (4, 26, 36), (int(x), int(y + 2)), int(rad + 1))
        pygame.draw.circle(screen, col, (int(x), int(y)), int(rad))
    hx = start_x + (n - 1) * spacing
    hy = cy + math.sin((n - 1) * 0.55 + phase) * amp
    for ex in (-2.5, 2.5):
        pygame.draw.circle(screen, (10, 24, 34), (int(hx + ex), int(hy - 1)), 2)
        pygame.draw.circle(screen, (235, 255, 255), (int(hx + ex - 1), int(hy - 2)), 1)
    pygame.draw.line(screen, (255, 100, 125), (int(hx + 9 * scale), int(hy)),
                     (int(hx + 16 * scale), int(hy + 3 * scale)), 1)
    ax, ay = start_x - 10 * scale, cy + math.sin(phase) * amp
    pygame.draw.circle(screen, (255, 82, 96), (int(ax), int(ay)), int(5.0 * scale))
    pygame.draw.line(screen, (95, 220, 130), (int(ax), int(ay - 5 * scale)),
                     (int(ax + 3 * scale), int(ay - 9 * scale)), 1)


class Star:
    __slots__ = ["x","y","speed","size","b","twinkle_speed","twinkle_phase","current_b","width","height"]
    def __init__(self, width=None, height=None):
        self.width = width if width is not None else WINDOW_WIDTH
        self.height = height if height is not None else WINDOW_HEIGHT
        self.x = random.randint(0, self.width)
        self.y = random.randint(0, self.height)
        self.speed = random.uniform(0.2, 0.8)
        self.size = random.randint(1, 3)
        self.b = random.randint(100, 220)
        self.twinkle_speed = random.uniform(0.5, 2.0)
        self.twinkle_phase = random.uniform(0, 2*math.pi)
        self.current_b = self.b

    def update(self):
        self.y += self.speed
        if self.y > self.height:
            self.y = 0
            self.x = random.randint(0, self.width)
        self.current_b = self.b + 50 * (1 + math.sin(pygame.time.get_ticks() * 0.003 * self.twinkle_speed + self.twinkle_phase)) // 2
        self.current_b = max(80, min(255, self.current_b))

    def draw(self, screen):
        pygame.draw.circle(screen, (self.current_b, self.current_b, 255), (int(self.x), int(self.y)), self.size)

_ICON_CACHE = {}


def _make_icon(name, color, size=20):
    """首页按钮图标（矢量绘制并缓存）：wings / gamepad / gear / power"""
    key = (name, color, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    u = size / 24.0

    def pt(x, y):
        return (x * u, y * u)

    if name == "wings":
        pygame.draw.polygon(s, color, [pt(12, 12.5), pt(2.4, 5.5), pt(4.6, 12.6), pt(11.4, 15.2)])
        pygame.draw.polygon(s, color, [pt(12, 12.5), pt(21.6, 5.5), pt(19.4, 12.6), pt(12.6, 15.2)])
        pygame.draw.polygon(s, color, [pt(11.2, 16.6), pt(4.8, 20.4), pt(8.2, 21.3), pt(11.6, 18.4)])
        pygame.draw.polygon(s, color, [pt(12.8, 16.6), pt(19.2, 20.4), pt(15.8, 21.3), pt(12.4, 18.4)])
    elif name == "gamepad":
        pygame.draw.rect(s, color, (round(2 * u), round(6.5 * u), round(20 * u), round(11 * u)),
                         2, round(5 * u))
        dx, dy = 8.0 * u, 12.0 * u
        pygame.draw.line(s, color, (dx - 2.2 * u, dy), (dx + 2.2 * u, dy), max(2, round(1.8 * u)))
        pygame.draw.line(s, color, (dx, dy - 2.2 * u), (dx, dy + 2.2 * u), max(2, round(1.8 * u)))
        pygame.draw.circle(s, color, pt(15.8, 10.6), max(1, round(1.5 * u)))
        pygame.draw.circle(s, color, pt(18.1, 13.3), max(1, round(1.5 * u)))
    elif name == "gear":
        cx, cy = 12.0 * u, 12.0 * u
        for i in range(8):
            a = math.pi / 4 * i
            pygame.draw.line(s, color,
                             (cx + math.cos(a) * 6.0 * u, cy + math.sin(a) * 6.0 * u),
                             (cx + math.cos(a) * 9.4 * u, cy + math.sin(a) * 9.4 * u),
                             max(2, round(2.6 * u)))
        pygame.draw.circle(s, color, (cx, cy), 6.2 * u)
        pygame.draw.circle(s, (0, 0, 0, 0), (cx, cy), 2.9 * u)
    elif name == "power":
        rect = pygame.Rect(round(4 * u), round(4 * u), round(16 * u), round(16 * u))
        pygame.draw.arc(s, color, rect, math.radians(52), math.radians(308), max(2, round(2.4 * u)))
        pygame.draw.line(s, color, pt(12, 3.2), pt(12, 11.2), max(2, round(2.6 * u)))
    _ICON_CACHE[key] = s
    return s


class Button:
    def __init__(self, text, rect, color, hover_color, text_color, font,
                 style="classic", accent=None, icon=None):
        self.text = text
        self.rect = pygame.Rect(rect)
        self.color = color
        self.hover_color = hover_color
        self.text_color = text_color
        self.font = font
        self.style = style
        self.accent = accent or (100, 220, 255)
        self.icon = icon
        self.hover = False
        self.original_rect = self.rect.copy()
        if style == "primary":
            self.radius = 16
        elif style in ("home", "home_primary"):
            self.radius = 18
        elif style == "ghost":
            self.radius = 10
        else:
            self.radius = 12
        self._surf_normal = self._build_surface(False)
        self._surf_hover = self._build_surface(True)
        self._shadow = self._build_shadow()
        self._glow = self._build_glow()
        self._icon_normal = _make_icon(icon, self._palette(False)["icon"], 22) if icon else None
        self._icon_hover = _make_icon(icon, self._palette(True)["icon"], 22) if icon else None

    def _palette(self, hover):
        if self.style == "home_primary":
            return {
                "top": (255, 255, 255) if hover else (250, 254, 255),
                "bottom": (224, 248, 251) if hover else (216, 243, 247),
                "border": (255, 255, 255), "border_w": 3, "alpha": 246,
                "text": (8, 66, 84),
                "icon": (0, 148, 178),
            }
        if self.style == "home":
            return {
                "top": (255, 255, 255) if hover else (252, 254, 255),
                "bottom": (231, 248, 249) if hover else (223, 244, 246),
                "border": (255, 255, 255), "border_w": 2, "alpha": 236,
                "text": (14, 58, 72) if hover else (20, 66, 80),
                "icon": (44, 112, 130) if hover else (58, 122, 138),
            }
        if self.style == "primary":
            return {
                "top": (124, 242, 250) if hover else (100, 228, 242),
                "bottom": (40, 180, 208) if hover else (28, 158, 190),
                "border": (255, 255, 255) if hover else (222, 250, 252),
                "border_w": 2, "alpha": 255, "text": (6, 52, 64),
            }
        if self.style == "card":
            return {
                "top": (255, 255, 255) if hover else (252, 254, 255),
                "bottom": (234, 249, 252) if hover else (243, 251, 254),
                "border": (88, 196, 218) if hover else (162, 218, 232),
                "border_w": 1, "alpha": 238,
                "text": (8, 64, 84) if hover else (14, 76, 94),
            }
        if self.style == "ghost":
            return {
                "top": (255, 247, 248) if hover else (252, 253, 255),
                "bottom": (255, 234, 237) if hover else (246, 250, 252),
                "border": (228, 150, 158) if hover else (198, 220, 230),
                "border_w": 1, "alpha": 228 if hover else 202,
                "text": (178, 74, 86) if hover else (96, 128, 142),
            }
        return {
            "top": (255, 255, 255) if hover else (250, 253, 255),
            "bottom": (230, 248, 252) if hover else (240, 250, 253),
            "border": (80, 190, 214) if hover else (162, 218, 232),
            "border_w": 1, "alpha": 238, "text": (14, 74, 92),
        }

    def _build_surface(self, hover):
        pal = self._palette(hover)
        w, h = self.original_rect.size
        surf = _rounded_gradient((w, h), self.radius, pal["top"], pal["bottom"],
                                pal["alpha"])
        tex_alpha = {"primary": 24, "card": 10, "ghost": 8}.get(self.style, 12)
        tex = _load_button_texture()
        if tex is not None:
            tex = pygame.transform.smoothscale(tex, (w, h))
            tex.set_alpha(tex_alpha)
            # 用圆角遮罩裁切，避免纹理溢出按钮圆角
            mask = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h), 0, self.radius)
            tex.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            surf.blit(tex, (0, 0))
        pygame.draw.rect(surf, pal["border"], (0, 0, w, h), pal["border_w"], self.radius)
        if self.style == "primary":
            pygame.draw.line(surf, (255, 255, 255, 95),
                             (self.radius, 1), (w - self.radius, 1), 1)
        if self.style in ("home", "home_primary"):
            # 玻璃高光：上半部白亮 + 内描边，贴近卡通气泡按钮质感
            gloss = pygame.Surface((max(1, w - 10), max(1, h // 2)), pygame.SRCALPHA)
            pygame.draw.rect(gloss, (255, 255, 255, 78),
                             (0, 0, gloss.get_width(), gloss.get_height()), 0, self.radius - 4)
            surf.blit(gloss, (5, 3))
            pygame.draw.rect(surf, (186, 226, 232, 120),
                             (3, 3, w - 6, h - 6), 1, self.radius - 3)
        return surf

    def _build_shadow(self):
        w, h = self.original_rect.size
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (24, 96, 116, 45), (0, 0, w, h), 0, self.radius)
        return surf

    def _build_glow(self):
        spread = 14
        w, h = self.original_rect.size
        surf = pygame.Surface((w + spread * 2, h + spread * 2), pygame.SRCALPHA)
        for i in range(spread, 0, -1):
            a = int(70 * ((spread - i) / spread) ** 1.5)
            pygame.draw.rect(surf, (*self.accent, a),
                             (spread - i, spread - i, w + i * 2, h + i * 2),
                             0, self.radius + i)
        return surf

    def update(self, mouse_pos):
        self.hover = self.original_rect.collidepoint(mouse_pos)

    def draw(self, screen):
        lift = 2 if self.hover else 0
        screen.blit(self._shadow, (self.rect.x, self.rect.y + 4))
        if self.hover or self.style in ("primary", "home_primary"):
            screen.blit(self._glow, self._glow.get_rect(
                center=(self.rect.centerx, self.rect.centery - lift)))
        surf = self._surf_hover if self.hover else self._surf_normal
        screen.blit(surf, (self.rect.x, self.rect.y - lift))
        color = self._palette(self.hover)["text"]
        txt = render_text(self.font, self.text, color)
        shift = 14 if self.icon else 0
        screen.blit(txt, txt.get_rect(
            center=(self.rect.centerx + shift, self.rect.centery - lift)))
        if self.icon:
            icon = self._icon_hover if self.hover else self._icon_normal
            screen.blit(icon, icon.get_rect(
                center=(self.rect.centerx - txt.get_width() // 2 - 4,
                        self.rect.centery - lift)))

    def is_clicked(self, event):
        return event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.hover

class Slider:
    """音量滑动条"""
    def __init__(self, x, y, width, height, min_val=0.0, max_val=1.0, initial_val=0.5, color=CYAN, bg_color=(204, 228, 235)):
        self.rect = pygame.Rect(x, y, width, height)
        self.min_val = min_val
        self.max_val = max_val
        self.value = initial_val
        self.color = color
        self.bg_color = bg_color
        self.dragging = False
        self.knob_radius = height // 2 + 2

    def update(self, mouse_pos):
        if self.dragging:
            rel_x = max(0, min(self.rect.width, mouse_pos[0] - self.rect.x))
            self.value = self.min_val + (rel_x / self.rect.width) * (self.max_val - self.min_val)

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.dragging = True
                rel_x = max(0, min(self.rect.width, event.pos[0] - self.rect.x))
                self.value = self.min_val + (rel_x / self.rect.width) * (self.max_val - self.min_val)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False

    def draw(self, screen, font):
        # 背景槽
        pygame.draw.rect(screen, self.bg_color, self.rect, 0, 6)
        # 填充部分
        fill_width = int((self.value - self.min_val) / (self.max_val - self.min_val) * self.rect.width)
        if fill_width > 0:
            fill_rect = pygame.Rect(self.rect.x, self.rect.y, fill_width, self.rect.height)
            pygame.draw.rect(screen, self.color, fill_rect, 0, 6)
        # 滑块圆钮
        knob_x = self.rect.x + fill_width
        knob_y = self.rect.centery
        pygame.draw.circle(screen, (255, 255, 255), (knob_x, knob_y), self.knob_radius)
        pygame.draw.circle(screen, (90, 180, 200), (knob_x, knob_y), self.knob_radius, 1)
        # 显示百分比
        percent_text = render_text(font, f"{int(self.value * 100)}%", (16, 78, 96))
        screen.blit(percent_text, (self.rect.x + self.rect.width + 10, self.rect.centery - percent_text.get_height()//2))


# ---------------- 统一暂停 / 结算 / 退出弹层 ----------------

_MODAL_BG_CACHE = {}
_MODAL_PANEL_CACHE = {}
_MODAL_SHADOW_CACHE = {}


def get_modal_backdrop(width, height):
    """全屏暗幕 + 中心柔光（只生成一次，按窗口尺寸缓存）"""
    key = (width, height)
    surf = _MODAL_BG_CACHE.get(key)
    if surf is None:
        surf = pygame.Surface((width, height), pygame.SRCALPHA)
        surf.fill((2, 5, 14, 198))
        radius = int(math.hypot(width, height) * 0.62)
        glow = make_radial_glow(radius, (26, 70, 122), 46)
        surf.blit(glow, (width // 2 - radius, height // 2 - radius),
                  special_flags=pygame.BLEND_RGBA_ADD)
        _MODAL_BG_CACHE[key] = surf
    return surf


def draw_modal_backdrop(screen, width, height, alpha=1.0):
    """绘制淡入淡出的暗幕"""
    if alpha <= 0.0:
        return
    surf = get_modal_backdrop(width, height)
    surf.set_alpha(max(0, min(255, int(255 * alpha))))
    screen.blit(surf, (0, 0))


def _get_modal_panel(size, radius, accent):
    """玻璃面板：竖向渐变 + 双层描边 + 顶部渐变高光 + 四角装饰（缓存）"""
    key = (size, radius, tuple(accent))
    surf = _MODAL_PANEL_CACHE.get(key)
    if surf is not None:
        return surf
    w, h = int(size[0]), int(size[1])
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        c = _lerp_color((24, 36, 66), (8, 12, 26), y / max(1, h - 1))
        pygame.draw.line(surf, (*c, 242), (0, y), (w, y))
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h), 0, radius)
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    pygame.draw.rect(surf, (*accent, 145), (0, 0, w, h), 1, radius)
    pygame.draw.rect(surf, (255, 255, 255, 28), (2, 2, w - 4, h - 4), 1,
                     max(2, radius - 2))
    # 顶部渐变高光条
    line = pygame.Surface((w, 2), pygame.SRCALPHA)
    for x in range(w):
        t = abs(x - w / 2.0) / max(1.0, w / 2.0)
        a = int(165 * (1.0 - t) ** 1.5)
        line.set_at((x, 0), (*accent, a))
        line.set_at((x, 1), (*accent, max(0, a - 60)))
    surf.blit(line, (0, 1))
    # 四角装饰短线
    length, off = 18, 12
    for x, y, dx, dy in ((off, off, 1, 1), (w - off, off, -1, 1),
                         (off, h - off, 1, -1), (w - off, h - off, -1, -1)):
        pygame.draw.line(surf, (*accent, 115), (x, y), (x + dx * length, y), 2)
        pygame.draw.line(surf, (*accent, 115), (x, y), (x, y + dy * length), 2)
    _MODAL_PANEL_CACHE[key] = surf
    return surf


def _get_modal_shadow(size, radius):
    """面板柔和投影（缓存）"""
    key = (size, radius)
    surf = _MODAL_SHADOW_CACHE.get(key)
    if surf is not None:
        return surf
    w, h = int(size[0]), int(size[1])
    pad = 24
    surf = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)
    for i in range(12, 0, -1):
        a = int(4 + 4 * (12 - i) / 12.0)
        pygame.draw.rect(surf, (0, 0, 0, a),
                         (pad - i, pad - i + 6, w + i * 2, h + i * 2),
                         0, radius + i)
    _MODAL_SHADOW_CACHE[key] = surf
    return surf


def make_modal_buttons(items, center_x, top, width, height, gap, font):
    """生成垂直排列的弹层按钮（items: [(文本, 样式)]）"""
    buttons = []
    y = top
    for text, style in items:
        buttons.append(Button(text, (center_x - width // 2, y, width, height),
                              DEEP_BLUE, (30, 30, 60), WHITE, font, style=style))
        y += height + gap
    return buttons


class ModalTransition:
    """弹层显隐过渡：展开 170ms 淡入上浮，收起 140ms 淡出"""

    __slots__ = ("open_ms", "close_ms", "_t0", "_close_t0", "_close_from")

    def __init__(self, open_ms=170, close_ms=140):
        self.open_ms = max(1, int(open_ms))
        self.close_ms = max(1, int(close_ms))
        self._t0 = -1
        self._close_t0 = -1
        self._close_from = 0.0

    def open(self):
        if self._t0 >= 0 and self._close_t0 < 0:
            return
        self._t0 = pygame.time.get_ticks()
        self._close_t0 = -1

    def close(self):
        if self._t0 < 0 or self._close_t0 >= 0:
            return
        self._close_from = self._raw_alpha()
        self._close_t0 = pygame.time.get_ticks()

    def reset(self):
        self._t0 = -1
        self._close_t0 = -1
        self._close_from = 0.0

    def _raw_alpha(self):
        now = pygame.time.get_ticks()
        if self._close_t0 >= 0:
            k = (now - self._close_t0) / self.close_ms
            if k >= 1.0:
                return 0.0
            return max(0.0, self._close_from * (1.0 - k))
        if self._t0 < 0:
            return 0.0
        k = (now - self._t0) / self.open_ms
        return 1.0 if k >= 1.0 else max(0.0, k)

    def alpha(self):
        x = self._raw_alpha()
        return 1.0 - (1.0 - x) ** 3

    def rise(self):
        """淡入时自下而上的轻微位移"""
        return int(round(14 * (1.0 - self.alpha())))

    def visible(self):
        return self.alpha() > 0.01

    def ready(self):
        return self.alpha() >= 0.99


class ModalOverlay:
    """统一的暂停 / 结算 / 退出确认弹层。

    只负责绘制与显隐动画；交互仍由调用方遍历 buttons 按文本派发，
    与原有按钮逻辑保持一致。
    """

    def __init__(self, size, title, title_font, body_font, button_font,
                 items, min_width=380, button_width=220, button_height=46,
                 button_gap=12, accent=(100, 220, 255),
                 title_colors=((225, 255, 255), (55, 185, 255)),
                 body_color=(188, 214, 236), open_ms=170, close_ms=140):
        self.size = (int(size[0]), int(size[1]))
        self.title_font = title_font
        self.body_font = body_font
        self.button_font = button_font
        # 弹层画在 1920 画布上，布局常量统一按画布放大
        self.button_width = int(button_width * UI_SCALE)
        self.button_height = int(button_height * UI_SCALE)
        self.button_gap = int(button_gap * UI_SCALE)
        self.min_width = int(min_width * UI_SCALE)
        self.accent = tuple(accent)
        self.body_color = body_color
        self._title = title
        self._title_colors = tuple(title_colors)
        self._subtitle = ""
        self._title_key = None
        self._panel_key = None
        self._title_surf = None
        self._title_glow = None
        self._sub_surfs = []
        self._sub_positions = []
        self._card = pygame.Rect(0, 0, 10, 10)
        self._title_y = 0
        self._buttons_top = 0
        self._layer = None
        self.pad_top = int(32 * UI_SCALE)
        self.pad_bottom = int(30 * UI_SCALE)
        self.buttons = [
            Button(text, (0, 0, self.button_width, self.button_height),
                   DEEP_BLUE, (30, 30, 60), WHITE, button_font, style=style)
            for text, style in items
        ]
        self.anim = ModalTransition(open_ms, close_ms)
        self.active = False
        self._layout()

    # ---- 内容 ----

    def set_title(self, text, title_colors=None):
        colors = tuple(title_colors) if title_colors is not None else self._title_colors
        if (text == self._title and colors == self._title_colors
                and self._title_surf is not None):
            return
        self._title = text
        self._title_colors = colors
        self._layout()

    def set_subtitle(self, text):
        text = text or ""
        if text == self._subtitle:
            return
        self._subtitle = text
        self._layout()

    # ---- 布局 ----

    def _layout(self):
        w, h = self.size
        tkey = (self._title, self._title_colors)
        if tkey != self._title_key:
            top, bottom = self._title_colors
            self._title_surf = render_gradient_text(self.title_font, self._title,
                                                    top, bottom)
            self._title_glow = make_text_glow(self.title_font, self._title,
                                              bottom, spread=26, alpha=115)
            self._title_key = tkey
        lines = self._subtitle.split("\n") if self._subtitle else []
        self._sub_surfs = [render_text(self.body_font, ln, self.body_color)
                           for ln in lines]
        title_w, title_h = self._title_surf.get_size()
        sub_w = max([s.get_width() for s in self._sub_surfs] or [0])
        content_w = max(title_w, sub_w, self.button_width if self.buttons else 0)
        card_w = max(self.min_width, content_w + 76)
        line_gap = 5
        sub_h = (sum(s.get_height() for s in self._sub_surfs)
                 + line_gap * max(0, len(self._sub_surfs) - 1))
        buttons_h = (sum(b._surf_normal.get_height() for b in self.buttons)
                     + self.button_gap * max(0, len(self.buttons) - 1))
        card_h = (self.pad_top + title_h + 12 + sub_h
                  + (20 if self._sub_surfs else 0)
                  + buttons_h + self.pad_bottom)
        card = pygame.Rect(0, 0, card_w, card_h)
        card.center = (w // 2, h // 2)
        self._card = card
        self._title_y = card.y + self.pad_top + title_h // 2
        y = card.y + self.pad_top + title_h + 12
        self._sub_positions = []
        for s in self._sub_surfs:
            self._sub_positions.append((w // 2 - s.get_width() // 2, y))
            y += s.get_height() + line_gap
        if self._sub_surfs:
            y += 16
        self._buttons_top = y
        for b in self.buttons:
            bw, bh = b._surf_normal.get_size()   # 按钮底纹按文字自适应，这里贴合其真实尺寸居中
            b.rect = pygame.Rect(w // 2 - bw // 2, y, bw, bh)
            b.original_rect = b.rect.copy()
            y += bh + self.button_gap
        pkey = (card.size, self.accent)
        if pkey != self._panel_key:
            self._panel = _get_modal_panel(card.size, 20, self.accent)
            self._shadow = _get_modal_shadow(card.size, 20)
            self._panel_key = pkey

    # ---- 显隐 ----

    def open(self):
        if not self.active:
            self.active = True
            self.anim.open()

    def close(self):
        if self.active:
            self.active = False
            self.anim.close()

    def hide_now(self):
        self.active = False
        self.anim.reset()

    def sync(self, visible):
        """按调用方的界面状态同步显隐，返回是否仍需绘制"""
        if visible:
            self.open()
        else:
            self.close()
        return self.is_visible()

    def is_visible(self):
        return self.anim.visible()

    def _canvas_pos(self, pos):
        """960 逻辑坐标 → 弹层绘制画布坐标。

        事件坐标在 main 的事件循环里已折算成 960×540 逻辑坐标，而弹层按钮
        是按当前画布（1920×1080 / 2048×1152，即 UI_SCALE 放大后）布版的，
        直接用事件坐标判定会整体偏到左上、点按钮没反应。
        """
        w, h = self.size
        return (pos[0] * w / 960.0, pos[1] * h / 540.0)

    def clicked(self, event):
        """返回被点击按钮的文本；动画未完成时忽略点击"""
        if not self.active or not self.anim.ready():
            return None
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return None
        pos = self._canvas_pos(event.pos)
        for b in self.buttons:
            b.update(pos)
            if b.is_clicked(event):
                return b.text
        return None

    # ---- 绘制 ----

    def draw(self, screen):
        a = self.anim.alpha()
        if a <= 0.002:
            return
        draw_modal_backdrop(screen, self.size[0], self.size[1], a)
        if self._layer is None:
            self._layer = pygame.Surface(self.size, pygame.SRCALPHA)
        layer = self._layer
        layer.fill((0, 0, 0, 0))
        card = self._card
        layer.blit(self._shadow, (card.x - 24, card.y - 18))
        layer.blit(self._panel, card.topleft)
        # 顶部高光随弹层出现缓慢流动，避免面板显得死板
        phase = (pygame.time.get_ticks() % 2600) / 2600.0
        hx = card.x + 18 + int((card.w - 36) * phase)
        pygame.draw.line(layer, (*self.accent, 130),
                         (max(card.x + 8, hx - 34), card.y + 1),
                         (min(card.right - 8, hx + 34), card.y + 1), 2)
        cx = self.size[0] // 2
        layer.blit(self._title_glow,
                   self._title_glow.get_rect(center=(cx, self._title_y)))
        layer.blit(self._title_surf,
                   self._title_surf.get_rect(center=(cx, self._title_y)))
        for s, pos in zip(self._sub_surfs, self._sub_positions):
            layer.blit(s, pos)
        mouse_pos = self._canvas_pos(to_logical_pos(pygame.mouse.get_pos()))
        for b in self.buttons:
            b.update(mouse_pos)
            b.draw(layer)
        layer.set_alpha(int(255 * a))
        screen.blit(layer, (0, self.anim.rise()))


class QuitConfirm:
    """退出游戏二次确认弹层"""

    def __init__(self, size, title_font, body_font, button_font, on_quit,
                 accent=(255, 118, 118), min_width=340):
        self.ui = ModalOverlay(
            size, "退出游戏", title_font, body_font, button_font,
            [("取消", "primary"), ("退出游戏", "ghost")],
            min_width=min_width, button_width=200,
            accent=accent, title_colors=((255, 232, 228), (255, 96, 96)),
            body_color=(240, 205, 205))
        self.ui.set_subtitle("确定要退出游戏吗？")
        self.on_quit = on_quit
        self.active = False

    def open(self):
        self.active = True
        self.ui.open()

    def close(self):
        self.active = False
        self.ui.close()

    def is_visible(self):
        return self.ui.is_visible()

    def draw(self, screen):
        self.ui.draw(screen)

    def handle_event(self, event):
        """激活时吞掉所有输入，避免穿透到下层界面"""
        if not self.active:
            return False
        text = self.ui.clicked(event)
        if text == "取消":
            self.close()
        elif text == "退出游戏":
            self.on_quit()
        elif event.type == pygame.KEYDOWN and key_is("menu", event.key):
            self.close()
        return True