import pygame
import sys
import os
import math
import subprocess
from game import SnakeGame
from ai_mode import AISnakeGame
from versus_mode import VersusGame
from netbattle_mode import NetBattleGame
from network import NetHost, NetClient, get_local_ip, make_room_code, parse_join_target
from ui import Button, Slider, QuitConfirm, create_menu_background
import menu_art
import home_bg
from config import *
from utils import get_chinese_font, create_history_surface, create_leaderboard_surface, \
    resource_path, load_settings, save_settings, sound_volume, key_name, render_text, \
    create_display, toggle_fullscreen, to_logical_pos, sync_sky_sens

# 菜单配色：青色调 · 清新 · 简约
MENU_INK = (16, 78, 96)          # 主文字（深青）
MENU_TITLE = (8, 108, 130)       # 标题
MENU_SUB = (64, 134, 154)        # 次要说明
MENU_MUTED = (108, 158, 176)     # 弱提示
MENU_LINE = (168, 214, 228)      # 分隔线 / 描边

# 首页右下角爱心 → 操作说明（960 逻辑坐标；首页画布为 2048x1152 时鼠标已换算回 960）
HOME_HELP_RECT = pygame.Rect(846, 424, 96, 100)


def _build_key_buttons(font):
    cx = _DESIGN_W // 2
    buttons = []
    for i, action in enumerate(ACTION_NAMES):
        text = f"{ACTION_DISPLAY[action]}：{key_name(KEY_BINDINGS[action])}"
        buttons.append(Button(text, (cx - 160, 132 + i * 42, 320, 36),
                              DEEP_BLUE, (30, 30, 60), WHITE, font))
    return buttons


def _build_sliders():
    rows = [("master", "主音量"), ("eat", "吃食音效"), ("death", "死亡音效"),
            ("bgm", "背景音乐"), ("menu", "菜单音乐")]
    sliders = {}
    for i, (key, _label) in enumerate(rows):
        sliders[key] = Slider(400, 126 + i * 30, 250, 16, 0.0, 1.0, SETTINGS[key], CYAN)
    return sliders


def _fps_text(value):
    """帧率选项的显示文本（0 = 不限帧）"""
    return "不限帧" if not value else f"{value} FPS"


def _sens_text(value):
    """天空贪吃蛇鼠标灵敏度的显示文本（按档位显示数值）"""
    try:
        v = float(value)
    except Exception:
        v = 1.6
    return f"{v:.1f}"


def _build_menu_art(game):
    """菜单系页面共用底图（首页使用动态视频，不依赖此资源）"""
    return {"bg": create_menu_background(_DESIGN_W, _DESIGN_H)}


def _draw_wait_banner(screen, room_code, cx, cy):
    """联机等待中：在游戏画面上显示房间码与等待提示"""
    font = get_chinese_font(22)
    surf = render_text(font, f"房间码 {room_code}  ·  等待对方加入…", (210, 240, 255))
    w, h = surf.get_width() + 44, surf.get_height() + 20
    rect = pygame.Rect(cx - w // 2, cy - h // 2, w, h)
    panel = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(panel, (8, 16, 32, 210), (0, 0, w, h), 0, 12)
    pygame.draw.rect(panel, (80, 190, 235, 140), (0, 0, w, h), 1, 12)
    pygame.draw.circle(panel, (120, 230, 160), (17, h // 2), 5)
    screen.blit(panel, rect.topleft)
    screen.blit(surf, (rect.x + 30, rect.y + (h - surf.get_height()) // 2))


def _draw_main_menu(game, art, mouse):
    """首页：2048x1152 原画 1:1 直出 + 实时动效，按钮为画面原有位置的透明热区"""
    s = game.screen
    scene = game.home_scene
    now = pygame.time.get_ticks()
    scene.update()
    scene.draw(s, now)

    for btn in game.btns_main:
        btn.update(mouse)
        if btn.hover:
            scene.draw_hover(s, btn.rect, now)
    game.btn_more_main.update(mouse)


def _draw_help_overlay(screen, help_scroll):
    """首页操作说明弹层：按 2048x1152 画布原生绘制（字号放大，文字不经过位图放大）"""
    _k = home_bg.k
    _kf = home_bg.kf
    W, H = screen.get_size()

    overlay = pygame.Surface((W, H), pygame.SRCALPHA)
    overlay.fill((236, 250, 254, 205))
    screen.blit(overlay, (0, 0))

    help_rect = pygame.Rect(_k(100), _k(14), _k(760), _k(512))
    pygame.draw.rect(screen, (255, 255, 255), help_rect, 0, _k(15))
    pygame.draw.rect(screen, (120, 196, 216), help_rect, max(1, _k(2)), _k(15))

    font_title = get_chinese_font(_kf(32))
    font_content = get_chinese_font(_kf(20))

    title_help = render_text(font_title, "操作说明", MENU_TITLE)
    screen.blit(title_help, title_help.get_rect(
        center=(help_rect.centerx, help_rect.y + _k(28))))

    lines = [
                        "【基本操作】",
                        "方向键 / WASD 控制移动（可在设置中重绑定）",
                        "P 键 暂停/恢复    |    ESC 打开暂停菜单",
                        "鼠标点击游戏画面也可暂停/恢复",
                        "",
                        "【加速机制】",
                        "长按与蛇头同向的方向键 → 蛇加速移动",
                        "每加速移动 10 格，蛇身缩短 1 格，同时得分 -1",
                        "",
                        "【得分与道具（对战）】",
                        "• 红色普通食物：+1 分",
                        "• 金色特殊食物：+5 分（5 秒后消失）",
                        "• 品红菱形 双倍得分：10 秒内得分翻倍",
                        "• 青色菱形 穿身护盾：5 秒内可穿过对方蛇身",
                        "",
                        "【对战规则】（人机/联机）",
                        "• 地图含随机障碍物，撞上即死亡（已校验连通性）",
                        "• 双方对角出生；前 10 步每步长 1 格，之后每 3 步长 1 格",
                        "• 房间可由房主设置速度/障碍/食物数/限时",
                        "• 限时模式时间到按得分判定胜负",
                        "• 联机对战自动记录战绩与对局明细",
                        "",
                        "【菜单导航】",
                        "• 首页按游戏类型归类：天空贪吃蛇 / 经典贪吃蛇 / 设置 / 退出游戏",
                        "• 天空贪吃蛇为首位主推（3D），其下含单人、人机、联机",
                        "• 一级类型 → 二级模式入口 → 三级难度或房间配置，ESC 逐级返回",
                        "",
                        "【性能与显示】",
                        "• 天空贪吃蛇（3D）游戏画面左下角实时显示 FPS 帧率",
                        "• 启动时自动检测 GPU：有硬件加速用 GPU 渲染，无 GPU 时回退 CPU",
                        "• 设置页可切换 2D 模式最大帧率（60～240 FPS / 不限帧），立即生效并自动保存",
                        "• 窗口为 16:9，可拖动边缘自由缩放；按 F11 切换全屏",
                        "",
                        "【游戏模式】",
                        "• 单人模式：经典贪吃蛇，撞墙或撞自身结束",
                        "• 人机对战：玩家与 AI 同场竞技（可选限时）",
                        "• 联机对战：房主创建房间获得房间码，",
                        "  对方输入房间码或 IP 加入；ESC 为离开对战",
                        "• 天空贪吃蛇（3D 立体天空世界）：蛇自动前进，",
                        "  按住鼠标左键滑动选择方向，支持 360° 平滑转向；",
                        "  滚轮缩放视角，按住右键拖动环绕视角；P 暂停，ESC 菜单",
                        "  天空安全，陆地与石墙触碰即死亡；吃经验果实升级加长",
                        "",
                        "【天空对战】（3D 联机，HP 制）",
                        "• 双方各 100 HP：Q 冲击波 / E 闪避 / R 护盾；Shift 冲刺、S/↓ 减速拉扯",
                        "• 无时间限制，任意一方 HP 归零立即结算胜负",
                        "• 战斗命中与拾取经验均可升级：每级 +3 攻击 / +10 生命上限",
                        "",
                        "【天空人机】（3D，HP 制）",
                        "• 与 AI 对手在同一片 3D 天空世界对决，规则同天空对战",
                        "• AI 会采集果实/光环升级，追击时预判走位，并闪避光波、低血量开盾",
                        "• 难度影响 AI 的反应速度、转向、瞄准精度与技能频率",
                        "",
                        "【个性化】",
                        "设置中可调整音量、按键、昵称与蛇皮肤",
                        "",
                        "提示：历史成绩与排行榜可查看最近得分和联机明细",
    ]

    content_start_y = _k(62)
    content_height = _k(424) - _k(24)   # 留出底部“点击任意处关闭”提示的位置
    clip_rect = pygame.Rect(help_rect.x + _k(10), help_rect.y + content_start_y,
                            help_rect.width - _k(20), content_height)
    old_clip = screen.get_clip()
    screen.set_clip(clip_rect)

    line_height = _k(26)
    total_content_height = len(lines) * line_height
    max_scroll = max(0, total_content_height - content_height)
    help_scroll = max(0, min(help_scroll, max_scroll))

    y = help_rect.y + content_start_y + _k(5) - help_scroll
    for line in lines:
        txt = render_text(font_content, line, MENU_INK)
        screen.blit(txt, (help_rect.x + _k(30), y))
        y += line_height

    screen.set_clip(old_clip)

    if max_scroll > 0:
        bar_x = help_rect.right - _k(16)
        bar_y_start = help_rect.y + content_start_y + _k(5)
        bar_height = content_height - _k(10)
        thumb_height = max(_k(30), bar_height * (content_height / total_content_height))
        thumb_y = bar_y_start + (help_scroll / max_scroll) * (bar_height - thumb_height)
        pygame.draw.rect(screen, (160, 200, 212),
                         (bar_x, bar_y_start, _k(6), bar_height), max(1, _k(2)))
        pygame.draw.rect(screen, (70, 160, 182), (bar_x, thumb_y, _k(6), thumb_height))

    hint = render_text(font_content, "点击任意处关闭  |  滚轮滚动", MENU_MUTED)
    screen.blit(hint, hint.get_rect(
        center=(help_rect.centerx, help_rect.bottom - _k(18))))

    return help_scroll


_DESIGN_W, _DESIGN_H = 960, 540          # 表单/联机等页面的设计稿坐标空间
_DESIGN_SIZE = (_DESIGN_W, _DESIGN_H)
_LO_BUF = {}


def _lo_layer(real):
    """非高清页面用的 960×540 离屏层（复用同一张，避免每帧分配）"""
    buf = _LO_BUF.get("buf")
    if buf is None or buf.get_size() != _DESIGN_SIZE or buf.get_flags() & 0:
        try:
            buf = pygame.Surface(_DESIGN_SIZE).convert()
        except pygame.error:
            buf = pygame.Surface(_DESIGN_SIZE)
        _LO_BUF["buf"] = buf
    return buf


def _present(game, lo, real):
    """统一出帧：若本帧画在设计稿层上，先整数放大贴回画布再翻转"""
    if lo:
        buf = game.screen
        game.screen = real
        real.blit(pygame.transform.smoothscale(buf, real.get_size()), (0, 0))
    pygame.display.flip()


def _menu_backdrop(game):
    """菜单系页面统一背景：青色调清新底图"""
    art = getattr(game, "menu_art", None)
    if art:
        game.screen.blit(art["bg"], (0, 0))
    else:
        game.screen.fill((228, 246, 251))


def _draw_chip(screen, surf, center=None, topleft=None):
    """在插画背景上垫一层磨砂小白底，保证小字可读"""
    pad_x, pad_y = 10, 5
    w = surf.get_width() + pad_x * 2
    h = surf.get_height() + pad_y * 2
    if center:
        rect = pygame.Rect(0, 0, w, h)
        rect.center = center
    else:
        rect = pygame.Rect(topleft[0] - pad_x, topleft[1] - pad_y, w, h)
    chip = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(chip, (255, 255, 255, 178), (0, 0, w, h), 0, h // 2)
    pygame.draw.rect(chip, (255, 255, 255, 225), (0, 0, w, h), 1, h // 2)
    screen.blit(chip, rect.topleft)
    screen.blit(surf, rect.topleft)
    return rect


def _draw_art_page(game, mouse, key, buttons):
    """插画版菜单页：底图按设计稿原样铺满，按钮只作透明热区 + 悬停光效

    高清阶段画布即 2048x1152，底图 1:1 直出；960 阶段仍取缩放版。
    热区与鼠标判定统一留在 960 坐标系，悬停光效由 HomeBackdrop 按画布宽度换算。
    """
    s = game.screen
    art = menu_art.surface_for(key, s.get_size())
    if art is None:
        return False
    s.blit(art, (0, 0))
    menu_art.apply_rects(buttons, key)
    scene = getattr(game, "home_scene", None)
    now = pygame.time.get_ticks()
    for btn in buttons:
        btn.update(mouse)
        if btn.hover and scene is not None:
            scene.draw_hover(s, btn.rect, now)
    pygame.display.flip()
    return True


def _draw_submenu_page(game, mouse, art_key, crumb, title, subtitle, buttons):
    """二级/三级菜单页：有插画底图时原样呈现，否则回退到旧版绘制"""
    if art_key and _draw_art_page(game, mouse, art_key, buttons):
        return

    s = game.screen
    art = getattr(game, "menu_art", None)
    if art:
        s.blit(art["bg"], (0, 0))
    else:
        s.fill((228, 246, 251))

    crumb_surf = render_text(get_chinese_font(14), f"首页  ›  {crumb}", MENU_MUTED)
    _draw_chip(s, crumb_surf, topleft=(22, 18))

    t = render_text(get_chinese_font(44), title, MENU_TITLE)
    s.blit(t, t.get_rect(center=(_DESIGN_W // 2, 88)))
    sub = render_text(get_chinese_font(16), subtitle, MENU_SUB)
    s.blit(sub, sub.get_rect(center=(_DESIGN_W // 2, 126)))

    # 磨砂玻璃容器，让二级菜单按钮不再悬空
    container = pygame.Rect(0, 0, 330, 336)
    container.center = (_DESIGN_W // 2, 300)
    glass = pygame.Surface(container.size, pygame.SRCALPHA)
    pygame.draw.rect(glass, (255, 255, 255, 150), (0, 0, *container.size), 0, 24)
    pygame.draw.rect(glass, (255, 255, 255, 220), (0, 0, *container.size), 2, 24)
    s.blit(glass, container.topleft)

    for btn in buttons:
        btn.update(mouse)
        btn.draw(s)

    back = render_text(get_chinese_font(14), "ESC 返回上一级", MENU_MUTED)
    _draw_chip(s, back, center=(_DESIGN_W // 2, 508))
    pygame.display.flip()


def _draw_versus_diff_page(game, mouse, time_btn, now):
    """人机对战难度页：插画底图 + “限时”实时文字；底图缺失返回 False 由旧版接管"""
    s = game.screen
    art = menu_art.surface_for(menu_art.KEY_DIFF, s.get_size())
    if art is None:
        return False
    s.blit(art, (0, 0))
    menu_art.apply_rects(game.btns_diff_art, menu_art.KEY_DIFF)
    for btn in game.btns_diff_art:
        btn.update(mouse)
        if btn.hover:
            game.home_scene.draw_hover(s, btn.rect, now)
    time_btn.update(mouse)
    if time_btn.hover:
        game.home_scene.draw_hover(s, time_btn.rect, now)
    scale = s.get_width() / 960.0
    lab = menu_art.time_label_surface(time_btn.text.replace(": ", "："), scale)
    center = (int(menu_art.TIME_CENTER[0] * scale), int(menu_art.TIME_CENTER[1] * scale))
    s.blit(lab, lab.get_rect(center=center))
    return True


def _dump_crash(exc):
    """frozen（无控制台）环境下把 3D 子进程的 traceback 落盘，便于排查"""
    import traceback, tempfile
    try:
        p = os.path.join(tempfile.gettempdir(), "sky3d_crash.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write("argv=%s\n" % sys.argv)
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except Exception:
        pass


def main():
    if "--net-host" in sys.argv or "--net-join" in sys.argv:   # 天空联机：3D 子进程入口
        try:
            import sky3d_net_mode
            sky3d_net_mode.main(sys.argv)
        except Exception as e:
            _dump_crash(e)
            raise
        return
    if "--sky3d-ai" in sys.argv:     # 天空人机对战：3D 子进程入口
        try:
            import sky3d_ai_mode
            sky3d_ai_mode.main(sys.argv)
        except Exception as e:
            _dump_crash(e)
            raise
        return
    if "--sky3d" in sys.argv:        # 打包 exe 的 3D 子进程入口：直接运行天空贪吃蛇 3D
        try:
            import sky3d_mode
            sky3d_mode.main()
        except Exception as e:
            _dump_crash(e)
            raise
        return
    os.environ['SDL_VIDEO_WINDOW_POS'] = "center"

    load_settings()

    game = SnakeGame()
    game.menu_art = _build_menu_art(game)
    game.home_scene = home_bg.HomeBackdrop()

    # 调试：START_STATE 环境变量可让程序从指定页面启动（用于截图自查）
    if os.environ.get("START_STATE"):
        game.state = os.environ["START_STATE"]

    # 首页与插画版菜单页使用 2048x1152 高清画布（与设计稿 1:1），其余页面仍是 960x540
    _STAGE = {"hi": False}

    def _switch_hi(hi):
        """切换页面画布尺寸；两者都是 1920×1080 时直接复用窗口，不再重建"""
        target = home_bg.HOME_SIZE if hi else (WINDOW_WIDTH, WINDOW_HEIGHT)
        cur = pygame.display.get_surface()
        if cur is None or cur.get_size() != tuple(target):
            create_display(target)
            game.screen = pygame.display.get_surface()
        _STAGE["hi"] = hi

    def _to_logical(pos):
        """鼠标/事件坐标 → 960 逻辑坐标。

        按当前绘制表面的实际尺寸换算（高清画布 2048 时折 960 比例），
        窗口被拉伸、缩放或切全屏后依然对齐；拿到的若是窗口像素坐标，
        由 to_logical_pos 用窗口尺寸兜底换算。
        """
        return to_logical_pos(pos)

    def _fix_event(event):
        """把事件里的鼠标坐标换算回 960 坐标，后续所有点击判定无需改动"""
        if hasattr(event, "pos"):
            event.pos = _to_logical(event.pos)
        if getattr(event, "rel", None):
            try:
                sw = pygame.display.get_surface().get_width()
            except Exception:
                sw = WINDOW_WIDTH
            k = WINDOW_WIDTH / float(sw or WINDOW_WIDTH)
            event.rel = (int(event.rel[0] * k), int(event.rel[1] * k))
        return event

    ai_game = None
    versus_game = None
    net_game = None
    net_host = None
    waiting_practice = None      # 经典房主等待对手期间的单机练习："classic" / None
    mode = "single"
    settings_parent = "menu"     # 设置页返回目标：首页 / 更多功能
    help_scroll = 0
    ip_text = ""
    net_err = ""
    net_err_time = 0
    room_code = ""
    room_cfg = None
    room_sel = dict(ROOM_DEFAULT)
    versus_time_idx = 0

    # 联机二级页按钮（经典联机 / 天空联机共用，net_kind 决定创建/加入的对象）
    ncx = _DESIGN_W // 2
    btns_net_menu = [
        Button("创建房间", (ncx - 110, 196, 220, 50), DEEP_BLUE, (30, 30, 60), WHITE, game.font28),
        Button("加入房间", (ncx - 110, 264, 220, 50), DEEP_BLUE, (30, 30, 60), WHITE, game.font28),
        Button("返回", (ncx - 110, 342, 220, 46), DEEP_BLUE, (30, 30, 60), WHITE, game.font28),
    ]
    net_kind = "classic"   # classic=经典对战房间  sky=天空对战房间
    btns_net_wait = [
        Button("取消", (ncx - 90, 430, 180, 50), DEEP_BLUE, (30, 30, 60), WHITE, game.font28),
    ]

    # 房主房间参数按钮（点击循环切换）
    btns_room = {
        "speed": Button("标准", (465, 168, 210, 36), DEEP_BLUE, (30, 30, 60), WHITE, game.font28),
        "obstacle": Button("标准", (465, 216, 210, 36), DEEP_BLUE, (30, 30, 60), WHITE, game.font28),
        "food": Button("3 个", (465, 264, 210, 36), DEEP_BLUE, (30, 30, 60), WHITE, game.font28),
        "duration": Button("不限时", (465, 312, 210, 36), DEEP_BLUE, (30, 30, 60), WHITE, game.font28),
    }
    btn_room_create = Button("创建房间", (ncx - 100, 372, 200, 44), DEEP_BLUE, (30, 30, 60), WHITE, game.font28)
    btn_room_back = Button("返回", (ncx - 100, 424, 200, 44), DEEP_BLUE, (30, 30, 60), WHITE, game.font28)

    # 人机对战限时选择（热区对齐难度页底图底部的“限时”文字）
    btn_versus_time = Button("限时: 不限时", menu_art.TIME_RECT,
                             DEEP_BLUE, (20, 40, 60), WHITE, game.font28)

    # 设置界面控件
    settings_sliders = _build_sliders()
    nick_text = SETTINGS["nickname"]
    nick_active = False
    nick_box = pygame.Rect(430, 264, 190, 36)
    btn_skin = Button(f"皮肤: {SKINS[SETTINGS['skin']]['name']}", (430, 302, 190, 36),
                      DEEP_BLUE, (30, 30, 60), WHITE, game.font28)
    btn_fps = Button(_fps_text(SETTINGS.get("fps", RENDER_FPS)), (430, 340, 190, 36),
                     DEEP_BLUE, (30, 30, 60), WHITE, game.font28)
    btn_sens = Button(_sens_text(SETTINGS.get("sky_sens", 3.0)), (430, 378, 190, 36),
                      DEEP_BLUE, (30, 30, 60), WHITE, game.font28)
    btn_keys = Button("按键设置", (330, 420, 150, 36), DEEP_BLUE, (30, 30, 60), WHITE, game.font28)
    btn_restore = Button("恢复默认", (490, 420, 150, 36), DEEP_BLUE, (30, 30, 60), WHITE, game.font28)
    btn_settings_back = Button("保存并返回", (ncx - 100, 472, 200, 38), DEEP_BLUE, (30, 30, 60), WHITE, game.font28)
    btn_settings_title_font = get_chinese_font(48)
    font22 = get_chinese_font(22)

    def _quit_from_main_menu():
        save_settings()
        pygame.quit()
        sys.exit()

    exit_confirm = QuitConfirm((WINDOW_WIDTH, WINDOW_HEIGHT), game.font48,
                               font22, game.font28, _quit_from_main_menu)

    # 按键设置界面
    key_buttons = _build_key_buttons(font22)
    listening_action = None
    btn_key_restore = Button("恢复默认", (330, 448, 150, 40), DEEP_BLUE, (30, 30, 60), WHITE, game.font28)
    btn_key_back = Button("返回设置", (490, 448, 150, 40), DEEP_BLUE, (30, 30, 60), WHITE, game.font28)

    leaderboard_surface = None

    def launch_sky3d_net(args):
        """启动 3D 天空联机子进程；返回后恢复主菜单窗口"""
        pygame.display.iconify()
        frozen = getattr(sys, "frozen", False)
        if frozen:
            cmd = [sys.executable, *args]
            cwd = None
        else:
            cmd = [sys.executable,
                   os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "sky3d_net_mode.py"), *args]
            cwd = os.path.dirname(os.path.abspath(__file__))
        code = subprocess.call(cmd, cwd=cwd)
        os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
        _switch_hi(False)          # 同步阶段标志，返回高清页时由主循环自动切回
        game.screen = pygame.display.get_surface()
        return code

    def launch_sky3d_ai(difficulty):
        """启动 3D 天空人机对战子进程；返回后恢复主菜单窗口"""
        pygame.display.iconify()
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--sky3d-ai", "--ai-diff", difficulty]
            cwd = None
        else:
            cmd = [sys.executable,
                   os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "sky3d_ai_mode.py"), "--ai-diff", difficulty]
            cwd = os.path.dirname(os.path.abspath(__file__))
        code = subprocess.call(cmd, cwd=cwd)
        os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
        _switch_hi(False)          # 同步阶段标志，返回高清页时由主循环自动切回
        game.screen = pygame.display.get_surface()
        return code

    def refresh_room_buttons():
        btns_room["speed"].text = ROOM_SPEEDS[room_sel["speed"]][0]
        btns_room["obstacle"].text = ROOM_OBSTACLES[room_sel["obstacle"]][0]
        btns_room["food"].text = f"{ROOM_FOODS[room_sel['food']]} 个"
        btns_room["duration"].text = ROOM_DURATIONS[room_sel["duration"]][0]

    def apply_settings_live():
        """音量改动立即生效（菜单音乐与单人音效）"""
        try:
            game.eat_sound.set_volume(sound_volume("eat"))
            game.death_sound.set_volume(sound_volume("death"))
            game.bgm.set_volume(sound_volume("bgm"))
        except Exception:
            pass
        if menu_music:
            menu_music.set_volume(sound_volume("menu"))

    # 菜单音乐（使用 resource_path）
    try:
        menu_music = pygame.mixer.Sound(resource_path("assets/audio/menu.wav"))
        menu_music.set_volume(sound_volume("menu"))
    except Exception:
        menu_music = None
    menu_music_playing = False

    MENU_STATES = ("menu", "diff", "versus_diff", "net_menu", "net_host_cfg",
                   "net_join", "net_wait", "settings", "keys", "leaderboard")

    # 走 2048x1152 高清画布的页面：首页 + 五个插画版菜单页（底图本就是 2048x1152）
    HI_STATES = ("menu", "menu_sky", "menu_classic", "diff", "versus_diff",
                 "sky3d_ai_diff")
    _HI_ART_KEY = {
        "menu_sky": menu_art.KEY_SKY,
        "menu_classic": menu_art.KEY_CLASSIC,
        "diff": menu_art.KEY_DIFF_SOLO,
        "versus_diff": menu_art.KEY_DIFF,
        "sky3d_ai_diff": menu_art.KEY_DIFF_SKY,
    }

    def _hi_ready(state):
        """底图齐备才算能上高清画布；素材缺失时留在 960，旧版绘制不会错位"""
        if state == "menu":
            scene = game.home_scene
            if getattr(scene, "video", None) is not None and scene.video.ok:
                return True
            return getattr(scene, "art", None) is not None
        key = _HI_ART_KEY.get(state)
        return key is not None and menu_art.surface_for(key, home_bg.HOME_SIZE) is not None

    while True:
        now = pygame.time.get_ticks()

        # 高清页面用 2048x1152 画布，进入设置/联机/对局等 960 页面时自动切回
        _want_hi = (mode == "single" and game.state in HI_STATES
                    and _hi_ready(game.state))
        if _want_hi != _STAGE["hi"]:
            _switch_hi(_want_hi)

        mouse = _to_logical(pygame.mouse.get_pos())

        if os.environ.get("MOUSE_DEBUG"):
            slot = int(now / 500)
            if slot != _STAGE.get("_dbg_slot"):
                _STAGE["_dbg_slot"] = slot
                surf = pygame.display.get_surface()
                print("DBG state=%s raw=%s logical=%s surface=%s window=%s"
                      % (game.state, pygame.mouse.get_pos(), mouse,
                         surf.get_size() if surf else None,
                         pygame.display.get_window_size()),
                      file=sys.stderr, flush=True)

        # 非高清页面（设置 / 按键 / 联机 / 成绩等）仍按 960×540 设计稿排版：
        # 重定向到离屏层绘制，再整体放大贴回 1920 画布，坐标与热区都不用改
        _lo_used = game.state != "play" and game.state not in HI_STATES
        _lo_real = game.screen
        if _lo_used:
            game.screen = _lo_layer(_lo_real)


        # 菜单音乐控制
        if mode == "single" and game.state in MENU_STATES:
            if menu_music and not menu_music_playing:
                menu_music.play(-1)
                menu_music_playing = True
            if hasattr(game, '_stop_bgm'):
                game._stop_bgm()
        else:
            if menu_music_playing:
                menu_music.stop()
                menu_music_playing = False

        # ---------- 事件处理 ----------
        for event in pygame.event.get():
            _fix_event(event)
            if event.type == pygame.QUIT:
                save_settings()
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                toggle_fullscreen()
                # 切换会重建显示面，屏幕引用要同步，否则后续绘制写到旧表面
                game.screen = pygame.display.get_surface()
                continue

            if exit_confirm.handle_event(event):
                continue

            if mode == "single" and game.state == "menu" and game.show_help:
                if event.type == pygame.MOUSEWHEEL:
                    help_scroll -= event.y * home_bg.k(30)

            if mode == "single":
                if game.state == "menu":
                    if game.show_help:
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 2, 3):
                            game.show_help = False
                            help_scroll = 0
                            continue
                        elif event.type == pygame.KEYDOWN:
                            game.show_help = False
                            help_scroll = 0
                            continue

                    if game.show_history:
                        if event.type == pygame.MOUSEBUTTONDOWN or event.type == pygame.KEYDOWN:
                            game.show_history = False
                            game.history_surface = None
                            continue

                    # 画面右下角爱心 → 操作说明
                    if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                            and HOME_HELP_RECT.collidepoint(event.pos)):
                        game.show_help = True
                        help_scroll = 0
                        continue

                    for btn in game.btns_main:
                        if btn.is_clicked(event):
                            if btn.text == "天空贪吃蛇":
                                game.state = "menu_sky"
                            elif btn.text == "经典贪吃蛇":
                                game.state = "menu_classic"
                            elif btn.text == "设置":
                                settings_parent = "menu"
                                nick_text = SETTINGS["nickname"]
                                nick_active = False
                                for key, sl in settings_sliders.items():
                                    sl.value = SETTINGS[key]
                                btn_skin.text = f"皮肤: {SKINS[SETTINGS['skin']]['name']}"
                                btn_fps.text = _fps_text(SETTINGS.get("fps", RENDER_FPS))
                                btn_sens.text = _sens_text(sync_sky_sens())
                                game.state = "settings"
                            elif btn.text == "退出游戏":
                                exit_confirm.open()
                    if game.btn_more_main.is_clicked(event):
                        game.state = "menu_more"

                elif game.state == "menu_classic":
                    for btn in game.btns_classic:
                        if btn.is_clicked(event):
                            if btn.text == "经典单人":
                                game.state = "diff"
                            elif btn.text == "人机对战":
                                game.state = "versus_diff"
                            elif btn.text == "经典联机":
                                net_kind = "classic"
                                game.state = "net_menu"
                            else:
                                game.state = "menu"
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = "menu"

                elif game.state == "menu_sky":
                    for btn in game.btns_sky:
                        if btn.is_clicked(event):
                            if btn.text == "天空贪吃蛇":
                                pygame.display.iconify()     # 最小化 pygame 窗口，运行 3D 子进程
                                if getattr(sys, "frozen", False):
                                    cmd = [sys.executable, "--sky3d"]
                                else:
                                    cmd = [sys.executable,
                                           os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                        "sky3d_mode.py")]
                                code = subprocess.call(cmd)  # 0=回菜单 42=退全游戏
                                if code == 42:
                                    pygame.quit()
                                    sys.exit()
                                os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
                                _switch_hi(False)   # 3D 子进程返回：先回 960，主循环再按状态切
                                game.screen = pygame.display.get_surface()
                            elif btn.text == "天空人机":
                                game.state = "sky3d_ai_diff"
                            elif btn.text == "天空联机":
                                net_kind = "sky"
                                game.state = "net_menu"
                            else:
                                game.state = "menu"
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = "menu"

                elif game.state == "menu_more":
                    for btn in game.btns_more:
                        if btn.is_clicked(event):
                            if btn.text == "历史成绩":
                                game.state = "menu"
                                game.toggle_history_chart()
                            elif btn.text == "排行榜":
                                leaderboard_surface = create_leaderboard_surface(
                                    _DESIGN_W, _DESIGN_H)
                                game.state = "leaderboard"
                            elif btn.text == "设置":
                                settings_parent = "menu_more"
                                nick_text = SETTINGS["nickname"]
                                nick_active = False
                                for key, sl in settings_sliders.items():
                                    sl.value = SETTINGS[key]
                                btn_skin.text = f"皮肤: {SKINS[SETTINGS['skin']]['name']}"
                                btn_fps.text = _fps_text(SETTINGS.get("fps", RENDER_FPS))
                                btn_sens.text = _sens_text(sync_sky_sens())
                                game.state = "settings"
                            elif btn.text == "操作说明":
                                game.state = "menu"
                                game.show_help = True
                            else:
                                game.state = "menu"
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = "menu"

                elif game.state == "leaderboard":
                    if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                        game.state = "menu_more"

                elif game.state == "settings":
                    if event.type == pygame.KEYDOWN and nick_active:
                        if event.key == pygame.K_BACKSPACE:
                            nick_text = nick_text[:-1]
                        elif event.key == pygame.K_RETURN:
                            nick_active = False
                            SETTINGS["nickname"] = nick_text.strip()[:10] or DEFAULT_NICKNAME
                        elif event.key == pygame.K_ESCAPE:
                            nick_active = False
                            nick_text = SETTINGS["nickname"]
                        elif event.unicode and event.unicode.isprintable() and len(nick_text) < 10:
                            nick_text += event.unicode
                        continue

                    for _key, sl in settings_sliders.items():
                        sl.handle_event(event)

                    if event.type == pygame.MOUSEBUTTONDOWN:
                        if not nick_box.collidepoint(event.pos):
                            nick_active = False
                        if nick_box.collidepoint(event.pos):
                            nick_active = True
                        elif btn_skin.is_clicked(event):
                            SETTINGS["skin"] = (SETTINGS["skin"] + 1) % len(SKINS)
                            btn_skin.text = f"皮肤: {SKINS[SETTINGS['skin']]['name']}"
                        elif btn_fps.is_clicked(event):
                            cur = SETTINGS.get("fps", RENDER_FPS)
                            try:
                                idx = FPS_OPTIONS.index(cur)
                            except ValueError:
                                idx = FPS_OPTIONS.index(RENDER_FPS)
                            SETTINGS["fps"] = FPS_OPTIONS[(idx + 1) % len(FPS_OPTIONS)]
                            btn_fps.text = _fps_text(SETTINGS["fps"])
                        elif btn_sens.is_clicked(event):
                            # 按档位循环：0.6 → 0.8 → … → 3.2 → 回 0.6
                            cur = float(SETTINGS.get("sky_sens", 3.0))
                            steps = int(round((cur - SKY_SENS_MIN) / SKY_SENS_STEP))
                            steps = (steps + 1) % (int(round((SKY_SENS_MAX - SKY_SENS_MIN) / SKY_SENS_STEP)) + 1)
                            SETTINGS["sky_sens"] = round(SKY_SENS_MIN + steps * SKY_SENS_STEP, 2)
                            btn_sens.text = _sens_text(SETTINGS["sky_sens"])
                            save_settings()
                        elif btn_keys.is_clicked(event):
                            key_buttons = _build_key_buttons(font22)
                            listening_action = None
                            game.state = "keys"
                        elif btn_restore.is_clicked(event):
                            SETTINGS.update({"master": MASTER_VOLUME, "eat": EAT_VOLUME,
                                             "death": DEATH_VOLUME, "bgm": BGM_VOLUME,
                                             "menu": MENU_VOLUME, "fps": RENDER_FPS,
                                             "sky_sens": 3.0})
                            btn_fps.text = _fps_text(SETTINGS["fps"])
                            btn_sens.text = _sens_text(SETTINGS["sky_sens"])
                            for key, sl in settings_sliders.items():
                                sl.value = SETTINGS[key]
                            apply_settings_live()
                        elif btn_settings_back.is_clicked(event):
                            SETTINGS["nickname"] = nick_text.strip()[:10] or DEFAULT_NICKNAME
                            save_settings()
                            game.state = settings_parent
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        SETTINGS["nickname"] = nick_text.strip()[:10] or DEFAULT_NICKNAME
                        save_settings()
                        game.state = settings_parent

                elif game.state == "keys":
                    if listening_action is not None:
                        if event.type == pygame.KEYDOWN:
                            KEY_BINDINGS[listening_action] = event.key
                            listening_action = None
                            key_buttons = _build_key_buttons(font22)
                            save_settings()
                        continue

                    if event.type == pygame.MOUSEBUTTONDOWN:
                        for i, btn in enumerate(key_buttons):
                            if btn.is_clicked(event):
                                listening_action = ACTION_NAMES[i]
                                break
                        else:
                            if btn_key_restore.is_clicked(event):
                                for action, key in DEFAULT_KEY_BINDINGS.items():
                                    KEY_BINDINGS[action] = key
                                key_buttons = _build_key_buttons(font22)
                                save_settings()
                            elif btn_key_back.is_clicked(event):
                                game.state = "settings"
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = "settings"

                elif game.state == "diff":
                    for i, btn in enumerate(game.btns_diff_art):
                        if btn.is_clicked(event):
                            if i < 3:
                                game.curr_diff = ["easy", "normal", "hard"][i]
                                game.reset()
                                game.state = "play"
                            else:
                                game.state = "menu_classic"
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = "menu_classic"

                elif game.state == "versus_diff":
                    for i, btn in enumerate(game.btns_diff_art):
                        if btn.is_clicked(event):
                            if i < 3:
                                diff = ["easy", "normal", "hard"][i]
                                duration = VERSUS_DURATIONS[versus_time_idx][1]
                                mode = "versus"
                                os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
                                create_display((VERSUS_WINDOW_WIDTH, VERSUS_WINDOW_HEIGHT))
                                game.screen = pygame.display.get_surface()
                                versus_game = VersusGame(game.screen, difficulty=diff, duration=duration)
                            else:
                                game.state = "menu_classic"
                                os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
                                _switch_hi(False)   # 回菜单页，主循环再切回高清画布
                                game.screen = pygame.display.get_surface()
                    if btn_versus_time.is_clicked(event):
                        versus_time_idx = (versus_time_idx + 1) % len(VERSUS_DURATIONS)
                        btn_versus_time.text = f"限时: {VERSUS_DURATIONS[versus_time_idx][0]}"
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = "menu_classic"

                elif game.state == "sky3d_ai_diff":
                    for i, btn in enumerate(game.btns_diff_art):
                        if btn.is_clicked(event):
                            if i < 3:
                                diff = ["easy", "normal", "hard"][i]
                                code = launch_sky3d_ai(diff)
                                if code == 42:
                                    save_settings()
                                    pygame.quit()
                                    sys.exit()
                                game.state = "menu_sky"
                            else:
                                game.state = "menu_sky"
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = "menu_sky"

                elif game.state == "net_menu":
                    for i, btn in enumerate(btns_net_menu):
                        if btn.is_clicked(event):
                            if i == 0:      # 创建房间：经典走参数配置，天空直接建房
                                net_err = ""
                                if net_kind == "classic":
                                    refresh_room_buttons()
                                    game.state = "net_host_cfg"
                                else:
                                    code = launch_sky3d_net(["--net-host"])
                                    if code == 42:
                                        save_settings()
                                        pygame.quit()
                                        sys.exit()
                            elif i == 1:    # 加入房间
                                ip_text = ""
                                net_err = ""
                                game.state = "net_join"
                            else:           # 返回上一级分类
                                game.state = ("menu_classic" if net_kind == "classic"
                                              else "menu_sky")
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = ("menu_classic" if net_kind == "classic"
                                      else "menu_sky")

                elif game.state == "net_host_cfg":
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        for kind, btn in btns_room.items():
                            if btn.is_clicked(event):
                                opts = {"speed": ROOM_SPEEDS, "obstacle": ROOM_OBSTACLES,
                                        "food": ROOM_FOODS, "duration": ROOM_DURATIONS}[kind]
                                room_sel[kind] = (room_sel[kind] + 1) % len(opts)
                                refresh_room_buttons()
                                break
                        else:
                            if btn_room_create.is_clicked(event):
                                room_cfg = {
                                    "interval": ROOM_SPEEDS[room_sel["speed"]][1],
                                    "obstacles": ROOM_OBSTACLES[room_sel["obstacle"]][1],
                                    "foods": ROOM_FOODS[room_sel["food"]],
                                    "duration": ROOM_DURATIONS[room_sel["duration"]][1],
                                }
                                try:
                                    net_host = NetHost(NET_PORT)
                                    room_code = make_room_code(get_local_ip())
                                    net_err = ""
                                    game.reset()
                                    game.state = "play"
                                    waiting_practice = "classic"
                                except Exception:
                                    net_err = "端口被占用，无法创建房间"
                                    net_err_time = now
                                    if net_host is not None:
                                        net_host.close()
                                        net_host = None
                                    waiting_practice = None
                            elif btn_room_back.is_clicked(event):
                                game.state = "net_menu"
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        game.state = "net_menu"

                elif game.state == "net_wait":
                    cancel = False
                    for btn in btns_net_wait:
                        if btn.is_clicked(event):
                            cancel = True
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        cancel = True
                    if cancel and net_host:
                        net_host.close()
                        net_host = None
                        game.state = "net_menu"

                elif game.state == "net_join":
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            game.state = "net_menu"
                        elif event.key == pygame.K_RETURN:
                            target = parse_join_target(ip_text or "127.0.0.1")
                            if not target:
                                net_err = "房间码或 IP 格式不正确"
                                net_err_time = now
                            else:
                                ip, port = target
                                if net_kind == "sky":
                                    # 天空对战使用 3D 子进程（房间码/IP 直连）
                                    code = launch_sky3d_net(
                                        ["--net-join", ip, str(port),
                                         "--nick", SETTINGS["nickname"]])
                                    if code == 42:
                                        save_settings()
                                        pygame.quit()
                                        sys.exit()
                                    if code != 0:
                                        net_err = "无法连接，请检查房间码/IP 并确认对方已创建房间"
                                        net_err_time = pygame.time.get_ticks()
                                    else:
                                        game.state = "net_menu"
                                else:
                                    try:
                                        net_client = NetClient(ip, port)
                                        mode = "net"
                                        os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
                                        create_display((VERSUS_WINDOW_WIDTH, VERSUS_WINDOW_HEIGHT))
                                        game.screen = pygame.display.get_surface()
                                        net_game = NetBattleGame(game.screen, net_client, False,
                                                                 nickname=SETTINGS["nickname"],
                                                                 skin=SETTINGS["skin"])
                                    except Exception:
                                        net_err = "无法连接，请检查房间码/IP 并确认对方已创建房间"
                                        net_err_time = now
                        elif event.key == pygame.K_BACKSPACE:
                            ip_text = ip_text[:-1]
                        elif event.unicode and event.unicode.isascii() and \
                                (event.unicode.isalnum() or event.unicode in ".:"):
                            if len(ip_text) < 20:
                                ip_text += event.unicode.upper()

                elif game.state == "play":
                    if not game.handle_event(event):
                        game.state = "menu"
                        if waiting_practice == "classic":
                            if net_host is not None:
                                net_host.close()
                                net_host = None
                            waiting_practice = None

            elif mode == "ai":
                if not ai_game.handle_event(event):
                    mode = "single"
                    # 经典贪吃蛇是从二级菜单进来的，返回时回二级菜单而不是主菜单
                    game.state = "menu_classic"

            elif mode == "versus":
                if not versus_game.handle_event(event):
                    mode = "single"
                    game.state = "menu_classic"
                    os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
                    _switch_hi(False)
                    game.screen = pygame.display.get_surface()

            elif mode == "net":
                if not net_game.handle_event(event):
                    mode = "single"
                    game.state = "menu"
                    net_game = None
                    os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
                    _switch_hi(False)
                    game.screen = pygame.display.get_surface()

        # ---------- 等待客户端加入（经典对战：等待期间房主可单机练习） ----------
        if net_host is not None and waiting_practice is not None and net_host.try_accept():
            os.environ['SDL_VIDEO_WINDOW_POS'] = "center"
            try:
                game._stop_bgm()
            except Exception:
                pass
            mode = "net"
            create_display((VERSUS_WINDOW_WIDTH, VERSUS_WINDOW_HEIGHT))
            game.screen = pygame.display.get_surface()
            net_game = NetBattleGame(game.screen, net_host, True,
                                     room_cfg=room_cfg,
                                     nickname=SETTINGS["nickname"],
                                     skin=SETTINGS["skin"])
            waiting_practice = None
            net_host = None

        # ---------- 渲染 ----------
        if mode == "single":
            if game.state == "menu":
                _draw_main_menu(game, game.menu_art, mouse)

                if game.show_help:
                    help_scroll = _draw_help_overlay(game.screen, help_scroll)

                if game.show_history and game.history_surface:
                    surf = game.history_surface
                    if surf.get_size() != game.screen.get_size():
                        surf = game.home_scene.upscale_layer(surf)
                    game.screen.blit(surf, (0, 0))

                if exit_confirm.is_visible():
                    exit_confirm.draw(game.screen)

                _present(game, _lo_used, _lo_real)

            elif game.state == "leaderboard":
                _menu_backdrop(game)

                if leaderboard_surface:
                    game.screen.blit(leaderboard_surface, (0, 0))
                _present(game, _lo_used, _lo_real)

            elif game.state == "settings":
                _menu_backdrop(game)

                panel = pygame.Rect(220, 78, 520, 442)
                pygame.draw.rect(game.screen, (250, 253, 255), panel, 0, 12)
                pygame.draw.rect(game.screen, (130, 200, 218), panel, 2, 12)

                t = render_text(btn_settings_title_font, "设置", MENU_TITLE)
                game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 52)))

                labels = {"master": "主音量", "eat": "吃食音效", "death": "死亡音效",
                          "bgm": "背景音乐", "menu": "菜单音乐"}
                for key, sl in settings_sliders.items():
                    sl.update(mouse)
                    if abs(SETTINGS[key] - sl.value) > 1e-6:
                        SETTINGS[key] = sl.value
                        apply_settings_live()
                    lab = render_text(font22, labels[key], MENU_INK)
                    game.screen.blit(lab, (250, sl.rect.centery - lab.get_height() // 2))
                    sl.draw(game.screen, font22)

                # 昵称
                lab = render_text(font22, "昵称", MENU_INK)
                game.screen.blit(lab, (250, nick_box.y + 8))
                pygame.draw.rect(game.screen, (240, 250, 252), nick_box, 0, 8)
                pygame.draw.rect(game.screen, (60, 180, 205) if nick_active else (176, 206, 218),
                                 nick_box, 2, 8)
                shown = nick_text + ("|" if nick_active and (now // 500) % 2 == 0 else "")
                txt = render_text(game.font28, shown, MENU_INK)
                game.screen.blit(txt, (nick_box.x + 10, nick_box.centery - txt.get_height() // 2))

                # 皮肤
                lab = render_text(font22, "皮肤", MENU_INK)
                game.screen.blit(lab, (250, btn_skin.rect.y + 6))
                skin = SKINS[SETTINGS["skin"]]
                pygame.draw.rect(game.screen, skin["head"], (388, btn_skin.rect.y + 6, 16, 16), 0, 4)
                pygame.draw.rect(game.screen, skin["c2"], (408, btn_skin.rect.y + 6, 16, 16), 0, 4)
                btn_skin.update(mouse)
                btn_skin.draw(game.screen)

                # 最大帧率（点击循环切换）
                lab = render_text(font22, "最大帧率", MENU_INK)
                game.screen.blit(lab, (250, btn_fps.rect.y + 8))
                btn_fps.update(mouse)
                btn_fps.draw(game.screen)

                # 天空贪吃蛇灵敏度（点击循环切换档位）
                lab = render_text(font22, "天空灵敏度", MENU_INK)
                game.screen.blit(lab, (250, btn_sens.rect.y + 8))
                btn_sens.update(mouse)
                btn_sens.draw(game.screen)

                for btn in (btn_keys, btn_restore, btn_settings_back):
                    btn.update(mouse)
                    btn.draw(game.screen)

                hint = render_text(font22, "改动即时生效，自动保存", MENU_MUTED)
                game.screen.blit(hint, hint.get_rect(center=(_DESIGN_W // 2, 112)))
                _present(game, _lo_used, _lo_real)

            elif game.state == "keys":
                _menu_backdrop(game)

                panel = pygame.Rect(220, 86, 520, 430)
                pygame.draw.rect(game.screen, (250, 253, 255), panel, 0, 12)
                pygame.draw.rect(game.screen, (130, 200, 218), panel, 2, 12)

                t = render_text(btn_settings_title_font, "按键设置", MENU_TITLE)
                game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 52)))

                for btn in key_buttons:
                    btn.update(mouse)
                    if listening_action and btn.text.startswith(ACTION_DISPLAY[listening_action]):
                        pygame.draw.rect(game.screen, MAGENTA, btn.rect.inflate(6, 6), 2, 14)
                    btn.draw(game.screen)

                if listening_action:
                    hint = render_text(font22, f"正在设置「{ACTION_DISPLAY[listening_action]}」，请按下新按键", MAGENTA)
                else:
                    hint = render_text(font22, "点击某一项后按下新按键（方向键仍兼容 WASD）", MENU_MUTED)
                game.screen.blit(hint, hint.get_rect(center=(_DESIGN_W // 2, 424)))

                for btn in (btn_key_restore, btn_key_back):
                    btn.update(mouse)
                    btn.draw(game.screen)
                _present(game, _lo_used, _lo_real)

            elif game.state == "menu_classic":
                _draw_submenu_page(game, mouse, menu_art.KEY_CLASSIC,
                                   "经典贪吃蛇", "经典贪吃蛇",
                                   "2D 经典 · 单人 / 人机 / 联机",
                                   game.btns_classic)

            elif game.state == "menu_sky":
                _draw_submenu_page(game, mouse, menu_art.KEY_SKY,
                                   "天空贪吃蛇", "天空贪吃蛇",
                                   "3D 空中世界 · 单人 / 人机 / 联机",
                                   game.btns_sky)

            elif game.state == "menu_more":
                _draw_submenu_page(game, mouse, None,
                                   "更多功能", "更多功能",
                                   "成绩记录 · 排行榜 · 设置 · 操作说明",
                                   game.btns_more)

            elif game.state == "diff":
                if not _draw_art_page(game, mouse, menu_art.KEY_DIFF_SOLO,
                                      game.btns_diff_art):
                    _menu_backdrop(game)
                    t = render_text(game.font48, "选择难度", MENU_TITLE)
                    game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 120)))
                    for btn in game.btns_diff:
                        btn.update(mouse)
                        btn.draw(game.screen)
                    _present(game, _lo_used, _lo_real)

            elif game.state == "versus_diff":
                if not _draw_versus_diff_page(game, mouse, btn_versus_time, now):
                    _menu_backdrop(game)
                    t = render_text(game.font48, "选择难度（人机对战）", MENU_TITLE)
                    game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 120)))
                    for btn in game.btns_diff_art:
                        btn.update(mouse)
                        btn.draw(game.screen)
                    btn_versus_time.update(mouse)
                    btn_versus_time.draw(game.screen)
                _present(game, _lo_used, _lo_real)

            elif game.state == "sky3d_ai_diff":
                if not _draw_art_page(game, mouse, menu_art.KEY_DIFF_SKY,
                                      game.btns_diff_art):
                    _menu_backdrop(game)
                    t = render_text(game.font48, "天空人机 · 选择难度", MENU_TITLE)
                    game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 120)))
                    hint = render_text(get_chinese_font(16),
                                       "3D 天空世界 HP 对决，将打开独立全屏窗口",
                                       MENU_SUB)
                    game.screen.blit(hint, hint.get_rect(center=(_DESIGN_W // 2, 158)))
                    for btn in game.btns_diff:
                        btn.update(mouse)
                        btn.draw(game.screen)
                    info = render_text(get_chinese_font(15),
                                       "AI 会采集果实、追击预判、闪避光波并在低血量开盾",
                                       MENU_MUTED)
                    game.screen.blit(info, info.get_rect(center=(_DESIGN_W // 2, 478)))
                    _present(game, _lo_used, _lo_real)

            elif game.state == "net_menu":
                _menu_backdrop(game)

                crumb = render_text(get_chinese_font(14),
                                    "首页  ›  经典贪吃蛇  ›  经典联机"
                                    if net_kind == "classic" else
                                    "首页  ›  天空贪吃蛇  ›  天空联机",
                                    MENU_MUTED)
                game.screen.blit(crumb, (22, 18))
                t = render_text(game.font48,
                                "经典联机" if net_kind == "classic" else "天空联机",
                                MENU_TITLE)
                game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 108)))
                hint = render_text(
                    get_chinese_font(16),
                    "房主创建房间后，对方输入房间码加入"
                    if net_kind == "classic" else
                    "3D 天空世界联机对战，将打开独立全屏窗口",
                    MENU_SUB)
                game.screen.blit(hint, hint.get_rect(center=(_DESIGN_W // 2, 146)))
                for btn in btns_net_menu:
                    btn.update(mouse)
                    btn.draw(game.screen)
                if net_err and now - net_err_time < 3000:
                    err = render_text(game.font28, net_err, (255, 100, 100))
                    game.screen.blit(err, err.get_rect(center=(_DESIGN_W // 2, 450)))
                _present(game, _lo_used, _lo_real)

            elif game.state == "net_host_cfg":
                _menu_backdrop(game)

                t = render_text(game.font48, "房间设置（房主）", MENU_TITLE)
                game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 88)))

                labels = {"speed": "速度", "obstacle": "障碍", "food": "食物数量", "duration": "比赛时长"}
                for kind, btn in btns_room.items():
                    lab = render_text(font22, labels[kind], MENU_INK)
                    game.screen.blit(lab, (285, btn.rect.centery - lab.get_height() // 2))
                    btn.update(mouse)
                    btn.draw(game.screen)

                hint = render_text(font22, "点击右侧按钮循环切换参数", MENU_MUTED)
                game.screen.blit(hint, hint.get_rect(center=(_DESIGN_W // 2, 344)))

                for btn in (btn_room_create, btn_room_back):
                    btn.update(mouse)
                    btn.draw(game.screen)
                if net_err and now - net_err_time < 3000:
                    err = render_text(game.font28, net_err, (255, 100, 100))
                    game.screen.blit(err, err.get_rect(center=(_DESIGN_W // 2, 480)))
                _present(game, _lo_used, _lo_real)

            elif game.state == "net_wait":
                _menu_backdrop(game)

                t = render_text(game.font48, "等待对方加入...", MENU_TITLE)
                game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 140)))

                code = render_text(game.font48, f"房间码: {room_code}", (198, 132, 20))
                game.screen.blit(code, code.get_rect(center=(_DESIGN_W // 2, 215)))
                info = render_text(game.font28, f"本机IP: {get_local_ip()}   端口: {NET_PORT}", MENU_INK)
                game.screen.blit(info, info.get_rect(center=(_DESIGN_W // 2, 268)))
                hint = render_text(font22, "请把房间码（或 IP）告知对方，对方在“加入房间”中输入", MENU_MUTED)
                game.screen.blit(hint, hint.get_rect(center=(_DESIGN_W // 2, 306)))

                if room_cfg:
                    cfg_text = (f"速度 {ROOM_SPEEDS[room_sel['speed']][0]} | "
                                f"障碍 {ROOM_OBSTACLES[room_sel['obstacle']][0]} | "
                                f"食物 {ROOM_FOODS[room_sel['food']]} | "
                                f"时长 {ROOM_DURATIONS[room_sel['duration']][0]}")
                    cfg_surf = render_text(font22, cfg_text, MENU_MUTED)
                    game.screen.blit(cfg_surf, cfg_surf.get_rect(center=(_DESIGN_W // 2, 350)))

                for btn in btns_net_wait:
                    btn.update(mouse)
                    btn.draw(game.screen)
                _present(game, _lo_used, _lo_real)

            elif game.state == "net_join":
                _menu_backdrop(game)

                t = render_text(game.font48, "输入房间码 / 主机IP", MENU_TITLE)
                game.screen.blit(t, t.get_rect(center=(_DESIGN_W // 2, 170)))
                box = pygame.Rect(300, 240, 360, 50)
                pygame.draw.rect(game.screen, (240, 250, 252), box, 0, 8)
                pygame.draw.rect(game.screen, (60, 180, 205), box, 2, 8)
                shown = ip_text + ("|" if (now // 500) % 2 == 0 else "")
                txt = render_text(game.font28, shown, MENU_INK)
                game.screen.blit(txt, (box.x + 12, box.centery - txt.get_height() // 2))
                hint = render_text(font22, "支持 7 位房间码或 IP（可带 :端口）", MENU_MUTED)
                game.screen.blit(hint, hint.get_rect(center=(_DESIGN_W // 2, 318)))
                hint2 = render_text(game.font28, "回车确认  |  ESC 返回", MENU_MUTED)
                game.screen.blit(hint2, hint2.get_rect(center=(_DESIGN_W // 2, 356)))
                if net_err and now - net_err_time < 3000:
                    err = render_text(game.font28, net_err, (255, 100, 100))
                    game.screen.blit(err, err.get_rect(center=(_DESIGN_W // 2, 420)))
                _present(game, _lo_used, _lo_real)

            elif game.state == "play":
                game.update(now)
                game.draw()
                if waiting_practice == "classic" and net_host is not None:
                    _draw_wait_banner(game.screen, room_code,
                                      _DESIGN_W // 2, _DESIGN_H - 38)
                _present(game, _lo_used, _lo_real)

        elif mode == "ai":
            ai_game.update(now)
            ai_game.draw()

        elif mode == "versus":
            versus_game.update(now)
            versus_game.draw()

        elif mode == "net":
            net_game.update(now)
            net_game.draw()

        _fps_cap = SETTINGS.get("fps", RENDER_FPS) or 0
        if _STAGE["hi"]:
            # 首页是 2048x1152 大画布，动效也不需要高帧率，统一压到 60 帧省电
            _fps_cap = min(_fps_cap, 60) if _fps_cap else 60
        game.clock.tick(_fps_cap)


if __name__ == "__main__":
    main()
