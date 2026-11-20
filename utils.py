import json
import os
import random
import sys
from collections import deque
from datetime import datetime
from config import SCORE_FILE, NET_RECORD_FILE, SPAWN_SAFE_RADIUS, \
    SETTINGS_FILE, SETTINGS, KEY_BINDINGS, DEFAULT_KEY_BINDINGS, SKINS, CYAN, WHITE, \
    SKY_SCORE_FILE, WINDOW_WIDTH, WINDOW_HEIGHT, DATA_DIR
try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def to_logical_pos(pos, base=(960, 540)):
    """把鼠标/事件坐标折回 960×540 设计稿坐标空间。

    绘制表面会随页面切换（960 或 2048 高清画布）以及窗口拉伸、缩放、全屏
    而改变尺寸；用固定比例换算会在"窗口被放大 / 切全屏"时把热区算偏，
    所以这里按当前绘制表面的实际尺寸换算，越界时再按窗口像素尺寸兜底。
    """
    import pygame
    bw, bh = base
    try:
        surf = pygame.display.get_surface()
        sw, sh = surf.get_size() if surf is not None else (bw, bh)
    except Exception:
        sw, sh = (bw, bh)
    if sw <= 0 or sh <= 0:
        sw, sh = (bw, bh)
    x = int(pos[0] * bw / float(sw))
    y = int(pos[1] * bh / float(sh))
    if 0 <= x < bw and 0 <= y < bh:
        return (x, y)
    # 兜底：拿到的是窗口像素坐标（全屏后 SDL 未做逻辑换算的情形）
    try:
        ww, wh = pygame.display.get_window_size()
    except Exception:
        ww = wh = 0
    if ww > 0 and wh > 0:
        x = int(pos[0] * bw / float(ww))
        y = int(pos[1] * bh / float(wh))
    return (x, y)


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path):
    """资源绝对路径：打包后在 _MEIPASS 内，源码运行在脚本所在目录

    以脚本位置为基准而不是 os.path.abspath(".")，从任意工作目录启动都能找到资源。
    """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(_BASE_DIR, relative_path)


# ---------------- 显示加速与性能监控 ----------------

_RENDER_STATE = {"probed": False, "gpu": False}


def _probe_gpu_acceleration():
    """探测 SDL2 能否创建硬件加速渲染器"""
    import pygame
    if not pygame.display.get_init():
        pygame.display.init()
    try:
        from pygame._sdl2 import video
        win = video.Window("snake-probe", size=(32, 32), hidden=True)
        try:
            video.Renderer(win, accelerated=1)
            ok = True
        except Exception:
            ok = False
        finally:
            win.destroy()
        return ok
    except Exception:
        return False


def create_display(size, caption=None):
    """创建可缩放窗口：有 GPU 走硬件加速，无 GPU 回退 CPU；支持自由拉伸与全屏"""
    import pygame
    if not _RENDER_STATE["probed"]:
        _RENDER_STATE["gpu"] = _probe_gpu_acceleration()
        _RENDER_STATE["probed"] = True
    size = (int(size[0]), int(size[1]))
    _RENDER_STATE["size"] = size
    flags = pygame.RESIZABLE | pygame.DOUBLEBUF | pygame.SCALED
    if _RENDER_STATE.get("fullscreen"):
        flags |= pygame.FULLSCREEN
    if _RENDER_STATE["gpu"]:
        flags |= pygame.HWSURFACE
    try:
        screen = pygame.display.set_mode(size, flags)
    except Exception:
        _RENDER_STATE["gpu"] = False
        screen = pygame.display.set_mode(
            size, pygame.RESIZABLE | pygame.DOUBLEBUF)
    if caption:
        pygame.display.set_caption(caption)
    return screen


def toggle_fullscreen():
    """F11：在窗口与全屏之间切换（保持画面比例，鼠标坐标继续按画布换算）

    这里用重建显示面（set_mode）的方式切换，而不是 SDL 的
    display.toggle_fullscreen()：后者在 SCALED 模式下会破坏逻辑尺寸映射，
    切回窗口（或再拉伸窗口）后画面只显示左上角一块、鼠标坐标与按钮热区
    明显错位。
    """
    import pygame
    want_full = not _RENDER_STATE.get("fullscreen", False)
    size = _RENDER_STATE.get("size") or (WINDOW_WIDTH, WINDOW_HEIGHT)
    flags = pygame.RESIZABLE | pygame.DOUBLEBUF | pygame.SCALED
    if _RENDER_STATE.get("gpu"):
        flags |= pygame.HWSURFACE
    if want_full:
        flags |= pygame.FULLSCREEN
    try:
        pygame.display.set_mode(size, flags)
    except Exception:
        return _RENDER_STATE.get("fullscreen", False)
    _RENDER_STATE["fullscreen"] = want_full
    return want_full


_FONT_CACHE = {}


def get_chinese_font(size):
    """获取支持中文的字体，按优先级尝试（按字号缓存，避免重复加载字体文件）"""
    import pygame

    cached = _FONT_CACHE.get(size)
    if cached is not None:
        return cached

    font_paths = [
        resource_path("assets/fonts/msyh.ttc"),
        resource_path("assets/fonts/simhei.ttf"),
        resource_path("msyh.ttc"),
        resource_path("simhei.ttf"),
        resource_path("fonts/msyh.ttc"),
        resource_path("fonts/simhei.ttf"),
    ]
    if sys.platform == 'win32':
        win_fonts_dir = os.environ.get('WINDIR', 'C:\\Windows') + '\\Fonts\\'
        font_paths.append(win_fonts_dir + 'simhei.ttf')
        font_paths.append(win_fonts_dir + 'msyh.ttc')

    for path in font_paths:
        if os.path.exists(path):
            try:
                font = pygame.font.Font(path, size)
                test_surf = font.render("中", True, (255, 255, 255))
                if test_surf.get_width() > 0:
                    _FONT_CACHE[size] = font
                    return font
            except:
                pass

    font_names = ["SimHei", "Microsoft YaHei", "SimSun", "FangSong", "KaiTi",
                  "Noto Sans CJK SC", "Noto Sans CJK TC", "PingFang SC"]
    for name in font_names:
        try:
            font = pygame.font.SysFont(name, size)
            test_surf = font.render("中", True, (255, 255, 255))
            if test_surf.get_width() > 0:
                _FONT_CACHE[size] = font
                return font
        except:
            continue

    font = pygame.font.Font(None, size)
    _FONT_CACHE[size] = font
    return font


# 画布相对 960×540 设计稿的放大倍数：字号、固定边距都按这个比例放大，
# 这样各处仍按设计稿标注写数字，换画布尺寸时只需调整 WINDOW_SIZE 一处
UI_SCALE = WINDOW_WIDTH / 960.0


def get_ui_font(size):
    """按当前画布换算后的字号取中文字体（size 仍按 960×540 设计稿标注）"""
    return get_chinese_font(max(8, int(round(size * UI_SCALE))))


def load_settings():
    """读取 settings.json，合并进 config.SETTINGS / KEY_BINDINGS（原地修改）"""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    for key in ("master", "eat", "death", "bgm", "menu"):
        try:
            if key in data:
                SETTINGS[key] = max(0.0, min(1.0, float(data[key])))
        except Exception:
            pass
    nick = data.get("nickname")
    if isinstance(nick, str) and nick.strip():
        SETTINGS["nickname"] = nick.strip()[:10]
    try:
        skin = int(data.get("skin", 0))
        if 0 <= skin < len(SKINS):
            SETTINGS["skin"] = skin
    except Exception:
        pass
    try:
        from config import FPS_OPTIONS
        fps = int(data.get("fps", SETTINGS.get("fps", 180)))
        if fps in FPS_OPTIONS:
            SETTINGS["fps"] = fps
    except Exception:
        pass
    try:
        from config import SKY_SENS_MIN, SKY_SENS_MAX
        sens = float(data.get("sky_sens", SETTINGS.get("sky_sens", 3.0)))
        SETTINGS["sky_sens"] = max(SKY_SENS_MIN, min(SKY_SENS_MAX, sens))
    except Exception:
        pass
    try:
        SETTINGS["sky_invert_y"] = 1 if int(data.get("sky_invert_y", 0)) else 0
    except Exception:
        pass
    keys = data.get("keys")
    if isinstance(keys, dict):
        for action in DEFAULT_KEY_BINDINGS:
            if isinstance(keys.get(action), int):
                KEY_BINDINGS[action] = keys[action]
    return SETTINGS


def sync_sky_sens():
    """只从 settings.json 取回天空灵敏度（3D 子进程里改过它时，主进程需要刷新）"""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        from config import SKY_SENS_MIN, SKY_SENS_MAX
        sens = float(data.get("sky_sens", SETTINGS.get("sky_sens", 3.0)))
        SETTINGS["sky_sens"] = max(SKY_SENS_MIN, min(SKY_SENS_MAX, sens))
    except Exception:
        pass
    return SETTINGS.get("sky_sens", 3.0)

def save_settings():
    """把当前设置写入 settings.json"""
    try:
        payload = {"master": SETTINGS["master"], "eat": SETTINGS["eat"],
                   "death": SETTINGS["death"], "bgm": SETTINGS["bgm"],
                   "menu": SETTINGS["menu"],
                   "nickname": SETTINGS["nickname"], "skin": SETTINGS["skin"],
                   "fps": SETTINGS.get("fps", 180),
                   "sky_sens": SETTINGS.get("sky_sens", 3.0),
                   "sky_invert_y": SETTINGS.get("sky_invert_y", 0),
                   "keys": {a: KEY_BINDINGS[a] for a in DEFAULT_KEY_BINDINGS}}
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def sound_volume(name):
    """按主音量缩放后的实际音量"""
    return max(0.0, min(1.0, SETTINGS["master"] * SETTINGS.get(name, 1.0)))


def key_name(key):
    """按键的可读名称"""
    import pygame
    named = {
        pygame.K_UP: "↑", pygame.K_DOWN: "↓", pygame.K_LEFT: "←", pygame.K_RIGHT: "→",
        pygame.K_ESCAPE: "ESC", pygame.K_RETURN: "Enter", pygame.K_SPACE: "空格",
        pygame.K_TAB: "Tab", pygame.K_BACKSPACE: "退格", pygame.K_LSHIFT: "Shift",
        pygame.K_LCTRL: "Ctrl", pygame.K_LALT: "Alt",
    }
    if key in named:
        return named[key]
    if pygame.K_a <= key <= pygame.K_z:
        return chr(key).upper()
    if pygame.K_0 <= key <= pygame.K_9:
        return chr(key)
    if pygame.K_F1 <= key <= pygame.K_F12:
        return f"F{key - pygame.K_F1 + 1}"
    return f"Key{key}"


def key_is(action, key):
    """按键是否对应动作（方向键仍兼容 WASD）"""
    import pygame
    if KEY_BINDINGS.get(action) == key:
        return True
    alts = {
        "up": (pygame.K_w,), "down": (pygame.K_s,),
        "left": (pygame.K_a,), "right": (pygame.K_d,),
    }
    return key in alts.get(action, ())


_TEXT_CACHE = {}
_TEXT_CACHE_LIMIT = 1024


def render_text(font, text, color):
    """缓存文本渲染结果（空间换时间）。

    相同字体/文本/颜色只光栅化一次，后续帧直接复用 Surface，
    像素结果与 font.render(text, True, color) 完全一致。
    """
    import pygame
    key = (font, text, color)
    surf = _TEXT_CACHE.get(key)
    if surf is None:
        if len(_TEXT_CACHE) >= _TEXT_CACHE_LIMIT:
            _TEXT_CACHE.clear()
        surf = font.render(text, True, color)
        _TEXT_CACHE[key] = surf
    return surf


_GRID_CACHE = {}
_GRID_KEY = (1, 2, 3)   # 透明色键（不会与网格/背景颜色冲突）


def get_grid_overlay(width, height, grid_size, color):
    """预渲染网格线为透明叠加层，替代每帧逐条 draw.line。

    使用 colorkey 透明 + RLEACCEL，比逐条画线和逐像素 alpha 更快，
    像素结果与直接画线完全一致。
    """
    import pygame
    key = (width, height, grid_size, color)
    surf = _GRID_CACHE.get(key)
    if surf is None:
        surf = pygame.Surface((width, height))
        surf.fill(_GRID_KEY)
        for x in range(0, width, grid_size):
            pygame.draw.line(surf, color, (x, 0), (x, height), 1)
        for y in range(0, height, grid_size):
            pygame.draw.line(surf, color, (0, y), (width, y), 1)
        surf.set_colorkey(_GRID_KEY, pygame.RLEACCEL)
        _GRID_CACHE[key] = surf
    return surf


def load_score_data():
    """加载分数数据，若不存在则返回默认"""
    default = {"highest_score": 0, "recent_scores": []}
    try:
        with open(SCORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "highest_score" in data:
                return data
    except:
        pass

    try:
        with open(os.path.join(DATA_DIR, "score.json"), "r", encoding="utf-8") as f:
            old_data = json.load(f)
            if "highest_score" in old_data:
                save_score_data(old_data["highest_score"], old_data["recent_scores"])
                return old_data
    except:
        pass
    return default


def save_score_data(highest, recent):
    try:
        with open(SCORE_FILE, "w", encoding="utf-8") as f:
            json.dump({"highest_score": highest, "recent_scores": recent}, f)
    except:
        pass


def update_score(current, highest, recent):
    if current > highest:
        highest = current
    recent.append({"score": current, "time": datetime.now().strftime("%m-%d %H:%M")})
    if len(recent) > 10:
        recent.pop(0)
    return highest, recent


def load_sky_best():
    """加载天空贪吃蛇最高经验"""
    try:
        with open(SKY_SCORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("highest_score", 0))
    except Exception:
        return 0


def save_sky_best(score):
    """保存天空贪吃蛇最高经验"""
    try:
        with open(SKY_SCORE_FILE, "w", encoding="utf-8") as f:
            json.dump({"highest_score": int(score)}, f, ensure_ascii=False)
    except Exception:
        pass


def create_history_surface(width, height, recent_scores):
    """绘制历史成绩图表，无数据时显示提示"""
    import pygame

    # 空数据直接返回提示界面
    if not recent_scores:
        surf = pygame.Surface((width, height), pygame.SRCALPHA)
        surf.fill((250, 253, 255, 236))
        try:
            font_title = get_chinese_font(26)
            font_label = get_chinese_font(18)
            font_hint = get_chinese_font(30)
        except:
            font_title = pygame.font.Font(None, 26)
            font_label = pygame.font.Font(None, 18)
            font_hint = pygame.font.Font(None, 30)
        title = font_title.render("历史成绩", True, (10, 110, 132))
        surf.blit(title, (width // 2 - title.get_width() // 2, 15))
        hint = font_hint.render("还没有进行游戏", True, (16, 78, 96))
        surf.blit(hint, (width // 2 - hint.get_width() // 2, height // 2 - hint.get_height() // 2))
        close = font_label.render("点击任意位置关闭", True, (120, 160, 178))
        surf.blit(close, (width - close.get_width() - 10, height - 20))
        return surf

    # 有数据时绘制折线图
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    surf.fill((250, 253, 255, 236))

    try:
        font_title = get_chinese_font(26)
        font_label = get_chinese_font(18)
        font_value = get_chinese_font(14)
    except:
        font_title = pygame.font.Font(None, 26)
        font_label = pygame.font.Font(None, 18)
        font_value = pygame.font.Font(None, 14)

    title = font_title.render("历史成绩", True, (10, 110, 132))
    surf.blit(title, (width // 2 - title.get_width() // 2, 15))

    scores = [d["score"] for d in recent_scores]
    times = [d["time"] for d in recent_scores]
    n = len(scores)
    if n == 0:
        return surf

    margin_left, margin_right, margin_top, margin_bottom = 40, 60, 55, 62
    chart_w = width - margin_left - margin_right
    chart_h = height - margin_top - margin_bottom
    if chart_w < 50 or chart_h < 50:
        return surf

    max_score = max(scores) if max(scores) > 0 else 1
    y_max = max_score * 1.2 if max_score > 0 else 10

    axis_color = (150, 195, 210)
    pygame.draw.line(surf, axis_color, (margin_left, margin_top), (margin_left, margin_top + chart_h), 2)
    pygame.draw.line(surf, axis_color, (margin_left, margin_top + chart_h), (margin_left + chart_w, margin_top + chart_h), 2)

    for i in range(5):
        y_val = (y_max / 4) * i
        y_pos = margin_top + chart_h - (y_val / y_max) * chart_h
        pygame.draw.line(surf, (196, 222, 230), (margin_left, y_pos), (margin_left + chart_w, y_pos), 1)
        label = font_label.render(str(int(y_val)), True, (90, 140, 158))
        surf.blit(label, (margin_left - label.get_width() - 3, y_pos - label.get_height() // 2))

    if n == 1:
        x = margin_left
        y_pos = margin_top + chart_h - (scores[0] / y_max) * chart_h
        pygame.draw.circle(surf, (0, 168, 200), (x, y_pos), 5)
        val = font_value.render(str(scores[0]), True, (16, 78, 96))
        surf.blit(val, (x + 6, y_pos - val.get_height() // 2))
        time_label = font_value.render(times[0], True, (110, 155, 172))
        surf.blit(time_label, (x - time_label.get_width() // 2, margin_top + chart_h + 5))
    else:
        points = []
        for i, score in enumerate(scores):
            x = margin_left + (i / (n - 1)) * chart_w
            y_pos = margin_top + chart_h - (score / y_max) * chart_h
            points.append((x, y_pos))
        if len(points) > 1:
            pygame.draw.lines(surf, (0, 168, 200), False, points, 2)
        for i, (x, y_pos) in enumerate(points):
            pygame.draw.circle(surf, (0, 168, 200), (int(x), int(y_pos)), 4)
            val = font_value.render(str(scores[i]), True, (16, 78, 96))
            surf.blit(val, (x + 6, y_pos - val.get_height() // 2))
            time_label = font_value.render(times[i], True, (110, 155, 172))
            if i % 2 == 0 or i == n - 1:
                surf.blit(time_label, (x - time_label.get_width() // 2, margin_top + chart_h + 5))

    hint = font_label.render("点击任意位置关闭", True, (120, 160, 178))
    surf.blit(hint, (width - hint.get_width() - 10, height - 20))
    return surf


def generate_obstacles(grid_w, grid_h, count, p1_spawn, p2_spawn,
                       safe_radius=SPAWN_SAFE_RADIUS, max_retry=200):
    """生成随机障碍物集合，并保证两个出生点之间连通。

    借鉴论文《基于SpringBoot和Vue的贪吃蛇双人对战游戏设计与实现》中
    check_connective() 的思路：随机生成障碍后用搜索校验起点可达终点，
    不可达则重新生成，避免玩家陷入封闭区域。这里用 BFS 实现。

    返回 set((x, y), ...)；多次尝试失败时退化为无障碍。
    """
    def in_safe(x, y):
        return (abs(x - p1_spawn[0]) + abs(y - p1_spawn[1]) <= safe_radius or
                abs(x - p2_spawn[0]) + abs(y - p2_spawn[1]) <= safe_radius)

    def connected(obstacles):
        visited = {p1_spawn}
        q = deque([p1_spawn])
        while q:
            x, y = q.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < grid_w and 0 <= ny < grid_h \
                        and (nx, ny) not in obstacles and (nx, ny) not in visited:
                    if (nx, ny) == p2_spawn:
                        return True
                    visited.add((nx, ny))
                    q.append((nx, ny))
        return False

    for _ in range(max_retry):
        obstacles = set()
        attempts = 0
        while len(obstacles) < count and attempts < count * 20:
            attempts += 1
            x = random.randint(1, grid_w - 2)
            y = random.randint(1, grid_h - 2)
            if (x, y) in obstacles or in_safe(x, y):
                continue
            obstacles.add((x, y))
        if connected(obstacles):
            return obstacles
    return set()


def load_net_record():
    """加载联机战绩 {"win": n, "lose": n, "draw": n, "matches": [...]}"""
    default = {"win": 0, "lose": 0, "draw": 0, "matches": []}
    try:
        with open(NET_RECORD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default
    record = {k: int(data.get(k, 0)) for k in ("win", "lose", "draw")}
    matches = data.get("matches", [])
    record["matches"] = matches if isinstance(matches, list) else []
    return record


def update_net_record(result, detail=None):
    """记录一局联机结果（'win'/'lose'/'draw'），可附带对局明细，返回更新后的战绩"""
    record = load_net_record()
    if result in record:
        record[result] += 1
    if isinstance(detail, dict):
        record["matches"].append(detail)
        if len(record["matches"]) > 20:
            record["matches"] = record["matches"][-20:]
    try:
        with open(NET_RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
    except Exception:
        pass
    return record


def create_leaderboard_surface(width, height):
    """排行榜：单人最高分/最近成绩 + 联机战绩 + 最近对局明细"""
    import pygame

    score = load_score_data()
    record = load_net_record()

    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    surf.fill((250, 253, 255, 236))
    font_title = get_chinese_font(26)
    font_head = get_chinese_font(20)
    font_body = get_chinese_font(16)
    font_hint = get_chinese_font(16)

    title = font_title.render("排行榜", True, (10, 110, 132))
    surf.blit(title, (width // 2 - title.get_width() // 2, 14))

    y = 58
    head = font_head.render("单人模式", True, (0, 150, 180))
    surf.blit(head, (40, y))
    y += 28

    best = font_body.render(f"最高分：{score.get('highest_score', 0)}", True, (16, 78, 96))
    surf.blit(best, (56, y))
    y += 24

    recent = score.get("recent_scores", [])[-5:]
    if recent:
        scores = [d.get("score", 0) for d in recent]
        line = font_body.render("最近成绩：" + "  ".join(str(s) for s in scores), True, (64, 134, 154))
        surf.blit(line, (56, y))
    else:
        line = font_body.render("最近成绩：暂无记录", True, (120, 160, 178))
        surf.blit(line, (56, y))
    y += 40

    head = font_head.render("联机对战", True, (0, 150, 180))
    surf.blit(head, (40, y))
    y += 28
    stats = font_body.render(
        f"{record['win']} 胜  ·  {record['lose']} 负  ·  {record['draw']} 平", True, (16, 78, 96))
    surf.blit(stats, (56, y))
    y += 34

    head = font_head.render("最近对局", True, (0, 150, 180))
    surf.blit(head, (40, y))
    y += 26

    matches = record.get("matches", [])[-5:][::-1]
    if not matches:
        surf.blit(font_body.render("暂无联机对局记录", True, (120, 160, 178)), (56, y))
    else:
        header = font_body.render("时间        我方   对方   结果", True, (108, 158, 176))
        surf.blit(header, (56, y))
        y += 22
        for m in matches:
            res = m.get("result", "")
            res_text = {"win": "胜", "lose": "负", "draw": "平"}.get(res, "?")
            res_color = {"win": (24, 140, 84), "lose": (200, 72, 84)}.get(res, (100, 140, 158))
            txt = font_body.render(
                f"{m.get('time','-')}   {m.get('my','-')}     {m.get('opp','-')}     {res_text}",
                True, res_color)
            surf.blit(txt, (56, y))
            y += 22

    hint = font_hint.render("点击任意位置关闭", True, (120, 160, 178))
    surf.blit(hint, (width - hint.get_width() - 10, height - 24))
    return surf


def create_bg(width, height):
    """使用 PIL 生成渐变背景图片，返回图片路径"""
    if not HAS_PIL:
        return None
    try:
        im = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(im)
        for y in range(height):
            r = 10 + (y // 150)
            g = 10 + (y // 150)
            b = 30 - (y // 120)
            draw.line([(0, y), (width, y)], (r, g, b))
        im.save("temp.png")
        return "temp.png"
    except:
        return None