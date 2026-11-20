"""天空贪吃蛇 3D：基于 Ursina(Panda3D) 的真三维天空世界。

玩法：自动前进、滑动鼠标全向转向（可爬升/俯冲）、吃果实成长、撞地面/山体/岩柱死亡。
世界为真实风格三维场景：连绵草地与圆润山丘、灰白岩柱、红岩高原、蓝天白云、
针叶松与阔叶树、漂移积云、彩虹、太阳与体积光柱，平行光实时阴影 + 雾效景深；
空中散布陨石带作为可碰撞障碍，飞行高度上限较高，可自由爬升探索。

技术要点：
  • 可视化剔除由 Panda3D 场景图自动完成（相机视锥剔除）。
  • 地形为平滑高度场网格，顶点色按高度/坡度混合（草→岩→雪），并叠加程序化无缝
    细节纹理、烘焙坡面明暗与谷地遮蔽；植被/陨石使用对应细节纹理与接地软阴影。
  • 蛇身用重叠球体沿轨迹链拟合（平滑管状体），追尾相机跟随蛇头朝向（含俯仰）。
  • 辉光/光柱/彩虹使用加法混合（additive blending）与广告牌(Billboard)技术。

运行方式：python sky3d_mode.py  （或由主菜单以子进程启动）
退出码：0=返回菜单  42=退出整个游戏
"""
import os
import sys
import math
import random
import time as _time
from collections import deque

from ursina import Ursina, Entity, Text, Button, Mesh, Audio, camera, scene, \
    window, mouse, held_keys, color, Vec3, Vec2, application, curve, Texture
from ursina import time as ursina_time
from panda3d.core import ColorBlendAttrib, TransparencyAttrib, loadPrcFileData

from config import SKY_WORLD_SIZE, SKY_SPEED, SKY_TURN_RATE, SKY_HEAD_RADIUS, \
    SKY_TRAIL_SPACING, SKY_INITIAL_LENGTH, SKY_LENGTH_PER_XP, SKY_MAX_LENGTH, \
    SKY_FRUIT_MAX, SKY_FRUIT_INTERVAL, SKY_GOLD_XP, SKY_XP_PER_LEVEL, \
    SKY_SPAWN_CLEAR, SKINS, SETTINGS, SKY_SOFT_WALL_BUFFER, SKY_SOFT_WALL_PUSH, \
    SKY_SOFT_WALL_INSET, SKY_SOFT_WALL_CUSTOM, SKY_SENS_MIN, SKY_SENS_MAX, \
    SKY_SENS_STEP
from utils import load_settings, save_settings, sound_volume, load_sky_best, \
    save_sky_best, resource_path

WORLD = SKY_WORLD_SIZE * 2      # 3D 版地图放大 2 倍（面积 4 倍）：更开阔的探索空间
SC = 0.1                        # 渲染缩放：1 像素 = 0.1 三维单位
FLY_Y = 0.7                     # 蛇飞行高度（三维单位）
LAND_BAND = 150
CAM_DIST = 16.0                 # 相机默认距离（追尾视角）
NEAR_HIDE = 7.0                 # 距相机过近的蛇段隐藏（避免长蛇把镜头包在体内）
MAX_ALT = 26.0                  # 飞行高度上限（三维单位，放大约 2 倍）
METEOR_BELTS = 24               # 空中陨石带数量（每带 8~12 颗，随地图扩大同步加密）
FRUIT_MAX = int(SKY_FRUIT_MAX * 2.0)   # 果实容量随地图面积提升
MOUSE_SENS = 3.0                # 鼠标转向灵敏度：弧度/屏幕宽（横移一屏约 172°，两屏将近一圈，FPS 中档）
MOUSE_DEADZONE = 0              # 死区：FPS 不设死区（设了会变成"起手迟滞"）
MOUSE_MAX_STEP = 0.8            # 单帧最大转角（约 46°）：只用来挡异常尖峰，正常甩鼠标不会碰到
PITCH_LIMIT = 1.05              # 俯仰角上限（60°）：远离 look_at 的 up 退化奇点，避免万向锁翻转

# ----- 冲刺与连击 -----
DASH_SPEED = 1.7                # 冲刺速度倍率
DASH_DRAIN = 32.0               # 冲刺能量消耗（每秒）
DASH_REGEN = 16.0               # 能量回复（每秒）
DASH_MIN = 12.0                 # 低于该能量无法再次起冲
COMBO_WINDOW = 2.6              # 连击窗口（秒）
COMBO_MAX = 6                   # 连击上限
DASH_FOV = 1.12                 # 冲刺时的视野扩张倍率（速度感）

# ----- 空中光环（穿越得分） -----
RING_COUNT = 16                 # 场上光环数量（大图同步增加）
RING_RADIUS = 3.2               # 光环开口半径（三维单位）
RING_XP = 5                     # 穿过光环奖励经验
RING_ENERGY = 26.0              # 穿过光环补充的冲刺能量
RING_RESPAWN = 5.0              # 光环被穿过后的重生延迟（秒）

# ----- 强化道具 -----
POWERUP_MAX = 5                 # 场上道具上限
POWERUP_INTERVAL = 7.5          # 道具刷新间隔（秒）
POWERUP_LIFE = 34.0             # 道具存在时间（秒）
BUFF_DUR = {"shield": 8.0, "magnet": 10.0, "double": 12.0,
            "turbo": 7.0, "lucky": 9.0}
TURBO_SPEED = 1.45              # 极速道具：前进速度倍率
GROW_XP = 10                    # 成长道具：立即获得的经验
LUCKY_GOLD_CHANCE = 0.35        # 幸运道具：金果生成概率
MAGNET_RANGE = 280.0            # 磁铁吸附半径（像素）
MAGNET_PULL_X = 420.0           # 磁铁水平吸附速度（像素/秒）
MAGNET_PULL_H = 14.0            # 磁铁垂直吸附速度（三维单位/秒）
POWERUP_COLORS = {
    "shield": (0.35, 0.90, 1.00),   # 护盾：蓝色
    "magnet": (0.72, 0.50, 1.00),   # 磁铁：紫色
    "double": (1.00, 0.82, 0.28),   # 双倍经验：金色
    "energy": (0.40, 1.00, 0.62),   # 冲刺能量：绿色
    "turbo": (1.00, 0.42, 0.18),    # 极速：橙红
    "grow": (0.72, 1.00, 0.35),     # 成长：青柠
    "lucky": (1.00, 0.40, 0.78),    # 幸运：桃红
}

# ----- 安全区（联机 / 人机对战） -----
ZONE_START_DELAY = 20.0         # 开局稳定期：20 秒后开始第一次缩圈
ZONE_WARN_TIME = 10.0           # 收缩前 10 秒显示"下一个安全区"位置（虚线蓝圈）
ZONE_SHRINK_TIME = 30.0         # 单次收缩时长：缓慢推进（原来 14 秒偏急）
ZONE_HOLD_TIME = 30.0           # 一轮 = 收缩 30 + 稳定 30 = 每 60 秒缩一次
ZONE_RADII = (0.50, 0.42, 0.34, 0.27, 0.20, 0.14)   # 初始 + 5 次收缩的目标半径
ZONE_DPS = (3.0, 4.0, 5.5, 7.0, 9.0, 12.0)          # 圈外每秒伤害（按已完成收缩次数）
ZONE_SEED = 20261118            # 固定布局种子：联机双方算出的圈完全一致
ZONE_PILLARS = 24               # 边界能量光柱数量
ZONE_BOLTS = 14                 # 边界竖状闪电数量
ZONE_SPARKS = 20                # 边界上升粒子数量
ZONE_WALL_Y = -11.0             # 能量墙底部高度（贴地）
ZONE_WALL_H = 26.0              # 能量墙高度（相机视高约 6~11，墙顶在 15）

# ----- 边界软墙撞击涟漪 -----
SOFT_RIPPLE_POOL = 6            # 同时存在的涟漪数量
SOFT_RIPPLE_LIFE = 0.55         # 单个涟漪持续时间（秒）
SOFT_RIPPLE_CD = 0.18           # 同一面墙的最小生成间隔（秒）
SOFT_RIPPLE_SIZE = 7.5          # 涟漪最终尺寸（三维单位）

# ----- 地形高度场（真实风格连续地表） -----
VOXEL_B = 2.5                   # 地形网格单元边长（三维单位 = 25 像素）
GROUND_BASE = -10.0             # 地形基准高度（最低层顶面）
GROUND_STEP = 1.0               # 地形阶梯单层高度
MOUNT_TOP = 2.6                 # 山丘（碰撞岛）顶高
MOUNT_LOW = 1.7                 # 山丘边缘梯层高度
PLATEAU_TOP = 2.6               # 世界边缘高原（死亡区）顶高
PILLAR_TOP = 2.8                # 圆石柱（石墙）顶高
GROUND_UV = 3.2                 # 地表细节纹理平铺尺寸（三维单位）
VEG_UV = 1.35                   # 植被/岩块细节纹理平铺尺寸

# ----- 地形碰撞（球形蛇头 vs 平滑地表，防止高速穿模） -----
HEAD_COL_R = SKY_HEAD_RADIUS * SC * 1.25   # 碰撞半径（三维单位）：覆盖渲染半径与上下浮动
SWEEP_STEP = 0.45                          # 扫掠采样步长（三维单位，约半个体径）
TERRAIN_MAX = PILLAR_TOP + 0.6             # 平滑地表的最高可能高度（高空可跳过碰撞检测）

TEST_MODE = os.environ.get("SKY3D_TEST") == "1"
EXIT_QUIT_GAME = 42             # 退出码：退出整个游戏
_APP = None                     # Ursina 应用实例（main 中赋值，测试截图用）
_SKIP_ANIM = os.environ.get("SKY3D_NOANIM") == "1"    # 诊断：跳过世界动画
_SKIP_VIS = os.environ.get("SKY3D_NOVIS") == "1"      # 诊断：跳过蛇身更新


# ---------------- 相对鼠标：成熟 FPS 的标准做法 ----------------
# 与 raylib 的 DisableCursor()、GLFW 的 GLFW_CURSOR_DISABLED、SDL 的
# SDL_SetRelativeMouseMode 完全同一套语义：
#   1. 隐藏系统光标，并把它**钉死在客户区中心**（每帧读完增量立刻回中）；
#   2. 每帧只取一次位移增量（不做累加、不做绝对位置换算）；
#   3. 不设死区（FPS 的死区表现为"起手迟滞"）；
#   4. 关闭系统"提高指针精确度"加速曲线，否则慢移与快甩的增益不一致；
#   5. 只有游戏窗口在前台时才吃鼠标（Alt+Tab 不产生转向）。
# 为什么不用 ursina 的 mouse.locked：它在锁定态把指针**绝对位置**当每帧位移
# （指针离中心越远转得越快），回中用的 move_pointer 又带 1.124 魔数，125% DPI
# 下会残留偏移 → 视角自漂。这里改用 GetCursorPos/SetCursorPos 同一套物理像素坐标。
class _RawMouse:
    """FPS 相对鼠标：返回本帧物理像素位移 + 客户区宽度"""

    TITLE = "天空贪吃蛇 3D"
    SPI_GETMOUSE = 0x0003
    SPI_SETMOUSE = 0x0004

    def __init__(self):
        self.ok = False
        self.hwnd = None
        self.prev = None
        self._ct = None
        self._wt = None
        self._accel_on = False       # 本进程是否已关闭系统鼠标加速
        self._accel_old = None
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            u.FindWindowW.restype = wintypes.HWND
            u.GetForegroundWindow.argtypes = []
            u.GetForegroundWindow.restype = wintypes.HWND
            u.GetClientRect.argtypes = [wintypes.HWND,
                                        ctypes.POINTER(wintypes.RECT)]
            u.ClientToScreen.argtypes = [wintypes.HWND,
                                         ctypes.POINTER(wintypes.POINT)]
            u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
            u.ShowCursor.argtypes = [ctypes.c_bool]
            u.ShowCursor.restype = ctypes.c_int
            u.IsWindow.argtypes = [wintypes.HWND]
            u.IsWindowVisible.argtypes = [wintypes.HWND]
            u.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            u.GetWindowThreadProcessId.restype = wintypes.DWORD

            class _CURSORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD),
                            ("flags", wintypes.DWORD),
                            ("hCursor", wintypes.HANDLE),
                            ("ptScreenPos", wintypes.POINT)]
            u.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
            u.GetCursorInfo.restype = wintypes.BOOL
            self._ci_cls = _CURSORINFO
            self._ct, self._wt, self._u = ctypes, wintypes, u
            self.ok = True
        except Exception:
            self.ok = False
        self._cursor_hidden = False

    # ---- 系统光标显隐（ShowCursor 是线程级引用计数，进程退出自动失效）----
    def _cursor_showing(self):
        """系统当前是否真的在画光标（GetCursorInfo 的 CURSOR_SHOWING 位）

        ShowCursor 只改线程计数，而 Panda3D 的 set_cursor_hidden 在 Windows
        上也是调 ShowCursor：ursina 的 mouse.visible 与这里的 hide_cursor
        各减一次，进游戏时计数就被压到 -2 以下，退出时按"调一次就够"的思路
        加回来只到 -1，系统仍判定隐藏 —— 这正是暂停菜单里只剩面板、看不到
        指针的原因。所以不再猜计数，直接读系统状态校验。
        """
        cls = getattr(self, "_ci_cls", None)
        if cls is None:
            return None
        try:
            ci = cls()
            ci.cbSize = self._ct.sizeof(cls)
            if not self._u.GetCursorInfo(self._ct.byref(ci)):
                return None
            return bool(ci.flags & 0x00000001)
        except Exception:
            return None

    def _apply_cursor(self, visible):
        """反复调 ShowCursor，直到系统真实状态与目标一致（最多 16 次）"""
        for _ in range(16):
            showing = self._cursor_showing()
            if showing is None:              # 查询失败：退回按计数调用一次
                self._u.ShowCursor(bool(visible))
                return
            if showing == bool(visible):
                return
            self._u.ShowCursor(bool(visible))

    def hide_cursor(self):
        if not self.ok:
            return
        try:
            self._apply_cursor(False)
            self._cursor_hidden = True
        except Exception:
            pass

    def show_cursor(self):
        if not self.ok:
            return
        try:
            self._apply_cursor(True)         # 不因标志位已回 False 而跳过：
            self._cursor_hidden = False      # 标志与实际系统状态可能已经脱节
        except Exception:
            pass

    # ---- 系统鼠标加速（"提高指针精确度"）----
    def kill_accel(self):
        """游戏进行时关闭指针加速曲线，让慢移与快甩增益一致（FPS 常规做法）。

        只改当前会话的内存设置（不带 SPIF_UPDATEINIFILE），不写注册表；
        退出/暂停时由 restore_accel() 还原。
        """
        if not self.ok or self._accel_on:
            return
        try:
            arr = (self._ct.c_int * 3)()
            self._u.SystemParametersInfoW(self.SPI_GETMOUSE, 0, arr, 0)
            self._accel_old = (arr[0], arr[1], arr[2])
            if self._accel_old == (0, 0, 0):
                return
            zero = (self._ct.c_int * 3)(0, 0, 0)
            self._u.SystemParametersInfoW(self.SPI_SETMOUSE, 0, zero, 0x02)
            self._accel_on = True
            try:                          # 兜底：无论从哪条路径退出都要还原
                import atexit
                atexit.register(self.restore_accel)
            except Exception:
                pass
        except Exception:
            pass

    def restore_accel(self):
        """还原系统鼠标加速曲线"""
        if not self._accel_on or not self._accel_old:
            return
        try:
            back = (self._ct.c_int * 3)(*self._accel_old)
            self._u.SystemParametersInfoW(self.SPI_SETMOUSE, 0, back, 0x02)
        except Exception:
            pass
        self._accel_on = False
        self._accel_old = None

    def _window(self):
        if self.hwnd:
            try:
                if self._u.IsWindow(self.hwnd):
                    return self.hwnd
            except Exception:
                pass
            self.hwnd = None
        if not self.ok:
            return None
        try:                             # 1) 单人模式：标题固定，按标题直查
            self.hwnd = self._u.FindWindowW(None, self.TITLE) or None
            if self.hwnd:
                return self.hwnd
        except Exception:
            self.hwnd = None
        self.hwnd = self._find_own_window()    # 2) 标题带后缀：按进程找窗口
        return self.hwnd

    def _find_own_window(self):
        """枚举本进程的顶层可见窗口，不依赖窗口标题。

        人机对战 / 联机对战的窗口标题是「天空贪吃蛇 3D 人机对战」这类带
        后缀的名字，而 FindWindowW 是精确匹配：查不到窗口 → _client_box()
        拿不到客户区 → read() 每帧都返回 0 位移 → 这两个模式完全无法转向。
        这里改成「本进程 + 可见 + 客户区够大」，标题怎么改都不受影响。
        """
        try:
            pid = self._ct.windll.kernel32.GetCurrentProcessId()
            proto = self._ct.WINFUNCTYPE(self._wt.BOOL, self._wt.HWND,
                                         self._wt.LPARAM)
            found = []

            def _cb(hwnd, _lparam):
                try:
                    p = self._wt.DWORD()
                    self._u.GetWindowThreadProcessId(hwnd, self._ct.byref(p))
                    if p.value != pid or not self._u.IsWindowVisible(hwnd):
                        return True
                    r = self._wt.RECT()
                    if (self._u.GetClientRect(hwnd, self._ct.byref(r))
                            and (r.right - r.left) >= 320
                            and (r.bottom - r.top) >= 240):
                        found.append(hwnd)
                except Exception:
                    pass
                return True

            self._u.EnumWindows(proto(_cb), 0)
            return found[0] if found else None
        except Exception:
            return None

    def _client_box(self):
        """客户区在桌面上的矩形：左上角 x/y 与宽高（物理像素）"""
        hwnd = self._window()
        if hwnd is None:
            return None
        r = self._wt.RECT()
        if not self._u.GetClientRect(hwnd, self._ct.byref(r)):
            return None
        p = self._wt.POINT(r.left, r.top)
        self._u.ClientToScreen(hwnd, self._ct.byref(p))
        w, h = int(r.right - r.left), int(r.bottom - r.top)
        if w <= 0 or h <= 0:
            return None
        return int(p.x), int(p.y), w, h

    def read(self):
        """返回 (dx, dy, client_w)：本帧位移（物理像素）与客户区宽度"""
        box = self._client_box()
        if box is None:
            return 0, 0, 1
        ox, oy, cw, ch = box
        # 只有游戏窗口在前台时才吃鼠标：Alt+Tab、弹窗抢焦点一律不产生转向
        if self._window() is not None:
            try:
                if self._u.GetForegroundWindow() != self.hwnd:
                    self.prev = None
                    return 0, 0, cw
            except Exception:
                pass
        pt = self._wt.POINT()
        self._u.GetCursorPos(self._ct.byref(pt))
        cur = (int(pt.x), int(pt.y))
        if self.prev is None:
            self.prev = cur
            return 0, 0, cw
        dx, dy = cur[0] - self.prev[0], cur[1] - self.prev[1]
        # 光标钉死在客户区中心：读完本帧增量立刻回中（raylib DisableCursor 同款）。
        # 玩家可以无限转向，也不存在"贴边回中"的阈值和跳变。
        tx, ty = ox + cw // 2, oy + ch // 2
        if cur != (tx, ty):
            try:
                self._u.SetCursorPos(tx, ty)
            except Exception:
                pass
            # 回读真实落点作为下一帧参考点：万一 SetCursorPos 被系统拒绝，
            # 参考点也不会与真实位置产生固定偏差（否则每帧同一误差 = 慢慢自漂）
            got = self._wt.POINT()
            self._u.GetCursorPos(self._ct.byref(got))
            self.prev = (int(got.x), int(got.y))
        else:
            self.prev = cur
        return dx, dy, cw

    def reset(self):
        """重新接管鼠标时丢掉上一帧参考点（避免进入游戏后第一帧的大位移）"""
        self.prev = None
        self.hwnd = None

    def lock(self):
        """进入游戏：关加速、隐藏系统光标、把光标钉在客户区中心。

        为什么用 ShowCursor(FALSE) 而不是 ClipCursor：我们靠 GetCursorPos
        的差分拿位移，ClipCursor 会把光标裁在中心，位移被系统吃掉，转向就
        失效了。所以改成"隐藏光标 + 每帧回中"——玩家看不到光标（视觉上
        完全静止），鼠标的物理位移依然完整可读。
        """
        self.kill_accel()
        self.hide_cursor()
        self.prev = None
        try:                             # 兜底：异常退出也要把光标放回来
            import atexit
            atexit.register(self.unlock)
        except Exception:
            pass
        box = self._client_box()
        if box is None:
            return
        ox, oy, cw, ch = box
        tx, ty = ox + cw // 2, oy + ch // 2
        try:
            self._u.SetCursorPos(tx, ty)
            self.prev = (tx, ty)
        except Exception:
            pass

    def unlock(self):
        """离开游戏（暂停 / 菜单 / 结算 / 退出）：恢复光标、还原系统设置"""
        self.show_cursor()
        self.restore_accel()
        self.prev = None
        # lock() 把指针钉在客户区正中，暂停面板也在正中；这里再显式落一次
        # 中心点，保证指针一定在窗口内、面板上方，而不是停在旧的屏幕边缘。
        box = self._client_box()
        if box is not None:
            ox, oy, cw, ch = box
            try:
                self._u.SetCursorPos(ox + cw // 2, oy + ch // 2)
            except Exception:
                pass


# ---------------- 程序化纹理 ----------------

def _make_glow_texture(size=128):
    """径向渐变光晕纹理"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    for i in range(20):
        r = c * (1 - i / 20)
        a = int(14 + 235 * (i / 20) ** 2)
        d.ellipse((c - r, c - r, c + r, c + r), fill=(255, 255, 255, a))
    return img


def _make_star_texture(size=48):
    """十字星芒纹理"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    d.ellipse((c - 5, c - 5, c + 5, c + 5), fill=(255, 255, 255, 255))
    d.line((c - size * 0.42, c, c + size * 0.42, c), fill=(255, 255, 255, 180), width=3)
    d.line((c, c - size * 0.42, c, c + size * 0.42), fill=(255, 255, 255, 180), width=3)
    d.ellipse((c - 10, c - 10, c + 10, c + 10), outline=(220, 230, 255, 90), width=2)
    return img


def _tileable_noise(size, seed, octaves=((4, 0.36), (8, 0.26), (16, 0.19),
                                        (32, 0.12), (64, 0.07))):
    """可无缝平铺的多频段值噪声（圆周双线性插值），返回 0..1 的 float 数组"""
    import numpy as np
    rng = np.random.default_rng(seed)
    coords = np.arange(size, dtype=np.float32)
    acc = np.zeros((size, size), dtype=np.float32)
    wsum = 0.0
    for cells, w in octaves:
        g = rng.random((cells, cells)).astype(np.float32)
        f = coords * (cells / size)
        i0 = np.floor(f).astype(np.int32) % cells
        i1 = (i0 + 1) % cells
        t = f - np.floor(f)
        t = t * t * (3.0 - 2.0 * t)         # smoothstep，避免线性插值的菱形纹
        tx, ty = t[:, None], t[None, :]
        v00 = g[np.ix_(i0, i0)]
        v10 = g[np.ix_(i1, i0)]
        v01 = g[np.ix_(i0, i1)]
        v11 = g[np.ix_(i1, i1)]
        acc += ((v00 * (1 - tx) + v10 * tx) * (1 - ty) +
                (v01 * (1 - tx) + v11 * tx) * ty) * w
        wsum += w
    return acc / wsum


def _make_ground_texture(size=1024):
    """地表细节纹理：中性灰底的草绒层（与顶点色相乘）——
    低频只做极轻微明暗，细节密度交给草叶短划与细颗粒，避免出现大块模糊暗斑"""
    import numpy as np
    from PIL import Image, ImageDraw
    macro = _tileable_noise(size, 61803, ((4, 0.34), (8, 0.28), (16, 0.22), (32, 0.16)))
    mid = _tileable_noise(size, 88017, ((32, 0.34), (64, 0.34), (128, 0.32)))
    micro = _tileable_noise(size, 7789, ((96, 0.40), (192, 0.34), (384, 0.26)))
    grain = np.random.default_rng(553311).random((size, size), dtype=np.float32)
    lum = (0.88 + 0.09 * (macro - 0.5)      # 低频：只留柔和明暗
           + 0.14 * (mid - 0.5)             # 中频：草簇斑驳
           + 0.05 * (micro - 0.5)           # 高频：克制，避免出现电视雪花
           + 0.03 * (grain - 0.5))
    arr = np.stack([lum * 1.01, lum, lum * 0.98], axis=-1)   # 极轻暖调，避免偏蓝
    img = Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(6180)

    def _wrap_line(x0, y0, x1, y1, fill):
        """跨边界时在对侧重复绘制，保证平铺无断茬"""
        for ox in (-size, 0, size):
            for oy in (-size, 0, size):
                d.line((x0 + ox, y0 + oy, x1 + ox, y1 + oy), fill=fill, width=1)

    # 草簇：每簇 3~5 根同向短划，形成可辨认的小草丛（比均匀噪点自然）
    for _ in range(int(size * size / 320)):
        cx, cy = rng.uniform(0, size, 2)
        base_ang = rng.uniform(-0.5, 0.5) + (0.0 if rng.random() < 0.75 else math.pi)
        tint = rng.uniform(0.70, 1.16)
        for _k in range(rng.integers(3, 6)):
            x = cx + rng.uniform(-3.0, 3.0)
            y = cy + rng.uniform(-2.0, 2.0)
            ln = rng.uniform(size * 0.008, size * 0.020)
            ang = base_ang + rng.uniform(-0.35, 0.35)
            v0 = int(255 * lum[int(y) % size, int(x) % size] * tint) & 0xFF
            c = (min(255, v0 + 2), min(255, v0), min(255, v0 - 1))
            _wrap_line(x, y, x + math.cos(ang) * ln, y + math.sin(ang) * ln, c)
    # 土屑 / 碎石：稀疏、低对比的小块，不作为均匀噪点
    for _ in range(int(size * size / 4300)):
        x, y = rng.uniform(0, size, 2)
        r = rng.uniform(size * 0.0035, size * 0.0075)
        base = rng.uniform(0.70, 0.88)
        c = (int(255 * base * 1.04), int(255 * base), int(255 * base * 0.88))
        d.ellipse((x - r, y - r, x + r, y + r), fill=c)
    return img


def _make_vegetation_texture(size=256):
    """植被细节纹理：叶片斑驳与深浅叶簇，用于树冠/灌木/岩块"""
    import numpy as np
    from PIL import Image
    macro = _tileable_noise(size, 4421, ((8, 0.5), (16, 0.5)))
    micro = _tileable_noise(size, 8837, ((16, 0.40), (32, 0.35), (64, 0.25)))
    leaf = _tileable_noise(size, 5150, ((16, 0.45), (32, 0.35), (64, 0.20)))
    grain = np.random.default_rng(992).random((size, size), dtype=np.float32)
    v = 0.60 * micro + 0.25 * macro + 0.15 * grain
    dark = np.clip((0.40 - leaf) / 0.10, 0.0, 1.0)
    lum = (0.84 + 0.16 * v) * (1.0 - 0.09 * dark)
    hue = micro - 0.5
    arr = np.stack([lum * (1.0 + 0.05 * hue), lum,
                    lum * (1.0 - 0.04 * hue)], axis=-1)
    return Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8), "RGB")


def _make_shadow_texture(size=96):
    """接地软阴影：径向渐变黑斑（边缘不规则），用于树/灌木/岩石的接触阴影"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    for i in range(14):
        r = c * (1 - i / 14)
        a = int(92 * (i / 14) ** 1.6)
        d.ellipse((c - r * 1.06, c - r * 0.92, c + r * 1.06, c + r * 0.92),
                  fill=(10, 14, 8, a))
    return img


def _make_rock_texture(size=256):
    """岩石细节纹理：块面斑驳 + 细裂缝，用于陨石与山岩"""
    import numpy as np
    from PIL import Image, ImageDraw
    macro = _tileable_noise(size, 90210)
    micro = _tileable_noise(size, 31337)
    grain = np.random.default_rng(1234).random((size, size), dtype=np.float32)
    v = 0.50 * macro + 0.35 * micro + 0.15 * grain
    lum = 0.74 + 0.26 * v
    arr = np.stack([lum * 1.02, lum, lum * 0.98], axis=-1)
    img = Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(606)
    for _ in range(16):                       # 裂缝：短折线，断开成岩纹
        x, y = rng.uniform(0, size, 2)
        pts = [(x, y)]
        ang = rng.uniform(0, 6.28)
        for _ in range(int(rng.integers(3, 7))):
            ang += rng.uniform(-0.7, 0.7)
            x += math.cos(ang) * rng.uniform(4, 12)
            y += math.sin(ang) * rng.uniform(4, 12)
            pts.append((x, y))
        d.line(pts, fill=(116, 114, 110), width=1)
    return img


def _make_zone_ring_texture(size=192, dashed=False):
    """小地图安全区圈：实线 / 虚线两种"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 8
    box = (pad, pad, size - 1 - pad, size - 1 - pad)
    if dashed:
        for a in range(0, 360, 24):
            d.arc(box, a, a + 13, fill=(238, 248, 255, 225), width=5)
    else:
        d.ellipse((pad - 2, pad - 2, size - pad + 1, size - pad + 1),
                  outline=(120, 195, 255, 110), width=2)
        d.ellipse(box, outline=(215, 240, 255, 240), width=5)
    return img


def _make_zone_vignette_texture(size=192):
    """安全区外屏幕暗角：中心透明、四周红黑暗雾"""
    import numpy as np
    from PIL import Image
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - size / 2, yy - size / 2) / (size * 0.5)
    a = (np.clip((r - 0.28) / 0.72, 0, 1) ** 1.45 * 215).astype(np.uint8)
    arr = np.zeros((size, size, 4), np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2] = 118, 8, 28
    arr[..., 3] = a
    return Image.fromarray(arr, "RGBA")


def _make_soft_ripple_texture(size=256):
    """撞击点光环：取自素材图的光环样式（中心最亮最实、向外一圈光环后渐淡）。

    优先读取 assets/soft_ripple_ring.png（由原图裁切成的圆形光环），
    缺失时退回同款结构的程序化纹理。
    """
    from PIL import Image
    path = resource_path("assets/soft_ripple_ring.png")
    if path and os.path.exists(path):
        try:
            img = Image.open(path).convert("RGBA")
            return img.resize((size, size), Image.LANCZOS) if img.size != (size, size) else img
        except Exception:
            pass
    import numpy as np
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - size / 2 + 0.5, yy - size / 2 + 0.5) / (size * 0.5)
    core = np.clip(1.0 - r / 0.17, 0.0, 1.0) ** 1.5
    ring = np.exp(-((r - 0.58) / 0.105) ** 2) * 0.98
    a = np.clip(core + ring, 0.0, 1.0)
    a *= np.clip((1.0 - r) / 0.16, 0.0, 1.0)
    arr = np.zeros((size, size, 4), np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2] = 255, 250, 240
    arr[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def _make_zone_bolt_texture(size=128, seed=90210):
    """结界闪电：竖直锯齿主干 + 分叉 + 外发光"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    pts = []
    x, y = size * 0.5, size * 0.03
    pts.append((x, y))
    while y < size * 0.97:
        y += rng.uniform(size * 0.05, size * 0.10)
        x += rng.uniform(-size * 0.10, size * 0.10)
        x = max(size * 0.14, min(size * 0.86, x))
        pts.append((x, y))
    for w, a in ((16, 30), (8, 90), (3, 235)):     # 三层叠加出辉光
        d.line(pts, fill=(196, 232, 255, a), width=w, joint="curve")
        d.line(pts, fill=(238, 250, 255, min(255, a + 20)), width=max(2, w // 2))
    for _ in range(3):                              # 分叉
        i = rng.randrange(1, len(pts) - 1)
        bx, by = pts[i]
        bpts = [(bx, by)]
        for _ in range(rng.randint(2, 4)):
            bx += rng.uniform(-size * 0.13, size * 0.13)
            by += rng.uniform(size * 0.04, size * 0.09)
            bpts.append((bx, by))
        d.line(bpts, fill=(180, 224, 255, 130), width=2)
    return img


# ---------------- 程序化几何 ----------------

def _prim_sphere(lat=5, lon=7):
    """单位球图元（半径 1，中心在原点）"""
    verts = [(0.0, 1.0, 0.0)]
    for i in range(1, lat):
        phi = math.pi * i / lat
        sp, cp = math.sin(phi), math.cos(phi)
        for j in range(lon):
            th = 2 * math.pi * j / lon
            verts.append((sp * math.cos(th), cp, sp * math.sin(th)))
    verts.append((0.0, -1.0, 0.0))
    bot = len(verts) - 1
    tris = []
    for j in range(lon):
        tris.append((0, 1 + j, 1 + (j + 1) % lon))
    for i in range(lat - 2):
        r0 = 1 + i * lon
        for j in range(lon):
            a, b = r0 + j, r0 + (j + 1) % lon
            tris += [(a, a + lon, b), (b, a + lon, b + lon)]
    r = 1 + (lat - 2) * lon
    for j in range(lon):
        tris.append((bot, r + (j + 1) % lon, r + j))
    return verts, tris


def _prim_cyl(r, h, seg=7, cy=0.0):
    """圆柱图元（底面中心在 (0,cy,0)，向上延伸 h）"""
    y0, y1 = cy, cy + h
    verts = []
    for y in (y0, y1):
        for i in range(seg):
            th = 2 * math.pi * i / seg
            verts.append((r * math.cos(th), y, r * math.sin(th)))
    verts += [(0.0, y0, 0.0), (0.0, y1, 0.0)]
    cb, ct = len(verts) - 2, len(verts) - 1
    tris = []
    for i in range(seg):
        a, b = i, (i + 1) % seg
        tris += [(a, b, seg + b), (a, seg + b, seg + a)]
        tris.append((cb, b, a))
        tris.append((ct, seg + a, seg + b))
    return verts, tris


def _prim_cone(r, h, seg=7, cy=0.0):
    verts = [(0, cy + h, 0)]
    for i in range(seg):
        th = 2 * math.pi * i / seg
        verts.append((r * math.cos(th), cy, r * math.sin(th)))
    verts.append((0, cy, 0))
    tris = []
    for i in range(seg):
        tris.append((0, 1 + i, 1 + (i + 1) % seg))
        tris.append((seg + 1, 1 + (i + 1) % seg, 1 + i))
    return verts, tris


def _prim_torus(major, minor, seg_major=26, seg_minor=8):
    """圆环图元（位于 XY 平面，法线为 +Z）"""
    verts, tris = [], []
    for i in range(seg_major):
        a = 2 * math.pi * i / seg_major
        ca, sa = math.cos(a), math.sin(a)
        for j in range(seg_minor):
            b = 2 * math.pi * j / seg_minor
            rr = major + minor * math.cos(b)
            verts.append((rr * ca, rr * sa, minor * math.sin(b)))
    for i in range(seg_major):
        for j in range(seg_minor):
            a0 = i * seg_minor + j
            a1 = i * seg_minor + (j + 1) % seg_minor
            b0 = ((i + 1) % seg_major) * seg_minor + j
            b1 = ((i + 1) % seg_major) * seg_minor + (j + 1) % seg_minor
            tris += [(a0, b0, a1), (a1, b0, b1)]
    return verts, tris


def _prim_box(w, h, d):
    """轴对齐长方体图元（底面中心在原点，向上延伸 h）"""
    x, z = w * 0.5, d * 0.5
    verts = [(-x, 0.0, -z), (x, 0.0, -z), (x, 0.0, z), (-x, 0.0, z),
             (-x, h, -z), (x, h, -z), (x, h, z), (-x, h, z)]
    tris = [(0, 1, 2), (0, 2, 3),            # 底
            (4, 6, 5), (4, 7, 6),            # 顶
            (0, 4, 5), (0, 5, 1),            # -Z
            (1, 5, 6), (1, 6, 2),            # +X
            (2, 6, 7), (2, 7, 3),            # +Z
            (3, 7, 4), (3, 4, 0)]            # -X
    return verts, tris


def _prim_octa():
    """正八面体图元（外接半径 1，用于强化道具水晶）"""
    verts = [(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
             (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]
    tris = []
    for ax in (0, 1):                       # ±X
        for ay in (2, 3):                   # ±Y
            for az in (4, 5):               # ±Z
                tris.append((ax, ay, az))
    return verts, tris


def _prim_peak(r, h, seg, rnd):
    """不规则山峰图元：圆周半径与高度抖动，避免规整圆锥的剪纸感"""
    verts = [(rnd.uniform(-0.10, 0.10) * r, h * rnd.uniform(0.95, 1.07),
              rnd.uniform(-0.10, 0.10) * r)]
    for i in range(seg):
        th = 2 * math.pi * i / seg
        rr = r * rnd.uniform(0.78, 1.18)
        yy = h * rnd.uniform(-0.06, 0.06)
        verts.append((rr * math.cos(th), yy, rr * math.sin(th)))
    verts.append((0.0, 0.0, 0.0))
    tris = []
    for i in range(seg):
        tris.append((0, 1 + i, 1 + (i + 1) % seg))
        tris.append((seg + 1, 1 + (i + 1) % seg, 1 + i))
    return verts, tris


# ---------------- 工具 ----------------

def _w(x):
    """像素坐标 → 三维单位"""
    return x * SC


def _mesh(verts, tris, cols, uvs=None):
    """构造 Mesh，顶点色统一转为 Color（RGBA）；可选 UV 用于细节纹理"""
    c4 = [c if hasattr(c, "alpha") else
          (color.Color(c[0], c[1], c[2], c[3]) if len(c) > 3
           else color.Color(c[0], c[1], c[2], 1.0)) for c in cols]
    return Mesh(vertices=verts, triangles=tris, colors=c4, uvs=uvs or [],
                mode="triangle", static=True)


def _additive(e):
    e.setAttrib(ColorBlendAttrib.make(ColorBlendAttrib.MAdd,
                                      ColorBlendAttrib.OOne,
                                      ColorBlendAttrib.OOne))
    e.setTransparency(TransparencyAttrib.MAlpha)
    e.setLightOff(1)
    e.setDepthWrite(False)


_CONE_MESH = None


def _cone_entity(**kwargs):
    """自制圆锥体（ursina 8.3 无内置 cone 模型）：半径0.5 高1 居中"""
    global _CONE_MESH
    if _CONE_MESH is None:
        v, t = _prim_cone(0.5, 1.0, cy=-0.5)
        _CONE_MESH = Mesh(vertices=v, triangles=t, mode="triangle", static=True)
    return Entity(model=_CONE_MESH, **kwargs)


def _unlit(e, depth_write=True):
    e.setLightOff(1)
    if not depth_write:
        e.setDepthWrite(False)


# ---------------- 顶点着色 / 烘焙明暗辅助 ----------------

def _vc(rgb, k=1.0):
    """顶点色：乘以亮度系数（管线线性直通，不做 gamma 变换）"""
    return (min(1.0, rgb[0] * k),
            min(1.0, rgb[1] * k),
            min(1.0, rgb[2] * k))


# 烘焙方向明暗用的太阳光方向（单位向量）
_LN = math.sqrt(0.46 ** 2 + 0.78 ** 2 + 0.42 ** 2)
_LX, _LY, _LZ = 0.46 / _LN, 0.78 / _LN, 0.42 / _LN


def _emit_quad(verts, cols, tris, p4, rgb, k=1.0):
    """追加一个四边形（两个三角形），四点颜色一致"""
    base = len(verts)
    c = _vc(rgb, k)
    for v in p4:
        verts.append(v)
        cols.append(c)
    a, b, c2, d = base, base + 1, base + 2, base + 3
    tris += [(a, b, c2), (b, d, c2)]


def _emit_prim(verts, cols, tris, prim, pos, rgb, scale=1.0, rot=0.0, cy_mid=0.0):
    """追加一个图元（平移/缩放/绕Y旋转），按顶点伪法线烘焙方向明暗"""
    pv, pt = prim
    base = len(verts)
    ca, sa = math.cos(rot), math.sin(rot)
    sx, sy, sz = (scale, scale, scale) if isinstance(scale, (int, float)) else scale
    for x, y, z in pv:
        nx, ny, nz = x, y - cy_mid, z
        nl = math.sqrt(nx * nx + ny * ny + nz * nz)
        d = (nx * _LX + ny * _LY + nz * _LZ) / nl if nl > 1e-6 else 0.0
        k = 0.66 + 0.34 * max(0.0, d * 0.5 + 0.5)
        x, y, z = x * sx, y * sy, z * sz
        verts.append((pos[0] + x * ca - z * sa, pos[1] + y, pos[2] + x * sa + z * ca))
        cols.append(_vc(rgb, k))
    tris += [(a + base, b + base, c + base) for a, b, c in pt]


# ---------------- 主类 ----------------

class Sky3DGame(Entity):
    def __init__(self, test_mode=False, seed=None):
        super().__init__()
        self.test_mode = test_mode
        self.invincible = test_mode      # 测试模式免碰撞死亡（死亡逻辑已单独验证）
        self.rng = random.Random(20261030 if test_mode else seed)
        self.cam_dist = CAM_DIST
        self._fov_base = camera.fov       # 默认视野（冲刺时临时扩张）
        self.orbit_last = None
        self.paused = False
        self.pause_menu = False
        self.over = False
        self.death_reason = ""
        self._anim_t = 0.0

        load_settings()
        self.mouse_sens = float(SETTINGS.get("sky_sens", MOUSE_SENS))
        self.invert_y = bool(int(SETTINGS.get("sky_invert_y", 0)))
        self.raw_mouse = _RawMouse()
        self._init_audio()
        self._init_font()
        self._generate_world()
        self._build_scene()
        self._build_soft_walls()
        self._build_snake()
        self._build_hud()
        self.reset()
        if self.test_mode:               # 统计 update() 分段耗时（诊断 CPU 瓶颈）
            self._sec = {"anim": 0.0, "move": 0.0, "fruit": 0.0,
                         "col": 0.0, "snake": 0.0, "vis": 0.0}
            self._prof_n = 0

    # ---------- 音频 / 字体 ----------

    def _init_audio(self):
        try:
            self.snd_eat = Audio("assets/audio/eat.wav", autoplay=False, loop=False,
                                 volume=sound_volume("eat"))
            self.snd_death = Audio("assets/audio/death.wav", autoplay=False, loop=False,
                                   volume=sound_volume("death"))
            self.bgm = Audio("assets/audio/game.wav", autoplay=False, loop=True,
                             volume=sound_volume("bgm"))
        except Exception:
            self.snd_eat = self.snd_death = self.bgm = None

    def _play(self, snd):
        if snd:
            try:
                snd.play()
            except Exception:
                pass

    def _init_font(self):
        # 通过 Text.default_font 全局生效（含 Button 内部文本）；
        # FontPool 从 asset_folder 加载 assets/fonts/msyh.ttc（exe 时为 _MEIPASS）
        self.font = None
        try:
            Text.default_font = "assets/fonts/msyh.ttc"
        except Exception:
            pass

    # ---------- 世界生成（与 2D 版同规则） ----------

    def _blob(self, cx, cy, r, n):
        rng = self.rng
        return [(cx + math.cos(2 * math.pi * i / n + rng.uniform(-0.12, 0.12))
                 * r * rng.uniform(0.72, 1.12),
                 cy + math.sin(2 * math.pi * i / n + rng.uniform(-0.12, 0.12))
                 * r * rng.uniform(0.72, 1.12)) for i in range(n)]

    @staticmethod
    def _bbox(pts):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def _generate_world(self):
        w, band, rng = WORLD, LAND_BAND, self.rng
        self.land, self.walls = [], []
        for pts in (
            [(0, 0), (w, 0), (w, band), (0, band)],
            [(0, w - band), (w, w - band), (w, w), (0, w)],
            [(0, band), (band, band), (band, w - band), (0, w - band)],
            [(w - band, band), (w, band), (w, w - band), (w - band, w - band)],
        ):
            self.land.append({"pts": pts, "bbox": self._bbox(pts)})
        # 悬浮岛屿（大图增加到 16 座，保持探索密度）
        for _ in range(16):
            for _try in range(40):
                r = rng.randint(90, 300)
                cx = rng.randint(band + r + 80, w - band - r - 80)
                cy = rng.randint(band + r + 80, w - band - r - 80)
                if math.hypot(cx - w / 2, cy - w / 2) < r + SKY_SPAWN_CLEAR:
                    continue
                if any(math.hypot(cx - s["cx"], cy - s["cy"]) < (r + s["r"]) * 1.15
                       for s in self.land[4:]):
                    continue
                pts = self._blob(cx, cy, r, rng.randint(8, 14))
                self.land.append({"pts": pts, "bbox": self._bbox(pts), "cx": cx, "cy": cy, "r": r})
                break
        # 天空石墙（大图增加到 30 处）
        for _ in range(30):
            for _try in range(40):
                r = rng.randint(28, 80)
                cx = rng.randint(band + r + 60, w - band - r - 60)
                cy = rng.randint(band + r + 60, w - band - r - 60)
                if math.hypot(cx - w / 2, cy - w / 2) < r + SKY_SPAWN_CLEAR * 0.75:
                    continue
                if any(math.hypot(cx - s["cx"], cy - s["cy"]) < r + s["r"] + 30
                       for s in self.land[4:]):
                    continue
                if any(math.hypot(cx - s["cx"], cy - s["cy"]) < r + s["r"] + 24
                       for s in self.walls):
                    continue
                pts = self._blob(cx, cy, r, rng.randint(6, 9))
                self.walls.append({"pts": pts, "bbox": self._bbox(pts), "cx": cx, "cy": cy, "r": r})
                break

    # ---------- 碰撞（AABB 预筛 + 精确测试 + 扫掠路径检测） ----------

    @staticmethod
    def _pt_in_poly(x, y, pts):
        inside, j = False, len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
                inside = not inside
            j = i
        return inside

    def _column_top(self, px, py):
        """像素坐标处的地表高度（三维单位，按渲染网格三角面插值）；界外返回 None。
        直接取视觉地表而非体素列顶，避免碰撞面与看到的坡面错位。"""
        n = self._surf_n
        fx, fz = px * SC / VOXEL_B, py * SC / VOXEL_B
        if not (0.0 <= fx < n and 0.0 <= fz < n):
            return None
        i, j = int(fx), int(fz)
        u, v = fx - i, fz - j
        g = self._surf_h
        ha, hb, hc = g[i][j], g[i][j + 1], g[i + 1][j]
        if u + v <= 1.0:                        # 三角形 a-c2-b
            return ha + (hc - ha) * u + (hb - ha) * v
        hd = g[i + 1][j + 1]                    # 三角形 b-c2-d
        return hd + (hc - hd) * (1.0 - v) + (hb - hd) * (1.0 - u)

    def _terrain_penetration(self, px, py, ph):
        """球形蛇头相对地表的最大穿入深度（三维单位）；<=0 表示未接触"""
        if ph - HEAD_COL_R > TERRAIN_MAX:       # 明显高于全部地表，快速跳过
            return 0.0
        rpx = HEAD_COL_R / SC
        worst = 0.0
        # 同心采样：越靠近边缘，允许的地表高度越高（球面下缘抬升）
        for ring, cnt in ((0.0, 1), (0.55, 8), (0.85, 8), (1.0, 8)):
            drop = HEAD_COL_R * math.sqrt(max(0.0, 1.0 - ring * ring))
            for k in range(cnt):
                a = k * math.pi / 4.0
                top = self._column_top(px + math.cos(a) * ring * rpx,
                                       py + math.sin(a) * ring * rpx)
                if top is not None:
                    d = top - (ph - drop)
                    if d > worst:
                        worst = d
        return worst

    def _terrain_sweep(self, px0, py0, ph0, px1, py1, ph1):
        """扫掠本帧移动段，返回 (首次穿入比例, 安全比例, 需抬升到的线高)。
        未撞到地形返回 (None, 0.0, 0.0)。采样步长小于蛇头直径，冲刺也不会穿过薄山脊。"""
        if min(ph0, ph1) - HEAD_COL_R > TERRAIN_MAX:
            return None, 0.0, 0.0
        dx, dy, dh = px1 - px0, py1 - py0, ph1 - ph0
        moved = math.sqrt((dx * SC) ** 2 + (dy * SC) ** 2 + dh * dh)
        steps = max(1, min(32, int(moved / SWEEP_STEP) + 1))
        hit_f, safe_f, lift = None, 0.0, 0.0
        for s in range(1, steps + 1):
            f = s / steps
            sh = ph0 + dh * f
            pen = self._terrain_penetration(px0 + dx * f, py0 + dy * f, sh)
            if pen <= 0.0:
                continue
            if hit_f is None:
                hit_f, safe_f = f, (s - 1) / steps
            lift = max(lift, sh + pen + 0.05)
        return hit_f, safe_f, lift

    def _random_sky_point(self):
        """随机果实点：水平在边界带内，高度悬于地表之上"""
        w, band = WORLD, LAND_BAND
        for _ in range(200):
            x = self.rng.randint(band + 40, w - band - 40)
            y = self.rng.randint(band + 40, w - band - 40)
            top = self._column_top(x, y)
            if top is None:
                continue
            if self.rng.random() < 0.3:      # 三成悬于高空，鼓励向上探索
                h = self.rng.uniform(top + 4.0, MAX_ALT - 2.0)
            else:
                h = top + self.rng.uniform(1.5, 4.5)
            if h > MAX_ALT - 2.0:
                continue
            if self._near_meteor(x, y, h):
                continue
            return x, y, h
        return None

    def _ring_point(self):
        """光环位置：悬浮于地表之上、避开陨石，开口下缘不插进地形"""
        w, band = WORLD, LAND_BAND
        for _ in range(200):
            x = self.rng.randint(band + 70, w - band - 70)
            y = self.rng.randint(band + 70, w - band - 70)
            if math.hypot(x - w / 2, y - w / 2) < SKY_SPAWN_CLEAR * 0.6:
                continue
            top = self._column_top(x, y)
            if top is None:
                continue
            h = max(4.4, top + RING_RADIUS + 0.9)      # 环底离地留余量
            if h > MAX_ALT - RING_RADIUS - 0.4:
                continue
            if self._near_meteor(x, y, h, RING_RADIUS + 1.2):
                continue
            return x, y, h
        return None

    def _powerup_point(self):
        """道具位置：优先出现在玩家前方航道上，便于主动拾取"""
        w, band = WORLD, LAND_BAND
        for _ in range(80):
            ang = self.angle + self.rng.uniform(-1.0, 1.0)
            dist = self.rng.uniform(520, 1050)
            x = self.x + math.cos(ang) * dist
            y = self.y + math.sin(ang) * dist
            if not (band + 45 < x < w - band - 45 and band + 45 < y < w - band - 45):
                continue
            top = self._column_top(x, y)
            if top is None:
                continue
            h = max(top + 1.8, min(MAX_ALT - 2.0,
                                   self.h + self.rng.uniform(-3.6, 4.6)))
            if h <= top + 1.5 or self._near_meteor(x, y, h, 1.5):
                continue
            return x, y, h
        return self._random_sky_point()

    def _near_meteor(self, px, py, h, margin=2.2):
        """点是否落在地表以上的陨石体积内（px/py 像素坐标，h 三维高度，margin 三维单位）"""
        for m in getattr(self, "meteors", ()):
            dx = (px - m["x"]) * SC
            dz = (py - m["y"]) * SC
            dh = h - m["h"]
            rr = m.get("cr", m["r"]) + margin
            if dx * dx + dz * dz + dh * dh < rr * rr:
                return True
        return False

    # ---------- 场景搭建 ----------

    def _build_scene(self):
        scene.fog_color = color.rgb(0.62, 0.73, 0.88)
        scene.fog_density = 0.0031
        scene.fog.setExpDensity(0.0031)     # ursina 标量赋值未接 Panda3D 指数雾，手动启用
        window.color = color.rgb(0.62, 0.73, 0.88)

        self._build_materials()
        self._build_sky_dome()
        self._build_terrain()
        self._build_mountains()
        self._build_vegetation()
        self._build_clouds()
        self._build_meteors()
        self._build_rainbow()
        self._build_sun_and_rays()
        self._build_stars()
        self._build_landmarks()
        self._build_pegasus()
        self._build_fruits()
        self._build_rings()
        self._build_powerups()

        # 光照：平行光（太阳，实时阴影）+ 环境光；SKY3D_NOSHADOW=1 可关阴影诊断帧率
        from ursina import DirectionalLight, AmbientLight
        use_shadow = os.environ.get("SKY3D_NOSHADOW") != "1"
        self.sun_light = DirectionalLight(shadows=use_shadow,
                                          shadow_map_resolution=Vec3(2048, 2048, 0))
        self.sun_light.look_at(Vec3(1.2, -2.0, 1.0))
        try:
            self.sun_light._light.getLens().setFilmSize(90, 90)
            self.sun_light._light.getLens().setNearFar(10, 160)
        except Exception:
            pass
        AmbientLight(color=color.rgba(0.55, 0.62, 0.85, 0.42))

    def _build_materials(self):
        """程序化材质纹理：地表 / 植被 / 岩石细节；重复平铺 + mipmap 防远处闪烁"""
        from ursina import Texture

        def _tex(img):
            t = Texture(img, filtering="mipmap")
            t.repeat = True
            try:
                t._texture.setAnisotropicDegree(8)   # 掠射角保持细节清晰
            except Exception:
                pass
            return t

        self.ground_tex = _tex(_make_ground_texture())
        try:
            self.ground_tex._texture.setAnisotropicDegree(16)   # 地面掠射角多，单给更高各向异性
        except Exception:
            pass
        self.veg_tex = _tex(_make_vegetation_texture())
        self.rock_tex = _tex(_make_rock_texture())
        self.shadow_tex = _tex(_make_shadow_texture())

    def _load_sky_texture(self):
        """全景天空贴图（assets/sky_panorama.png，2048×1024 等距柱状）。
        缺文件或解码失败时返回 None，由顶点色渐变兜底。"""
        path = resource_path(os.path.join("assets", "sky_panorama.png"))
        if not os.path.exists(path):
            return None
        try:
            t = Texture(path, filtering="mipmap")
            try:
                t._texture.setAnisotropicDegree(4)
            except Exception:
                pass
            return t
        except Exception:
            return None

    def _build_sky_dome(self):
        """全球穹顶：优先贴全景天空贴图（天顶晴空 → 地平线薄雾 → 云海），
        贴图缺失时退回原顶点色渐变"""
        seg, rings, R = 48, 24, 900
        verts, tris, cols, uvs = [], [], [], []
        top = (0.10, 0.28, 0.72)
        mid = (0.34, 0.56, 0.90)
        low = (0.72, 0.82, 0.93)
        sea_hi = (0.58, 0.68, 0.79)
        sea_lo = (0.34, 0.44, 0.55)
        dome_tex = self._load_sky_texture()      # None → 走渐变兜底
        for j in range(rings + 1):
            phi = math.pi * j / rings           # 全球：0..π
            y = R * math.cos(phi)
            t = j / rings
            if t <= 0.55:                       # 天顶 → 地平线
                k = t / 0.55
                c = tuple(top[m] * (1 - k) + (mid[m] if k < 0.6 else low[m]) * k
                          for m in range(3))
            else:                               # 地平线之下 → 深邃云海
                k = (t - 0.55) / 0.45
                c = tuple(sea_hi[m] * (1 - k) + sea_lo[m] * k for m in range(3))
            v = 1.0 - t                         # 贴图 v：天顶 1 → 云海底 0
            for i in range(seg):
                th = 2 * math.pi * i / seg
                verts.append((R * math.sin(phi) * math.cos(th), y, R * math.sin(phi) * math.sin(th)))
                # 有贴图时顶点色改白，避免与原渐变相乘压暗画面
                cols.append((1.0, 1.0, 1.0, 1.0) if dome_tex else c)
                uvs.append((i / seg, v))
        for j in range(rings):
            for i in range(seg):
                a = j * seg + i
                b = j * seg + (i + 1) % seg
                c = (j + 1) * seg + i
                d = (j + 1) * seg + (i + 1) % seg
                tris += [(a, b, c), (b, d, c)]
        dome = Entity(model=_mesh(verts, tris, cols, uvs if dome_tex else None),
                      eternal=True)
        if dome_tex:
            dome.texture = dome_tex         # 贴图与地平线在穹顶赤道对齐
        dome.setTwoSided(True)          # 从穹顶内部看必须双面渲染
        _unlit(dome)
        dome.setFogOff(1)
        self.dome = dome

    def _build_terrain(self):
        """真实风格开阔大陆（合并为单个 Mesh）：
        平滑高度场地形 —— 连绵草地、圆润山丘（碰撞岛）、灰白岩柱（石墙）、
        世界边缘红岩高原（死亡区警示色）；顶点色按高度/坡度混合草/岩/雪。
        碰撞数据（_vox_top / _vox_kind）不变，仅视觉层平滑化。"""
        B = VOXEL_B
        n = int(WORLD * SC / B)                     # 128×128 列
        rng = random.Random(440502)
        # 值噪声：8×8 随机栅格双线性插值
        gn = 8
        ggrid = [[rng.uniform(0, 1) for _ in range(gn + 1)] for _ in range(gn + 1)]
        vary = [[0.9 + 0.2 * rng.uniform(0, 1) for _ in range(n)] for _ in range(n)]

        def _h(i, j):
            fx, fz = i / n * gn, j / n * gn
            x0, z0 = min(int(fx), gn - 1), min(int(fz), gn - 1)
            tx, tz = fx - x0, fz - z0
            v = (ggrid[x0][z0] * (1 - tx) * (1 - tz) + ggrid[x0 + 1][z0] * tx * (1 - tz)
                 + ggrid[x0][z0 + 1] * (1 - tx) * tz + ggrid[x0 + 1][z0 + 1] * tx * tz)
            return int(v * 4 + 0.5)

        def _hash(ii, jj):                          # 无周期伪随机（草地斑驳/抖动）
            h = math.sin(ii * 12.9898 + jj * 78.233) * 43758.5453
            return h - math.floor(h)

        # 分类：g=普通地形 m=山丘(碰撞岛) p=边缘高原(死亡区) w=岩柱(石墙)
        kind = [["g"] * n for _ in range(n)]
        top = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                px = (i + 0.5) * B / SC             # 列中心像素坐标
                py = (j + 0.5) * B / SC
                top[i][j] = GROUND_BASE + _h(i, j) * GROUND_STEP
                if px < LAND_BAND or px > WORLD - LAND_BAND or \
                   py < LAND_BAND or py > WORLD - LAND_BAND:
                    kind[i][j] = "p"
                    top[i][j] = PLATEAU_TOP
                    continue
                hit = False
                for s in self.land[4:]:
                    x0, y0, x1, y1 = s["bbox"]
                    if x0 <= px <= x1 and y0 <= py <= y1 and \
                       self._pt_in_poly(px, py, s["pts"]):
                        kind[i][j] = "m"
                        hit = True
                        break
                if hit:
                    continue
                for s in self.walls:
                    x0, y0, x1, y1 = s["bbox"]
                    if x0 <= px <= x1 and y0 <= py <= y1 and \
                       self._pt_in_poly(px, py, s["pts"]):
                        kind[i][j] = "w"
                        top[i][j] = PILLAR_TOP
                        break
        # 山丘梯层：四邻皆山丘 → 主峰顶，否则边缘低一层
        for i in range(n):
            for j in range(n):
                if kind[i][j] == "m":
                    inner = all(0 <= i + di < n and 0 <= j + dj < n
                                and kind[i + di][j + dj] == "m"
                                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                    top[i][j] = MOUNT_TOP if inner else MOUNT_LOW

        GRASS_A = (0.26, 0.53, 0.16)    # 草地·葱郁深绿
        GRASS_B = (0.42, 0.58, 0.21)    # 草地·中间调
        GRASS_C = (0.56, 0.57, 0.25)    # 草地·偏干黄绿
        ROCK = (0.46, 0.45, 0.43)       # 灰岩
        ROCK_P = (0.46, 0.33, 0.25)     # 红岩（高原死亡区）
        ROCK_W = (0.38, 0.39, 0.42)     # 灰岩柱（石墙，明显区别于雪）
        SNOW = (0.90, 0.92, 0.96)       # 雪顶

        # 草地色斑：两级平滑值噪声（大块干湿交替），避免逐格白噪声被平均成一片死绿
        gn1 = [[rng.uniform(0, 1) for _ in range(9)] for _ in range(9)]
        gn2 = [[rng.uniform(0, 1) for _ in range(25)] for _ in range(25)]

        def _snoise(grid, cells, ii, jj):
            fx, fz = ii / n * cells, jj / n * cells
            x0, z0 = min(int(fx), cells - 1), min(int(fz), cells - 1)
            tx, tz = fx - x0, fz - z0
            tx, tz = tx * tx * (3 - 2 * tx), tz * tz * (3 - 2 * tz)
            x1, z1 = (x0 + 1) % cells, (z0 + 1) % cells
            return (grid[x0][z0] * (1 - tx) * (1 - tz) + grid[x1][z0] * tx * (1 - tz)
                    + grid[x0][z1] * (1 - tx) * tz + grid[x1][z1] * tx * tz)

        ccol = [[None] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                k = kind[i][j]
                if k == "p":
                    c = tuple(ROCK_P[ch] * vary[i][j] for ch in range(3))
                elif k == "w":
                    c = tuple(ROCK_W[ch] * vary[i][j] for ch in range(3))
                elif k == "m":
                    t01 = (top[i][j] - MOUNT_LOW) / (MOUNT_TOP - MOUNT_LOW)
                    m = 0.15 + 0.35 * t01
                    c = tuple(GRASS_A[ch] * (1 - m) + ROCK[ch] * m for ch in range(3))
                else:
                    # 0..1：大块斑块（8 格）+ 中频（24 格），叠一点逐格抖动
                    p = 0.62 * _snoise(gn1, 8, i, j) + 0.30 * _snoise(gn2, 24, i, j)
                    p = min(1.0, max(0.0, p + 0.16 * (_hash(i * 7, j * 13) - 0.5)))
                    if p < 0.5:                       # 深绿 → 中间调
                        q = p * 2.0
                        c3 = [GRASS_A[ch] * (1 - q) + GRASS_B[ch] * q for ch in range(3)]
                    else:                             # 中间调 → 偏干黄绿
                        q = (p - 0.5) * 2.0
                        c3 = [GRASS_B[ch] * (1 - q) + GRASS_C[ch] * q for ch in range(3)]
                    c = tuple(c3[ch] * vary[i][j] for ch in range(3))
                ccol[i][j] = c

        # 平滑高度场：先算全网格高度，再按太阳方向烘焙坡面明暗/环境遮蔽，最后写顶点色
        n1 = n + 1
        surf = [[0.0] * n1 for _ in range(n1)]      # 渲染网格顶点高度（碰撞/相机按三角面插值）
        for i in range(n1):
            for j in range(n1):
                adj = [(i + di, j + dj) for di in (-1, 0) for dj in (-1, 0)
                       if 0 <= i + di < n and 0 <= j + dj < n]
                hv = sum(top[a][b] for a, b in adj) / len(adj)
                hv += (0.22 * math.sin(i * 0.90 + j * 0.55) +
                       0.11 * math.sin(i * 2.10 - j * 1.70))   # 大起伏
                # 细碎起伏：草丛/小土包级别（±9cm），让烘焙明暗有颗粒感而不是塑料平面
                hv += (0.065 * math.sin(i * 4.30 + j * 2.85) +
                       0.045 * math.cos(i * 2.60 - j * 5.40) +
                       0.055 * (_hash(i * 5 + 31, j * 7 + 17) - 0.5))
                surf[i][j] = hv

        # ---- 光线步进烘焙太阳遮挡（等效“地形自投影”的光线追踪）----
        # 沿太阳方向逐段前进，采样高度场；若射线被地形挡住则压暗该顶点。
        # 太阳方向固定，故一次性烘焙，运行时零开销。
        import numpy as np
        H = np.asarray(surf, dtype=np.float32)
        sun_h = math.hypot(_LX, _LZ)                 # 太阳水平分量
        sdx, sdz = _LX / sun_h, _LZ / sun_h          # 水平步进方向（每单位）
        sslope = _LY / sun_h                         # 水平走 1 单位射线抬升的高度
        occ = np.zeros_like(H)
        gi = np.arange(n1, dtype=np.float32)
        for st in range(1, 23):                      # 22 段 × 1.9 单位 ≈ 42 单位射程
            d = st * 1.9
            px = np.clip(gi + sdx * d / B, 0.0, n1 - 1.001)
            pz = np.clip(gi + sdz * d / B, 0.0, n1 - 1.001)
            ix = np.floor(px).astype(np.int32)
            iz = np.floor(pz).astype(np.int32)
            tx = (px - ix)[:, None]
            tz = (pz - iz)[None, :]
            x1, z1 = np.minimum(ix + 1, n1 - 1), np.minimum(iz + 1, n1 - 1)
            h00 = H[np.ix_(ix, iz)]
            h10 = H[np.ix_(x1, iz)]
            h01 = H[np.ix_(ix, z1)]
            h11 = H[np.ix_(x1, z1)]
            hs = (h00 * (1 - tx) * (1 - tz) + h10 * tx * (1 - tz) +
                  h01 * (1 - tx) * tz + h11 * tx * tz)
            ray = H + d * sslope                     # 该段射线高度
            occ = np.maximum(occ, np.clip((hs - ray) * 0.9, 0.0, 1.0))
        # 3×3 均值柔化，避免顶点级硬边（阴影边缘自然过渡）
        occ = (occ + np.roll(occ, 1, 0) + np.roll(occ, -1, 0) +
               np.roll(occ, 1, 1) + np.roll(occ, -1, 1) +
               np.roll(np.roll(occ, 1, 0), 1, 1) + np.roll(np.roll(occ, 1, 0), -1, 1) +
               np.roll(np.roll(occ, -1, 0), 1, 1) + np.roll(np.roll(occ, -1, 0), -1, 1)) / 9.0
        self._sun_occ = occ
        verts, cols, tris = [], [], []
        for i in range(n1):
            for j in range(n1):
                adj = [(i + di, j + dj) for di in (-1, 0) for dj in (-1, 0)
                       if 0 <= i + di < n and 0 <= j + dj < n]
                ts = [top[a][b] for a, b in adj]
                hv = surf[i][j]
                cr = [sum(ccol[a][b][ch] for a, b in adj) / len(adj) for ch in range(3)]
                slope = (max(ts) - min(ts)) / B if len(ts) > 1 else 0.0
                near_p = any(kind[a][b] == "p" for a, b in adj)
                rk = min(0.75, max(0.0, slope - 1.2) * 0.45)   # 缓坡保留草色，陡坡露岩
                if rk > 0.0:
                    rc = ROCK_P if near_p else ROCK
                    cr = [c * (1 - rk) + r * rk for c, r in zip(cr, rc)]
                jit = 0.94 + 0.12 * _hash(i + 53, j + 97)
                cr = [min(1.0, c * jit) for c in cr]
                if hv > 2.5 and slope < 0.35 and not near_p and \
                   any(kind[a][b] == "m" for a, b in adj):
                    sk = min(0.6, (hv - 2.5) / 0.2)
                    cr = [c * (1 - sk) + s * sk for c, s in zip(cr, SNOW)]
                # 烘焙坡面明暗（与树木/岩石同一光照方向）+ 谷地环境遮蔽
                i0, i1 = max(0, i - 1), min(n1 - 1, i + 1)
                j0, j1 = max(0, j - 1), min(n1 - 1, j + 1)
                dhx = (surf[i1][j] - surf[i0][j]) / ((i1 - i0) * B)
                dhz = (surf[i][j1] - surf[i][j0]) / ((j1 - j0) * B)
                nl = math.sqrt(dhx * dhx + 1.0 + dhz * dhz)
                lit = max(0.0, (-dhx * _LX + _LY - dhz * _LZ) / nl)
                shade = 0.58 + 0.42 * lit        # 坡面明暗对比略加强，草地起伏更立体
                ring = [surf[i + di][j + dj] for di, dj in
                        ((-2, 0), (2, 0), (0, -2), (0, 2),
                         (-1, -1), (-1, 1), (1, -1), (1, 1))
                        if 0 <= i + di < n1 and 0 <= j + dj < n1]
                if ring:
                    shade *= 1.0 - 0.12 * min(
                        1.0, max(0.0, (sum(ring) / len(ring) - hv) / 0.9))
                warm = 0.035 * lit              # 迎光偏暖、背光偏冷的日光色温
                so = float(occ[i][j])           # 太阳遮挡（地形自投影）
                shade *= 1.0 - 0.58 * so         # 阴影区压暗
                cr = [cr[0] * (shade + warm), cr[1] * shade,
                      cr[2] * (shade - warm * 0.6 + 0.05 * so)]   # 阴影偏冷，模拟天光
                verts.append((i * B, hv, j * B))
                cols.append(_vc(cr))
        for i in range(n):
            for j in range(n):
                a = i * n1 + j
                b = i * n1 + j + 1
                c2 = (i + 1) * n1 + j
                d = (i + 1) * n1 + j + 1
                tris += [(a, c2, b), (b, c2, d)]
        # 远方地平线延展面（融入雾霭）
        e2 = 700.0
        wy = WORLD * SC
        _emit_quad(verts, cols, tris,
                   [(-e2, GROUND_BASE - 0.55, -e2), (-e2, GROUND_BASE - 0.55, wy + e2),
                    (wy + e2, GROUND_BASE - 0.55, -e2), (wy + e2, GROUND_BASE - 0.55, wy + e2)],
                   (0.34, 0.50, 0.26), 1.0)
        # （远山改由 _build_mountains() 单独构建：分层山脊 + 大气透视 + 雪线）
        uvs = [(vx / GROUND_UV, vz / GROUND_UV) for vx, _vy, vz in verts]
        ent = Entity(model=_mesh(verts, tris, cols, uvs), eternal=True)
        ent.texture = self.ground_tex       # 细节纹理与顶点色相乘：草地/岩面颗粒感
        ent.setTwoSided(True)
        _unlit(ent)
        self._vox_top = top
        self._vox_kind = kind
        self._surf_h = surf          # 与渲染三角面一致的顶点高度（碰撞/相机共用）
        self._surf_n = n

    def _build_mountains(self):
        """远山：三层山脊 + 大气透视 + 雪线。
        每座山由裙边/山脚/山腰/肩部四级环构成，顶点色按太阳方位做朝向明暗，
        底部溶进地平线霭色，避免"纸片三角"的硬边与平板感。"""
        rng = random.Random(3111)
        wy = WORLD * SC
        ex = wy / 2.0
        sun_h = math.hypot(_LX, _LZ) or 1.0
        sxd, szd = _LX / sun_h, _LZ / sun_h
        HAZE = (0.74, 0.82, 0.93)              # 地平线霭色（略亮于雾色）
        SNOW = (0.93, 0.95, 0.98)
        verts, cols, tris = [], [], []

        def jit(k, s=1.0):
            h = math.sin(k * 12.9898 + s * 78.233) * 43758.5453
            return h - math.floor(h)

        def peak(cx, cz, r, h, rock, haze, snow, seg, seed):
            """单座山峰：环 + 顶点抖动构成不规则锥体，按三角面平涂着色（低多边形棱面感）"""
            base_y = GROUND_BASE - 1.0
            top = (cx + (jit(seed, 3) - 0.5) * r * 0.34, base_y + h,
                   cz + (jit(seed, 7) - 0.5) * r * 0.34)
            rings = []
            # (相对高度, 相对半径, 半径抖动幅度)：越往上收越紧，避免山顶被抖成薄针
            for fy, fr, amp in ((-0.34, 1.24, 0.30), (0.0, 1.0, 0.26),
                                (0.34, 0.74, 0.18), (0.62, 0.46, 0.12),
                                (0.86, 0.16, 0.07)):
                ring = []
                for k in range(seg):
                    th = 2 * math.pi * k / seg
                    rr = r * fr * (1.0 + amp * (jit(seed + k, 11) * 2 - 1))
                    vy = base_y + h * fy * (0.94 + 0.10 * (jit(seed + k, 19) * 2 - 1))
                    ring.append((cx + math.cos(th) * rr, vy, cz + math.sin(th) * rr))
                rings.append(ring)

            def face(p0, p1, p2):
                """每个三角面单色：法线朝向决定明暗，高度决定雾霭与雪"""
                ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
                vx, vy2, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
                nx_, ny_, nz_ = uy * vz - uz * vy2, uz * vx - ux * vz, ux * vy2 - uy * vx
                ln = math.sqrt(nx_ * nx_ + ny_ * ny_ + nz_ * nz_) or 1.0
                nx_, ny_, nz_ = nx_ / ln, ny_ / ln, nz_ / ln
                mx = (p0[0] + p1[0] + p2[0]) / 3 - cx
                mz = (p0[2] + p1[2] + p2[2]) / 3 - cz
                if nx_ * mx + nz_ * mz < 0:             # 法线统一朝外
                    nx_, ny_, nz_ = -nx_, -ny_, -nz_
                lit = max(0.0, nx_ * _LX + ny_ * _LY + nz_ * _LZ)
                fy = ((p0[1] + p1[1] + p2[1]) / 3 - base_y) / max(1e-3, h)
                hz = haze * (0.42 + 0.58 * max(0.0, 1.0 - fy / 0.30) ** 1.5)
                if snow and fy >= 0.84:
                    c0 = (SNOW[0] * (0.60 + 0.44 * lit), SNOW[1] * (0.64 + 0.42 * lit),
                          SNOW[2] * (0.70 + 0.38 * lit))
                    hz *= 0.30
                else:
                    sh = 0.56 + 0.54 * lit
                    c0 = (rock[0] * sh, rock[1] * sh, rock[2] * sh)
                col = tuple(c0[ch] * (1 - hz) + HAZE[ch] * hz for ch in range(3))
                i0 = len(verts)
                verts.extend((p0, p1, p2))
                cols.extend((col, col, col))
                tris.extend(((i0, i0 + 1, i0 + 2),))

            for s in range(len(rings) - 1):
                ra, rb = rings[s], rings[s + 1]
                for k in range(seg):
                    k2 = (k + 1) % seg
                    face(ra[k], rb[k], ra[k2])
                    face(ra[k2], rb[k], rb[k2])
            for k in range(seg):                       # 肩部扇面收向峰顶
                k2 = (k + 1) % seg
                face(rings[-1][k], top, rings[-1][k2])

        # 三层：越远越淡（大气透视），近层更肯定、远层只剩剪影
        layers = (
            (80, 54, 26, 44, 34, 56, (0.44, 0.50, 0.60), 0.62, True),
            (46, 46, 19, 33, 30, 48, (0.38, 0.44, 0.55), 0.44, True),
            (10, 36, 11, 21, 26, 38, (0.31, 0.37, 0.47), 0.30, False),
        )
        for off_d, cnt, hmin, hmax, rmin, rmax, rock, haze, snow_on in layers:
            phase = off_d * 0.37
            for i in range(cnt):
                a = 2 * math.pi * i / cnt + rng.uniform(-0.09, 0.09)
                if jit(i, off_d) < 0.16:            # 留出豁口，避免一圈均匀排满
                    continue
                # 大尺度起伏：有的段落长成高山群，有的只是低矮丘陵
                t01 = i / cnt
                v = (0.62 * math.sin(t01 * 12.56 * 2.3 + phase) +
                     0.38 * math.sin(t01 * 12.56 * 5.6 + phase * 2.1))
                nh = 0.42 + 0.80 * max(0.0, 0.5 + 0.5 * v)
                rr = ex + off_d + rng.uniform(-14, 14)
                bx, bz = ex + math.cos(a) * rr, ex + math.sin(a) * rr
                ph = rng.uniform(hmin, hmax) * nh
                pr = rng.uniform(rmin, rmax) * (0.80 + 0.30 * nh)
                tx, tz = -math.sin(a), math.cos(a)          # 沿山脊的切向
                nx, nz = math.cos(a), math.sin(a)           # 指向山体内部的法向
                seg = 13 + int(jit(i * 3, 5) * 5)
                snow = snow_on and ph > hmin + (hmax - hmin) * 0.72
                peak(bx, bz, pr, ph, rock, haze, snow, seg, int(a * 900))
                # 主峰周围四座伴峰：高度错落、基座重叠，合成锯齿状山脊而不是孤立三角
                for s, (so, sn, sh) in enumerate((
                        (-0.46, 0.05, (0.62, 0.82)),
                        (0.44, 0.06, (0.55, 0.74)),
                        (0.10, 0.46, (0.70, 0.92)),
                        (-0.28, -0.42, (0.42, 0.60)))):
                    peak(bx + tx * pr * so + nx * pr * sn,
                         bz + tz * pr * so + nz * pr * sn,
                         pr * rng.uniform(0.55, 0.86),
                         ph * rng.uniform(*sh),
                         rock, haze, False,          # 雪只落在主峰，伴峰不带雪冠
                         11 + int(jit(i * 7 + s, 9) * 4), int(a * 900) + s * 37 + 11)
        ent = Entity(model=_mesh(verts, tris, cols), eternal=True)
        ent.setTwoSided(True)
        ent.setFogOff(1)        # 雾已按层烘焙进顶点色，关闭实时雾避免双重叠加
        _unlit(ent)
        self.mountain_entity = ent

    def _build_vegetation(self):
        """真实风格植被：针叶松 / 阔叶树 / 灌木 / 花草，
        散布于草地与山丘之上，合并为单个 Mesh（烘焙方向明暗）"""
        rng = random.Random(20261030)
        verts, cols, tris = [], [], []
        shadows = []                        # 接地软阴影 (x, z, 半径)
        B = VOXEL_B
        n = int(WORLD * SC / B)
        top, kind = self._vox_top, self._vox_kind

        def pine(x, z, base, s):
            _emit_prim(verts, cols, tris, _prim_cyl(0.16 * s, 2.2 * s, 7),
                       (x, base, z), (0.32, 0.24, 0.15), cy_mid=1.1 * s)
            g = 0.42 + rng.uniform(-0.05, 0.06)
            for rr, hh, yy in ((1.5, 2.2, 1.3), (1.1, 1.8, 2.8), (0.7, 1.4, 4.0)):
                _emit_prim(verts, cols, tris, _prim_cone(rr * s, hh * s, 8),
                           (x, base + yy * s, z), (0.18, g, 0.20),
                           rot=rng.uniform(0, 6.28), cy_mid=hh * s * 0.4)

        def leafy(x, z, base, s, canopy=(0.25, 0.52, 0.22)):
            _emit_prim(verts, cols, tris, _prim_cyl(0.17 * s, 2.0 * s, 7),
                       (x, base, z), (0.33, 0.25, 0.16), cy_mid=1.0 * s)
            k = 1.0 + rng.uniform(-0.10, 0.10)
            rgb = (min(1.0, canopy[0] * k), min(1.0, canopy[1] * k),
                   min(1.0, canopy[2] * k))
            for ox, oy, oz, ss in ((0.0, 3.0, 0.0, 1.65), (0.95, 2.4, 0.35, 1.05),
                                   (-0.8, 2.5, -0.45, 0.9)):
                _emit_prim(verts, cols, tris, _prim_sphere(5, 7),
                           (x + ox * s, base + oy * s, z + oz * s), rgb,
                           scale=(ss * s, ss * 0.82 * s, ss * s))

        def tree(x, z, base, s):
            shadows.append((x, z, 1.05 * s, 5.4 * s))      # 半径 + 投影高度
            r = rng.random()
            if r < 0.14:                      # 樱花树：粉色冠层
                leafy(x, z, base, s, (0.96, 0.62, 0.74))
            elif r < 0.26:                    # 秋叶树：金橙冠层
                leafy(x, z, base, s, (0.95, 0.58, 0.22))
            elif r < 0.62:
                pine(x, z, base, s)
            else:
                leafy(x, z, base, s)

        def bush(x, z, base, s):
            shadows.append((x, z, 0.85 * s, 1.4 * s))
            g = 0.50 + rng.uniform(-0.05, 0.07)
            _emit_prim(verts, cols, tris, _prim_sphere(5, 7),
                       (x, base + 0.45 * s, z), (0.23, g, 0.26),
                       scale=(0.95 * s, 0.6 * s, 0.95 * s))

        def flower(x, z, base, s, fc=None):
            if fc is None:
                fc = rng.choice([(1.0, 0.55, 0.60), (1.0, 0.82, 0.40),
                                 (0.72, 0.60, 1.0), (1, 1, 1)])
            _emit_prim(verts, cols, tris, _prim_cyl(0.03 * s, 0.55 * s, 5),
                       (x, base, z), (0.26, 0.5, 0.26), cy_mid=0.28 * s)
            _emit_prim(verts, cols, tris, _prim_sphere(4, 6),
                       (x, base + 0.62 * s, z), fc, scale=0.17 * s)

        # 地面植被（避开出生点安全区与功能地块）
        placed = 0
        for _try in range(9800):
            if placed >= 800:
                break
            i = rng.randrange(n)
            j = rng.randrange(n)
            if kind[i][j] != "g":
                continue
            px, py = (i + 0.5) * B / SC, (j + 0.5) * B / SC
            if math.hypot(px - WORLD / 2, py - WORLD / 2) < SKY_SPAWN_CLEAR * 0.8:
                continue
            x, z = (i + 0.5) * B, (j + 0.5) * B
            k = rng.random()
            if k < 0.6:
                tree(x, z, top[i][j], rng.uniform(0.9, 1.4))
            elif k < 0.85:
                bush(x, z, top[i][j], rng.uniform(1.0, 1.5))
            else:
                flower(x, z, top[i][j], rng.uniform(1.2, 1.8))
            placed += 1
        # 花海：若干成片同色花田，形成鲜明的地貌节奏
        meadow_pals = (
            ((1.00, 0.52, 0.60), (1.00, 0.74, 0.80)),
            ((1.00, 0.84, 0.34), (1.00, 0.94, 0.62)),
            ((0.74, 0.60, 1.00), (0.87, 0.79, 1.00)),
            ((1.00, 0.62, 0.32), (1.00, 0.80, 0.52)),
        )
        for _m in range(8):
            cx_px = cy_px = None
            for _try in range(140):
                px = rng.randint(LAND_BAND + 120, WORLD - LAND_BAND - 120)
                py = rng.randint(LAND_BAND + 120, WORLD - LAND_BAND - 120)
                if math.hypot(px - WORLD / 2, py - WORLD / 2) < SKY_SPAWN_CLEAR * 0.9:
                    continue
                ci, cj = int(px * SC / B), int(py * SC / B)
                if 0 <= ci < n and 0 <= cj < n and kind[ci][cj] == "g":
                    cx_px, cy_px = px, py
                    break
            if cx_px is None:
                continue
            pal = meadow_pals[_m % len(meadow_pals)]
            pr = rng.uniform(65, 115)            # 花田半径（像素）
            for _k in range(rng.randint(40, 70)):
                a = rng.uniform(0, 2 * math.pi)
                rr = pr * math.sqrt(rng.random())
                px = cx_px + math.cos(a) * rr
                py = cy_px + math.sin(a) * rr
                i, j = int(px * SC / B), int(py * SC / B)
                if not (0 <= i < n and 0 <= j < n) or kind[i][j] != "g":
                    continue
                flower(_w(px), _w(py), top[i][j], rng.uniform(1.3, 2.1),
                       fc=rng.choice(pal))
        # 山丘植被（面积越大越多）
        for s in self.land[4:]:
            count = max(2, min(8, int(s["r"] ** 2 / 6500)))
            placed = 0
            for _try in range(count * 14):
                if placed >= count:
                    break
                a = rng.uniform(0, 2 * math.pi)
                rr = rng.uniform(0, s["r"] * 0.6)
                px = s["cx"] + math.cos(a) * rr
                py = s["cy"] + math.sin(a) * rr
                if not self._pt_in_poly(px, py, s["pts"]):
                    continue
                i, j = int(px * SC / B), int(py * SC / B)
                if not (0 <= i < n and 0 <= j < n) or kind[i][j] != "m":
                    continue
                if rng.random() < 0.7:
                    tree(_w(px), _w(py), top[i][j], rng.uniform(0.9, 1.3))
                else:
                    bush(_w(px), _w(py), top[i][j], rng.uniform(1.0, 1.4))
                placed += 1
        # 巨石点缀：山丘与草坡上的天然岩块
        rng_b = random.Random(5511)
        for _ in range(180):
            px = rng_b.randint(LAND_BAND + 40, WORLD - LAND_BAND - 40)
            py = rng_b.randint(LAND_BAND + 40, WORLD - LAND_BAND - 40)
            if math.hypot(px - WORLD / 2, py - WORLD / 2) < SKY_SPAWN_CLEAR * 0.8:
                continue
            top_b = self._column_top(px, py)
            if top_b is None:
                continue
            s = rng_b.uniform(0.45, 1.0)
            gray = rng_b.uniform(0.30, 0.40)
            shadows.append((_w(px), _w(py), 1.0 * s, 0.8 * s))
            _emit_prim(verts, cols, tris, _prim_sphere(5, 7),
                       (_w(px), top_b + s * 0.42, _w(py)),
                       (gray, gray * 0.97, gray * 0.92),
                       scale=(s, s * 0.58, s * 0.85),
                       rot=rng_b.uniform(0, 6.28))
        # 斜向投影 UV：竖直面也有 y 方向纹理变化，避免树干/树冠被拉成竖条纹
        uvs = [((vx + vy * 0.8) / VEG_UV, (vz - vy * 0.5) / VEG_UV)
               for vx, vy, vz in verts]
        e = Entity(model=_mesh(verts, tris, cols, uvs), eternal=True)
        e.texture = self.veg_tex            # 叶簇斑驳：树冠/灌木/树干纹理
        e.setTwoSided(True)
        _unlit(e)
        # 接地软阴影：按太阳方向拉长成斜投影（默认朝向太阳，落在背光侧）
        sun_hd = math.hypot(_LX, _LZ)
        shx, shz = _LX / sun_hd, _LZ / sun_hd      # 指向太阳的水平单位向量
        ax, az = -shx, -shz                        # 影子延伸方向
        pxv, pzv = -shz, shx                       # 影子横向
        sslope = _LY / sun_hd                      # 每单位水平距离的抬升
        sv, sc, st, su = [], [], [], []
        for item in shadows:
            sx, sz, rad = item[0], item[1], item[2]
            hh = item[3] if len(item) > 3 else 0.0
            cy = self._column_top(sx / SC, sz / SC)
            if cy is None:
                continue
            ln = hh / sslope if sslope > 1e-3 else 0.0     # 影子长度
            half_len = ln * 0.5 + rad
            cxm = sx + ax * (ln * 0.5)
            czm = sz + az * (ln * 0.5)
            b0 = len(sv)
            for dl, dp, u, v in ((-1, -1, 0, 0), (1, -1, 1, 0),
                                 (1, 1, 1, 1), (-1, 1, 0, 1)):
                wx = cxm + ax * (dl * half_len) + pxv * (dp * rad)
                wz = czm + az * (dl * half_len) + pzv * (dp * rad)
                gy = self._column_top(wx / SC, wz / SC)
                if gy is None:
                    gy = cy
                sv.append((wx, max(cy, gy) + 0.06, wz))
                sc.append((1.0, 1.0, 1.0, 0.82))     # 顶点 alpha 略降，影子更通透
                su.append((u, v))
            st += [(b0, b0 + 2, b0 + 1), (b0 + 1, b0 + 2, b0 + 3)]
        sh = Entity(model=_mesh(sv, st, sc, su), eternal=True)
        sh.texture = self.shadow_tex
        sh.setTransparency(TransparencyAttrib.MAlpha)
        sh.setDepthWrite(False)
        sh.setTwoSided(True)
        _unlit(sh)

    def _build_clouds(self):
        """真实风格积云：多个压扁球体组成柔软云团，高空漂移"""
        rng = random.Random(99)
        self.clouds = []
        for _ in range(20):
            verts, cols, tris = [], [], []
            puff = random.Random(rng.randint(0, 99999))
            for _i in range(puff.randint(4, 6)):
                _emit_prim(verts, cols, tris, _prim_sphere(5, 7),
                           (puff.uniform(-4.5, 4.5), puff.uniform(-0.3, 0.4),
                            puff.uniform(-2.6, 2.6)),
                           (0.98, 0.99, 1.0),
                           scale=(puff.uniform(2.2, 3.8), puff.uniform(0.9, 1.5),
                                  puff.uniform(1.6, 2.8)))
            e = Entity(model=_mesh(verts, tris, cols), color=color.rgba(1, 1, 1, 0.62))
            e.setTransparency(TransparencyAttrib.MAlpha)
            _unlit(e, depth_write=False)
            cl = {"e": e, "x": rng.uniform(0, WORLD), "z": rng.uniform(0, WORLD),
                  "y": rng.uniform(10.0, 17.0), "sp": rng.uniform(0.5, 1.3),
                  "ph": rng.uniform(0, 6.28)}
            self.clouds.append(cl)
        self.cloud_span = WORLD * SC + 60

    def _build_meteors(self):
        """空中陨石障碍：不规则岩石团，悬浮于地表之上的天空并缓慢自转（可碰撞）"""
        rng = random.Random(777)
        self._rock_parts = []           # 三种岩石形体的顶点数据；Mesh 需为每个实体单独创建
        self._rock_cr = []              # 每种形体的真实碰撞半径（主体顶点最大模长）
        for variant in range(3):        # 三种岩石形体，各自独立旋转
            v0, t0 = _prim_sphere(6, 9)
            r = random.Random(900 + variant)
            dv = [(x * (1.0 + r.uniform(-0.12, 0.12)),
                   y * (1.0 + r.uniform(-0.12, 0.12)),
                   z * (1.0 + r.uniform(-0.12, 0.12))) for x, y, z in v0]
            self._rock_cr.append(max(math.sqrt(x * x + y * y + z * z)
                                     for x, y, z in dv))
            base = ((0.34, 0.34, 0.36), (0.27, 0.27, 0.30),
                    (0.39, 0.37, 0.34))[variant]
            verts, cols, tris = [], [], []
            _emit_prim(verts, cols, tris, (dv, t0), (0, 0, 0), base)
            for i in range(3):          # 附着的小碎块，随主体一起旋转
                pv, pt = _prim_sphere(4, 6)
                ang = i * 2.094 + r.uniform(-0.3, 0.3)
                _emit_prim(verts, cols, tris, (pv, pt),
                           (math.cos(ang) * 0.72, r.uniform(-0.35, 0.35),
                            math.sin(ang) * 0.72), base,
                           scale=r.uniform(0.30, 0.48), rot=ang)
            uvs = [((x + y * 0.8) * 1.15 + 0.5, (z - y * 0.5) * 1.15 + 0.5)
                   for x, y, z in verts]
            self._rock_parts.append((verts, tris, cols, uvs))

        self.meteors = []
        w, band = WORLD, LAND_BAND
        for _ in range(METEOR_BELTS):            # 陨石带：成片聚集，远处即可辨认
            ccx = ccy = None
            for _try in range(40):
                ccx = rng.randint(band + 160, w - band - 160)
                ccy = rng.randint(band + 160, w - band - 160)
                if math.hypot(ccx - w / 2, ccy - w / 2) >= SKY_SPAWN_CLEAR * 1.15:
                    break                         # 出生点上方留出安全通道
            if ccx is None:
                continue
            spread = rng.randint(320, 620)        # 带内散布半径（像素）
            for _k in range(rng.randint(8, 12)):
                for _t2 in range(14):
                    x = ccx + rng.randint(-spread, spread)
                    y = ccy + rng.randint(-spread, spread)
                    if not (band + 50 < x < w - band - 50 and
                            band + 50 < y < w - band - 50):
                        continue
                    radius = rng.uniform(1.8, 5.0)
                    h = rng.uniform(4.0 + radius, 20.0 - radius)   # 固定在飞行空域内
                    if any((x - m["x"]) ** 2 + (y - m["y"]) ** 2 <
                           (radius + m["r"] + 34) ** 2 and abs(h - m["h"]) < 4.0
                           for m in self.meteors):
                        continue                  # 陨石之间保持可穿行的间距
                    variant = rng.randrange(3)
                    e = Entity(model=_mesh(*self._rock_parts[variant]),
                               position=(_w(x), h, _w(y)), scale=radius,
                               rotation=(rng.uniform(0, 360), rng.uniform(0, 360),
                                         rng.uniform(0, 360)))
                    e.texture = self.rock_tex       # 岩面颗粒与裂缝
                    _unlit(e)
                    self.meteors.append({"e": e, "x": x, "y": y, "h": h,
                                         "r": radius,
                                         "cr": radius * self._rock_cr[variant],
                                         "sp": (rng.uniform(-18, 18),
                                                rng.uniform(-14, 14),
                                                rng.uniform(-18, 18))})
                    break

    def _build_rainbow(self):
        """七色竖直半圆虹（加法混合，透明度呼吸）"""
        bands = [(1.0, 0.38, 0.38), (1.0, 0.66, 0.31), (1.0, 0.92, 0.43),
                 (0.47, 0.89, 0.51), (0.38, 0.69, 1.0), (0.51, 0.51, 0.98),
                 (0.75, 0.55, 0.98)]
        verts, tris, cols = [], [], []
        R0, w_band, seg = 34.0, 1.5, 40
        for bi, c in enumerate(bands):
            r_out = R0 - bi * w_band
            r_in = r_out - w_band * 0.92
            base = len(verts)
            for i in range(seg + 1):
                a = math.pi * i / seg
                verts.append((math.cos(a) * r_out, math.sin(a) * r_out, 0))
                cols.append((c[0], c[1], c[2], 0.16))
                verts.append((math.cos(a) * r_in, math.sin(a) * r_in, 0))
                cols.append((c[0], c[1], c[2], 0.16))
            for i in range(seg):
                b = base + i * 2
                tris += [(b, b + 2, b + 1), (b + 1, b + 2, b + 3)]
        e = Entity(model=_mesh(verts, tris, cols),
                   position=(_w(WORLD * 0.22), 4.0, _w(WORLD * 0.30)),
                   color=color.white)
        e.setTwoSided(True)             # 半环带正反都要可见
        e.setTransparency(TransparencyAttrib.MAlpha)
        _unlit(e, depth_write=False)
        self.rainbow = e

    def _build_sun_and_rays(self):
        from ursina import Texture
        self.glow_tex = Texture(_make_glow_texture())
        sx, sy, sz = _w(WORLD * 0.72), 40.0, _w(WORLD * 0.12)
        sun = Entity(model="sphere", position=(sx, sy, sz), scale=7,
                     color=color.rgb(1.0, 0.86, 0.55))
        _unlit(sun)
        sun.setFogOff(1)
        halo = Entity(model="quad", texture=self.glow_tex, position=(sx, sy, sz),
                      scale=26, color=color.rgba(1.0, 0.88, 0.6, 0.55))
        halo.setBillboardPointEye()
        _additive(halo)
        halo.setFogOff(1)
        self.sun_halo = halo
        # 体积光柱：自太阳发散的加法混合光锥
        self.rays = []
        for i, ry in enumerate((-0.35, 0.1, 0.5)):
            beam = _cone_entity(position=(sx - 10 + i * 7, sy - 16, sz + 6),
                                rotation=(18 + i * 8, 0, ry * 30),
                                scale=(7 - i, 30, 7 - i),
                                color=color.rgba(1.0, 0.94, 0.72, 0.10))
            _additive(beam)
            beam.setFogOff(1)
            self.rays.append(beam)

    def _build_stars(self):
        """闪烁星辰：3 组静态交叉星片合并 Mesh（加法混合，整组 alpha 呼吸）"""
        from ursina import Texture
        self.star_tex = Texture(_make_star_texture())
        rng = random.Random(7)
        self.star_groups = []
        for g_i in range(3):
            verts, tris, cols = [], [], []
            for _ in range(37):
                a = rng.uniform(0, 2 * math.pi)
                rr = rng.uniform(110, 430)
                y = rng.uniform(25, 240)
                cx = _w(WORLD / 2) + math.cos(a) * rr
                cz = _w(WORLD / 2) + math.sin(a) * rr
                s = rng.uniform(0.5, 1.2)
                for rot in (0.0, math.pi / 2):        # 交叉双片替代 billboard
                    ca, sa = math.cos(rot), math.sin(rot)
                    base = len(verts)
                    for dx, dy in ((-s, -s), (s, -s), (s, s), (-s, s)):
                        verts.append((cx + dx * ca, y + dy, cz + dx * sa))
                        cols.append((0.85, 0.9, 1.0))
                    tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
            e = Entity(model=_mesh(verts, tris, cols))
            e.setTwoSided(True)
            _additive(e)
            e.setFogOff(1)
            e.enabled = False                # 白天天空不显示星辰（纹理仍供天马拖尾使用）
            self.star_groups.append(e)

    def _build_landmarks(self):
        """地图点缀：湖泊水镜 / 石拱遗迹 / 发光水晶簇（各自合并为一个 Mesh）"""
        rng = random.Random(88421)
        B = VOXEL_B
        n = self._surf_n
        top, kind = self._vox_top, self._vox_kind
        margin = LAND_BAND + 100

        def _spot():
            for _ in range(160):
                px = rng.randint(margin, WORLD - margin)
                py = rng.randint(margin, WORLD - margin)
                if math.hypot(px - WORLD / 2, py - WORLD / 2) < SKY_SPAWN_CLEAR * 0.95:
                    continue
                i, j = int(px * SC / B), int(py * SC / B)
                if not (0 <= i < n and 0 <= j < n) or kind[i][j] != "g":
                    continue
                return px, py
            return None

        def _low_spot():
            """找到草地上的低洼盆地中心（湖泊只能落在低处）"""
            for _ in range(240):
                px = rng.randint(margin, WORLD - margin)
                py = rng.randint(margin, WORLD - margin)
                if math.hypot(px - WORLD / 2, py - WORLD / 2) < SKY_SPAWN_CLEAR * 0.95:
                    continue
                i, j = int(px * SC / B), int(py * SC / B)
                if not (0 <= i < n and 0 <= j < n) or kind[i][j] != "g":
                    continue
                if top[i][j] <= GROUND_BASE + 1.0:
                    return px, py
            return None

        # ---- 湖泊：低洼草地上的平滑水镜（高出水面的地形自然遮挡成岸线） ----
        wv, wc, wt = [], [], []
        water_y = GROUND_BASE + 1.05                    # 水位低于上一级台地，岸线由地形自然裁出
        seg = 36
        for _ in range(7):
            p = _low_spot()
            if p is None:
                continue
            cx, cy = p
            rw = _w(rng.uniform(90, 150))               # 半径（三维单位）
            wob = rng.uniform(0, 6.28)
            base = len(wv)
            wv.append((_w(cx), water_y, _w(cy)))
            wc.append((0.14, 0.38, 0.58, 0.94))
            for rr_k, col in ((0.72, (0.24, 0.52, 0.72, 0.86)),
                              (1.0, (0.56, 0.77, 0.90, 0.0))):   # 外圈透明：软边渐隐
                for k in range(seg):
                    a = 2 * math.pi * k / seg
                    edge = (1.0 + 0.14 * math.sin(a * 3 + wob) +
                            0.07 * math.sin(a * 5 - wob))
                    rr = rw * rr_k * edge
                    wv.append((_w(cx) + math.cos(a) * rr, water_y,
                               _w(cy) + math.sin(a) * rr))
                    wc.append(col)
            inner0, outer0 = base + 1, base + 1 + seg
            for k in range(seg):
                k2 = (k + 1) % seg
                wt += [(base, inner0 + k, inner0 + k2),
                       (inner0 + k, outer0 + k, outer0 + k2),
                       (inner0 + k, outer0 + k2, inner0 + k2)]
        if wv:
            e = Entity(model=_mesh(wv, wt, wc))
            e.setTwoSided(True)
            e.setTransparency(TransparencyAttrib.MAlpha)
            e.setDepthWrite(False)
            e.color = color.rgba(1.0, 1.0, 1.0, 0.90)
            _unlit(e)
            self.water = e

        # ---- 石拱遗迹：两根石柱 + 横梁，柱脚带苔痕 ----
        rv, rc, rt = [], [], []
        for _ in range(8):
            p = _spot()
            if p is None:
                continue
            cx, cy = p
            base = self._column_top(cx, cy)
            if base is None:
                continue
            ang = rng.uniform(0, 6.28)
            span = rng.uniform(4.5, 7.5)                # 半跨度（三维单位）
            hgt = rng.uniform(4.5, 7.5)
            stone = (0.63, 0.62, 0.57) if rng.random() < 0.6 else (0.55, 0.54, 0.50)
            moss = (0.36, 0.46, 0.31)
            ca, sa = math.cos(ang), math.sin(ang)
            for side in (-1.0, 1.0):
                ox, oz = ca * span * side, sa * span * side
                _emit_prim(rv, rc, rt, _prim_cyl(0.68, hgt, 8),
                           (_w(cx) + ox, base - 0.2, _w(cy) + oz), stone,
                           rot=ang, cy_mid=hgt * 0.5)
                _emit_prim(rv, rc, rt, _prim_cyl(0.86, 0.42, 8),
                           (_w(cx) + ox, base - 0.2, _w(cy) + oz), moss,
                           rot=ang, cy_mid=0.2)
            _emit_prim(rv, rc, rt, _prim_box(span * 2.0 + 1.7, 0.95, 1.25),
                       (_w(cx), base - 0.2 + hgt, _w(cy)), stone, rot=ang,
                       cy_mid=0.48)
        if rv:
            e = Entity(model=_mesh(rv, rt, rc))
            e.setTwoSided(True)
            _unlit(e)

        # ---- 发光水晶簇：八面体晶柱 + 加法混合光晕（光晕在主循环里呼吸） ----
        cv, cc, ct = [], [], []
        self.crystal_glows = []
        palette = ((0.40, 0.94, 1.00), (0.66, 0.58, 1.00),
                   (1.00, 0.56, 0.86), (0.52, 1.00, 0.74))
        for _ in range(12):
            p = _spot()
            if p is None:
                continue
            cx, cy = p
            base = self._column_top(cx, cy)
            if base is None:
                continue
            col = rng.choice(palette)
            for _k in range(rng.randint(3, 6)):
                ox = rng.uniform(-2.4, 2.4)
                oz = rng.uniform(-2.4, 2.4)
                hh = rng.uniform(1.6, 4.4)
                rr = rng.uniform(0.30, 0.62)
                _emit_prim(cv, cc, ct, _prim_octa(),
                           (_w(cx) + ox, base + hh * 0.30, _w(cy) + oz), col,
                           scale=(rr, hh * 0.5, rr), rot=rng.uniform(0, 6.28))
            g = Entity(model="quad", texture=self.glow_tex,
                       position=(_w(cx), base + 1.3, _w(cy)), scale=5.0,
                       color=color.rgb(col[0] * 0.7, col[1] * 0.7, col[2] * 0.7))
            g.setBillboardPointEye()
            _additive(g)
            g.setFogOff(1)
            self.crystal_glows.append({"e": g, "col": col,
                                       "ph": rng.uniform(0, 6.28)})
        if cv:
            e = Entity(model=_mesh(cv, ct, cc))
            e.setTwoSided(True)
            _unlit(e)

    def _build_pegasus(self):
        """天马行空：白身蓝鬃独角飞马，双翅扇动 + 星光拖尾"""
        body_col = color.rgb(0.97, 0.97, 1.0)
        mane_col = color.rgb(0.55, 0.78, 1.0)
        root = Entity(position=(0, 12, 0))
        Entity(parent=root, model="sphere", scale=(2.6, 1.5, 1.5), color=body_col)
        Entity(parent=root, model="sphere", position=(1.5, 0.55, 0), scale=0.85,
               color=body_col)                                     # 头
        Entity(parent=root, model="sphere", position=(2.05, 0.4, 0), scale=0.42,
               color=color.rgb(0.85, 0.86, 0.95))                   # 吻部
        horn = _cone_entity(parent=root, position=(1.75, 1.15, 0),
                            scale=(0.22, 0.9, 0.22), color=color.rgb(1.0, 0.9, 0.55))
        Entity(parent=root, model="sphere", position=(1.75, 0.85, 0.32), scale=0.16,
               color=color.rgb(0.1, 0.1, 0.2))                      # 眼
        for i in range(3):                                          # 鬃毛
            Entity(parent=root, model="sphere", position=(1.1 - i * 0.5, 0.9 + i * 0.12, 0),
                   scale=(0.5, 0.42, 0.3), color=mane_col)
        Entity(parent=root, model="sphere", position=(-1.7, 0.3, 0), scale=(1.0, 0.3, 0.3),
               color=mane_col)                                      # 尾
        self.wing_l = Entity(parent=root, model="cube", position=(0, 0.55, 0.95),
                             scale=(1.6, 0.12, 1.7), color=body_col)
        self.wing_r = Entity(parent=root, model="cube", position=(0, 0.55, -0.95),
                             scale=(1.6, 0.12, 1.7), color=body_col)
        for e in root.children:
            _unlit(e)
        self.pegasus = root
        # 星光拖尾池
        self.trail_pool = []
        for _ in range(22):
            s = Entity(model="quad", texture=self.star_tex, scale=0.9,
                       color=color.rgba(0.7, 0.85, 1.0, 0.8), enabled=False)
            s.setBillboardPointEye()
            _additive(s)
            self.trail_pool.append({"e": s, "t": -1})

    # ---------- 果实 / 蛇 ----------

    def _build_fruits(self):
        self.fruit_pool = []
        for _ in range(FRUIT_MAX):
            core = Entity(model="sphere", scale=1.15, visible=False)
            glow = Entity(parent=core, model="quad", texture=self.glow_tex,
                          scale=2.8)          # 子节点随核心移动（父 scale 1.15 已折算）
            glow.setBillboardPointEye()
            _additive(glow)
            self.fruit_pool.append({"core": core, "glow": glow, "x": 0, "y": 0, "h": 0.0,
                                    "gold": False, "value": 1, "ph": 0.0, "active": False})

    def _build_rings(self):
        """天空光环门：竖立的金色圆环，穿过开口奖励经验并补充冲刺能量"""
        self.rings = []
        for _ in range(RING_COUNT):
            # Mesh 是 NodePath：每个实体必须独立建网格，共享会被 reparent 抢占
            v, t = _prim_torus(RING_RADIUS, 0.20, 28, 8)
            body = Entity(model=_mesh(v, t, [(1.0, 0.82, 0.32)] * len(v)))
            _unlit(body)
            v2, t2 = _prim_torus(RING_RADIUS, 0.44, 28, 8)
            glow = Entity(parent=body,
                          model=_mesh(v2, t2, [(1.0, 1.0, 1.0)] * len(v2)))
            _additive(glow)                                 # 子节点继承位置/朝向
            self.rings.append({"body": body, "glow": glow, "x": 0.0, "y": 0.0,
                               "h": 0.0, "nx": 1.0, "nz": 0.0, "active": False,
                               "respawn": 0.0, "last_d": None,
                               "ph": self.rng.uniform(0, 6.28)})

    def _place_ring(self, ring, t):
        p = self._ring_point()
        if p is None:
            ring["active"] = False
            ring["body"].visible = False
            ring["respawn"] = t + 2.0
            return
        x, y, h = p
        yaw = self.rng.uniform(0, math.pi * 2)
        ring.update(x=x, y=y, h=h, nx=math.sin(yaw), nz=math.cos(yaw),
                    active=True, respawn=0.0, ph=self.rng.uniform(0, 6.28))
        body = ring["body"]
        body.setPos(_w(x), h, _w(y))
        # 圆环平面法线为局部 +Z；look_at 让局部 +Z 指向水平法线方向
        body.look_at(Vec3(_w(x) + ring["nx"], h, _w(y) + ring["nz"]))
        body.visible = True
        ring["last_d"] = self._ring_sd(ring, self.x, self.y, self.h)

    @staticmethod
    def _ring_sd(ring, px, py, ph):
        """玩家相对光环平面的带符号距离"""
        return ((px - ring["x"]) * SC) * ring["nx"] + (ph - ring["h"]) * ring["nz"]

    def _collect_ring(self, ring, t):
        ring["active"] = False
        ring["body"].visible = False
        ring["respawn"] = t + RING_RESPAWN
        ring["last_d"] = None
        self.rings_passed += 1
        self.energy = min(100.0, self.energy + RING_ENERGY)
        self._gain_xp(RING_XP, t)
        self._play(self.snd_eat)

    def _build_powerups(self):
        """强化道具水晶：护盾 / 磁铁 / 双倍经验 / 冲刺能量 / 极速 / 幸运 / 成长"""
        self.powerup_pool = []
        for _ in range(POWERUP_MAX):
            v, t = _prim_octa()
            core = Entity(model=Mesh(vertices=v, triangles=t, mode="triangle",
                                     static=True), scale=1.05, visible=False)
            core.setTwoSided(True)
            _unlit(core)
            glow = Entity(model="quad", texture=self.glow_tex, scale=2.0, visible=False)
            glow.setBillboardPointEye()
            _additive(glow)
            self.powerup_pool.append({"core": core, "glow": glow,
                                      "x": 0.0, "y": 0.0, "h": 0.0,
                                      "kind": "shield", "active": False,
                                      "life": 0.0, "ph": 0.0})

    def _spawn_powerup(self):
        slot = next((p for p in self.powerup_pool if not p["active"]), None)
        if slot is None:
            return
        pt = self._powerup_point()
        if pt is None:
            return
        r = self.rng.random()
        if r < 0.16:
            kind = "shield"
        elif r < 0.32:
            kind = "magnet"
        elif r < 0.46:
            kind = "double"
        elif r < 0.60:
            kind = "energy"
        elif r < 0.74:
            kind = "turbo"
        elif r < 0.88:
            kind = "lucky"
        else:
            kind = "grow"
        col = POWERUP_COLORS[kind]
        slot.update(x=pt[0], y=pt[1], h=pt[2], kind=kind, active=True,
                    life=POWERUP_LIFE, ph=self.rng.uniform(0, 6.28))
        slot["core"].color = color.rgb(*col)
        slot["core"].rotation = (0, 0, 0)
        slot["glow"].color = color.rgba(col[0] * 0.55, col[1] * 0.55, col[2] * 0.55, 1.0)
        slot["core"].visible = True
        slot["glow"].visible = True

    def _apply_powerup(self, kind, t):
        if kind == "energy":
            self.energy = 100.0
        elif kind == "grow":
            self._gain_xp(GROW_XP, t)       # 成长道具：立即获得经验
        else:
            self.buffs[kind] = BUFF_DUR[kind]
        self.powerups_taken += 1
        self._play(self.snd_eat)
        names = {"shield": "护盾展开", "magnet": "磁力吸附",
                 "double": "双倍经验", "energy": "能量充满",
                 "turbo": "极速冲刺", "lucky": "幸运星",
                 "grow": f"成长 +{GROW_XP} 经验"}
        self.item_text.text = names.get(kind, "")
        c = POWERUP_COLORS[kind]
        self.item_toast_col = (c[0], c[1], c[2])
        self.item_text.color = color.rgba(c[0], c[1], c[2], 1.0)
        tex = getattr(self, "icon_tex", {}).get(kind)
        if tex is not None:
            self.item_icon.texture = tex
            self.item_icon.color = color.rgba(1, 1, 1, 1)
            self.item_icon.visible = True
        else:
            self.item_icon.visible = False
        self.item_toast_t = 1.5

    def _build_snake(self):
        self.max_seg = max(8, int(SKY_MAX_LENGTH / SKY_TRAIL_SPACING))
        self.seg_ents = []
        for _ in range(self.max_seg):
            e = Entity(model="sphere", scale=1.0, visible=False)
            self.seg_ents.append(e)
        # 蛇头：球体 + 双眼
        self.head = Entity(model="sphere", scale=SKY_HEAD_RADIUS * SC * 2.2)
        self.eye_l = Entity(parent=self.head, model="sphere", scale=0.2,
                            position=(0.26, 0.2, 0.3), color=color.white)
        self.eye_r = Entity(parent=self.head, model="sphere", scale=0.2,
                            position=(0.26, 0.2, -0.3), color=color.white)
        self.pup_l = Entity(parent=self.head, model="sphere", scale=0.1,
                            position=(0.4, 0.2, 0.32), color=color.rgb(0.08, 0.08, 0.16))
        self.pup_r = Entity(parent=self.head, model="sphere", scale=0.1,
                            position=(0.4, 0.2, -0.32), color=color.rgb(0.08, 0.08, 0.16))
        # 护盾泡（护盾生效时显示）
        self.shield_bubble = Entity(parent=self.head, model="sphere", scale=1.25,
                                    color=color.rgba(0.35, 0.9, 1.0, 0.16), visible=False)
        self.shield_bubble.setTransparency(TransparencyAttrib.MAlpha)
        _unlit(self.shield_bubble, depth_write=False)

    # ---------- 软碰撞墙（和平精英风格） ----------

    def _build_soft_walls(self):
        """矩形隐形力场：默认全透明，仅蛇靠近时显示轮廓并施加平滑外推"""
        t = 6
        walls = []

        def add(rect, push_dir, phase):
            walls.append({"rect": rect, "dir": push_dir,
                          "buffer": SKY_SOFT_WALL_BUFFER,
                          "push": SKY_SOFT_WALL_PUSH,
                          "phase": phase,
                          "e": self._make_soft_wall_entity(rect)})

        inset = SKY_SOFT_WALL_INSET
        # 四周边界：单向向场内推，避免把蛇推到场外
        add((0, inset - t, WORLD, inset + t), (0.0, 1.0), 0.0)
        add((0, WORLD - inset - t, WORLD, WORLD - inset + t), (0.0, -1.0), 1.7)
        add((inset - t, 0, inset + t, WORLD), (1.0, 0.0), 3.1)
        add((WORLD - inset - t, 0, WORLD - inset + t, WORLD), (-1.0, 0.0), 4.6)
        # 自定义软墙：默认沿最近边缘向外推
        for i, (x, y, w, h) in enumerate(SKY_SOFT_WALL_CUSTOM):
            add((float(x), float(y), float(x + w), float(y + h)), None, i * 1.3)
        self.soft_walls = walls
        # 撞击涟漪：蛇贴墙时在接触点生成金色扩散环
        self.soft_ripples = []
        rip_tex = Texture(_make_soft_ripple_texture())
        for _ in range(SOFT_RIPPLE_POOL):
            e = Entity(model="quad", texture=rip_tex,
                       color=color.rgba(1.0, 0.82, 0.42, 0.0))
            e.setTwoSided(True)
            e.setTransparency(TransparencyAttrib.MAlpha)
            _unlit(e, depth_write=False)
            e.setFogOff(1)
            e.visible = False
            self.soft_ripples.append({"e": e, "life": 0.0, "dur": SOFT_RIPPLE_LIFE})
        self._soft_rip_i = 0
        self._soft_rip_lt = None

    def _spawn_soft_ripple(self, cx, cy, nx, ny):
        """在软墙接触点生成一圈金色涟漪（贴墙面、朝圈内）"""
        if not self.soft_ripples:
            return
        r = self.soft_ripples[self._soft_rip_i]
        self._soft_rip_i = (self._soft_rip_i + 1) % len(self.soft_ripples)
        e = r["e"]
        e.position = (_w(cx), max(0.6, min(self.h, 9.5)), _w(cy))
        e.rotation_y = math.degrees(math.atan2(nx, ny))   # 面片法线对齐墙面法线
        e.scale = 1.0
        r["dur"] = r["life"] = SOFT_RIPPLE_LIFE
        e.color = color.rgba(1.0, 1.0, 1.0, 0.92)
        e.visible = True

    def _make_soft_wall_entity(self, rect):
        """矩形四边做成竖直能量幕：贴地最实、往高空渐隐，地面再叠一条光带。

        高度远高于飞行上限，任何俯仰视角都能看到完整一面墙（原来只做到 10，
        高飞时只露出一小段）。
        """
        x0, y0, x1, y1 = rect
        top = 46.0
        layers = 10
        verts, tris, cols = [], [], []
        edges = ((x0, y0, x1, y0), (x1, y0, x1, y1),
                 (x1, y1, x0, y1), (x0, y1, x0, y0))
        for ax, az, bx, bz in edges:
            dx, dz = bx - ax, bz - az
            length = math.hypot(dx, dz)
            if length < 1e-6:
                continue
            nx, nz = -dz / length, dx / length
            # 竖直能量幕：分段绘制，越高越透明（顶点 alpha 渐变）
            for li in range(layers):
                h0 = top * li / layers
                h1 = top * (li + 1) / layers
                a0 = 0.92 * (1.0 - li / float(layers)) ** 1.45 + 0.07
                a1 = 0.92 * (1.0 - (li + 1) / float(layers)) ** 1.45 + 0.07
                base = len(verts)
                verts.extend([
                    (_w(ax), h0, _w(az)),
                    (_w(bx), h0, _w(bz)),
                    (_w(bx), h1, _w(bz)),
                    (_w(ax), h1, _w(az)),
                ])
                tris.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
                cols.extend([(0.20, 0.72, 1.0, a0)] * 2 + [(0.20, 0.72, 1.0, a1)] * 2)
            # 墙顶亮边：一条细白青高光，明确"这堵墙到这里为止"
            base = len(verts)
            verts.extend([
                (_w(ax), top - 1.8, _w(az)),
                (_w(bx), top - 1.8, _w(bz)),
                (_w(bx), top, _w(bz)),
                (_w(ax), top, _w(az)),
            ])
            tris.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
            cols.extend([(0.80, 0.96, 1.0, 0.42)] * 2 + [(0.86, 0.98, 1.0, 0.58)] * 2)
            # 地面光带：沿墙脚的水平细条
            half = 6.0
            base = len(verts)
            verts.extend([
                (_w(ax - nx * half), 0.3, _w(az - nz * half)),
                (_w(bx - nx * half), 0.3, _w(bz - nz * half)),
                (_w(bx + nx * half), 0.3, _w(bz + nz * half)),
                (_w(ax + nx * half), 0.3, _w(az + nz * half)),
            ])
            tris.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
            cols.extend([(0.55, 0.90, 1.0, 0.95)] * 4)
        e = Entity(model=_mesh(verts, tris, cols))
        e.setTwoSided(True)
        e.setTransparency(TransparencyAttrib.MAlpha)
        _unlit(e, depth_write=False)
        e.setFogOff(1)
        e.color = color.rgba(1.0, 1.0, 1.0, 0.0)
        e.enabled = False
        return e

    @staticmethod
    def _soft_wall_hit(wall, px, py):
        """返回 (带符号距离, 外推法线)：矩形外为正，矩形内为负"""
        x0, y0, x1, y1 = wall["rect"]
        cx = min(max(px, x0), x1)
        cy = min(max(py, y0), y1)
        dx, dy = px - cx, py - cy
        if dx != 0.0 or dy != 0.0:
            d = math.hypot(dx, dy)
            return d, dx / d, dy / d
        dl, dr = px - x0, x1 - px
        db, dt = py - y0, y1 - py
        m = min(dl, dr, db, dt)
        if m == dl:
            return -m, -1.0, 0.0
        if m == dr:
            return -m, 1.0, 0.0
        if m == db:
            return -m, 0.0, -1.0
        return -m, 0.0, 1.0

    def _apply_soft_walls(self, dt):
        """只推动蛇头：越靠近墙推力越大，平滑回推，不改朝向、不锁移动"""
        if self.over:
            return
        for wall in self.soft_walls:
            d, nx, ny = self._soft_wall_hit(wall, self.x, self.y)
            if d >= wall["buffer"]:
                continue
            k = 1.0 if d <= 0.0 else 1.0 - d / wall["buffer"]
            k = k * k * (3.0 - 2.0 * k)          # smoothstep：贴墙才明显发力
            fnx, fny = wall["dir"] if wall["dir"] else (nx, ny)
            # 贴墙接触：按冷却在接触点生成金色涟漪
            if k > 0.30 and self._anim_t - wall.get("rip_t", -9.0) > SOFT_RIPPLE_CD:
                wall["rip_t"] = self._anim_t
                x0, y0, x1, y1 = wall["rect"]
                self._spawn_soft_ripple(min(max(self.x, x0), x1),
                                        min(max(self.y, y0), y1), fnx, fny)
            push = wall["push"] * k * dt
            if d < 0.0:                          # 已进入墙内：额外柔推，避免卡住
                push += min(-d, 60.0) * 1.6 * k * dt
            self.x += fnx * push
            self.y += fny * push

    def _update_soft_walls_visual(self, t):
        """能量幕可见性：远处保留淡轮廓，靠近迅速增强（呼吸闪烁）

        不再只在 190 像素缓冲内出现——那样远看几乎没有墙，高飞时只看到贴地一小段。
        """
        far_vis = 900.0
        for wall in self.soft_walls:
            d, _nx, _ny = self._soft_wall_hit(wall, self.x, self.y)
            if d >= far_vis:
                wall["e"].enabled = False
                continue
            k = 1.0 if d <= 0.0 else max(0.0, 1.0 - d / far_vis)
            breath = 0.72 + 0.28 * math.sin(t * 2.8 + wall["phase"])
            alpha = (0.14 + 0.76 * (k ** 0.85)) * breath
            if alpha < 0.02:
                wall["e"].enabled = False
                continue
            wall["e"].enabled = True
            # 刚撞击时轮廓闪一下金色，随后回到青蓝
            hot = max(0.0, 1.0 - (t - wall.get("rip_t", -9.0)) / 0.30)
            wall["e"].color = color.rgba(0.44 + 0.56 * hot,
                                         0.88 - 0.06 * hot,
                                         1.0 - 0.52 * hot, alpha)
        # 金色涟漪：向外扩散并淡出
        fdt = min(0.1, max(0.0, t - self._soft_rip_lt)) \
            if self._soft_rip_lt is not None else 0.0
        self._soft_rip_lt = t
        for r in getattr(self, "soft_ripples", ()):
            if r["life"] <= 0.0:
                continue
            r["life"] -= fdt
            e = r["e"]
            if r["life"] <= 0.0:
                e.visible = False
                continue
            k = 1.0 - r["life"] / max(1e-4, r["dur"])     # 0 → 1 扩散进度
            s = 1.0 + SOFT_RIPPLE_SIZE * (k ** 0.7)
            e.scale = (s, s, 1.0)
            # 越往外越浅：中心最实，边缘透明
            e.color = color.rgba(1.0, 1.0, 1.0, 0.96 * (1.0 - k))

    # ---------- 状态 ----------

    def reset(self):
        c = WORLD // 2
        self.x = float(c)
        self.y = float(c)
        self.px = self.x
        self.py = self.y
        self.angle = 0.0
        self.target_angle = 0.0
        self.xp = 0
        self.level = 1
        self.level_flash = 0.0
        self.target_len = SKY_INITIAL_LENGTH
        self.best = load_sky_best()
        self.over = False
        self.death_reason = ""
        self.paused = False
        self.pause_menu = False
        self.pause_badge.enabled = False
        self._set_hud_visible(True)
        self.h = 2.0                      # 飞行高度（三维单位）
        self.ph = self.h                  # 上一帧飞行高度（光环穿越检测用）
        self.pitch = 0.0                  # 当前俯仰角
        self.target_pitch = 0.0           # 鼠标给出的目标俯仰角
        self.energy = 100.0               # 冲刺能量
        self.dashing = False
        self.combo = 0                    # 当前连击数（0/1 表示未连击）
        self.combo_t = 0.0                # 上次吃果实的时刻
        self.rings_passed = 0             # 本局穿过的光环数
        self.powerups_taken = 0           # 本局拾取的强化道具数
        self.buffs = {"shield": 0.0, "magnet": 0.0, "double": 0.0,
                      "turbo": 0.0, "lucky": 0.0}
        self.next_powerup = 3.0
        self._combo_shown = -1            # 连击文本缓存（避免每帧重建纹理）
        self._set_cursor_locked(True)
        self._vis_n = -1                # 强制下一帧重算蛇段颜色/尺寸
        self.skin = SKINS[SETTINGS["skin"] % len(SKINS)]
        c1 = self.skin["c1"]
        self.head.color = color.rgb(self.skin["head"][0] / 255,
                                    self.skin["head"][1] / 255, self.skin["head"][2] / 255)

        self.trail = deque()
        n0 = max(8, int(SKY_INITIAL_LENGTH / SKY_TRAIL_SPACING))
        for i in range(n0):
            self.trail.append((self.x - SKY_TRAIL_SPACING * i, self.y, self.h))

        self.fruits = []
        for f in self.fruit_pool:
            f["active"] = False
            f["core"].visible = False      # glow 是子节点，随父隐藏
        for _ in range(26):
            self._spawn_fruit()
        self.next_fruit = 0.7
        # 光环全量重置；强化道具清场（随后按间隔刷新）
        for ring in self.rings:
            ring["last_d"] = None
            self._place_ring(ring, 0.0)
        for p in self.powerup_pool:
            p["active"] = False
            p["life"] = 0.0
            p["core"].visible = False
            p["glow"].visible = False
        self._hide_menus()
        self._update_hud_text()
        self._update_energy_hud(0.0)
        self._update_buff_hud()
        if self.bgm and not self.test_mode:
            self._set_bgm_paused(False)     # 新开局也从断点/正常倍速起播，别停在 0 倍速

    def _set_cursor_locked(self, locked):
        """游戏中隐藏光标，退出时恢复。

        不再使用 ursina 的 mouse.locked：它在锁定态下把"指针绝对位置"当成
        每帧位移，并用带魔数(1.124)的 move_pointer 回中，Windows 125% DPI 下
        会残留偏移，表现为不做任何操作镜头也在缓慢乱转。
        转向改为 _RawMouse 的原生相对位移（GetCursorPos 差分 + 贴边 SetCursorPos 回中），
        这里只负责系统光标的显隐。
        """
        try:
            mouse.locked = False
            mouse.visible = not locked       # 显式隐藏系统光标，避免仍显示箭头
        except Exception:
            pass
        try:                                 # 更直接的方式：Panda 层面隐藏光标
            from panda3d.core import WindowProperties
            wp = WindowProperties()
            wp.setCursorHidden(bool(locked))
            application.base.win.requestProperties(wp)
        except Exception:
            pass
        rm = getattr(self, "raw_mouse", None)
        if rm is not None:
            if locked:
                rm.lock()                # 光标钉死在中心 + 关指针加速
            else:
                rm.unlock()              # 解除裁剪 + 还原系统设置

    def _spawn_fruit(self):
        slot = next((f for f in self.fruit_pool if not f["active"]), None)
        if slot is None:
            return
        p = self._random_sky_point()
        if p is None:
            return
        gold_chance = (LUCKY_GOLD_CHANCE if self.buffs.get("lucky", 0.0) > 0.0
                       else 0.15)
        gold = self.rng.random() < gold_chance
        slot.update(x=p[0], y=p[1], h=p[2], gold=gold,
                    value=SKY_GOLD_XP if gold else 1,
                    ph=self.rng.uniform(0, 6.28), active=True)
        col = (1.0, 0.82, 0.25) if gold else (0.35, 0.95, 0.5)
        slot["core"].color = color.rgb(*col)
        slot["glow"].color = color.rgba(col[0], col[1], col[2], 0.55)
        slot["core"].visible = True

    def _record_best(self):
        if self.xp > self.best:
            self.best = self.xp
            save_sky_best(self.best)

    def die(self, reason):
        if self.over or self.invincible:
            return
        self.over = True
        self.death_reason = reason
        if self.bgm:
            try:
                self.bgm.stop(destroy=False)   # 只停播、不销毁：重开一局还能再用同一个对象
            except Exception:
                pass
        self._play(self.snd_death)
        for k in self.buffs:            # 结算时清空增益状态
            self.buffs[k] = 0.0
        self.shield_bubble.visible = False
        self._update_buff_hud()
        self._record_best()
        self._show_gameover()

    # ---------- 主循环 ----------

    def update(self):
        dt = min(0.033, ursina_time.dt)
        self._anim_t += dt
        t = self._anim_t

        _pt = _time.perf_counter if self.test_mode else None
        if _pt:
            self._prof_n += 1
            _s = _pt()
        self._animate_world(t, dt) if not _SKIP_ANIM else None
        if _pt:
            _a, _s = _s, _pt()
            self._sec["anim"] += _s - _a
        if self.over or self.paused or self.pause_menu:
            self._update_camera(dt)
            return

        # 转向：偏航走最短弧平滑逼近；俯仰限速并钳制（全向 4π 飞行）
        diff = (self.target_angle - self.angle + math.pi) % (math.pi * 2) - math.pi
        max_turn = SKY_TURN_RATE * dt
        self._turn(diff, max_turn)
        pd = max(-max_turn, min(max_turn, self.target_pitch - self.pitch))
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.pitch + pd))

        # 冲刺：按住 Shift 消耗能量加速，松开自动回充
        want_dash = bool(held_keys["shift"] or held_keys["left shift"])
        if self.dashing:
            if not want_dash or self.energy <= 0.0:
                self.dashing = False
        elif want_dash and self.energy > DASH_MIN:
            self.dashing = True
        if self.dashing:
            if self.buffs["turbo"] > 0.0:       # 极速期间冲刺不耗能
                self.energy = min(100.0, self.energy + DASH_REGEN * 0.5 * dt)
            else:
                self.energy = max(0.0, self.energy - DASH_DRAIN * dt)
        else:
            self.energy = min(100.0, self.energy + DASH_REGEN * dt)
        # 冲刺速度感：视野轻微扩张，松开后平滑回落（极速再额外扩张）
        fov_k = (DASH_FOV if self.dashing else 1.0)
        if self.buffs["turbo"] > 0.0:
            fov_k *= 1.06
        tf = self._fov_base * fov_k
        if abs(camera.fov - tf) > 0.01:
            camera.fov += (tf - camera.fov) * min(1.0, dt * 8.0)

        # 前进（三维全向）+ 三维轨迹
        self.px, self.py, self.ph = self.x, self.y, self.h
        cp = math.cos(self.pitch)
        sp = SKY_SPEED * (DASH_SPEED if self.dashing else 1.0) \
            * getattr(self, "speed_scale", 1.0) * self._item_speed_mult() * dt
        self.x += math.cos(self.angle) * cp * sp
        self.y += math.sin(self.angle) * cp * sp
        self.h += math.sin(self.pitch) * sp * SC
        if self.h > MAX_ALT:                # 高度软顶
            self.h = MAX_ALT
            self.pitch = min(self.pitch, 0.0)
            self.target_pitch = min(self.target_pitch, 0.0)
        self._apply_soft_walls(dt)          # 软墙：进入缓冲后平滑外推
        added = 0                   # 本帧新增的轨迹点数（碰撞回退时同步裁剪）
        moved_px = 0.0              # 本帧移动距离（像素尺度，含高度差）
        if self.trail:
            tx, ty, th = self.trail[0]
            dpx, dpy = self.x - tx, self.y - ty
            dh = (self.h - th) / SC          # 高度差换算到像素尺度统一度量
            moved_px = math.sqrt(dpx * dpx + dpy * dpy + dh * dh)
            if moved_px > 1e-9:
                for k in range(1, int(moved_px // SKY_TRAIL_SPACING) + 1):
                    f = (k * SKY_TRAIL_SPACING) / moved_px
                    self.trail.appendleft((tx + dpx * f, ty + dpy * f,
                                           th + (self.h - th) * f))
                    added += 1
        else:
            self.trail.appendleft((self.x, self.y, self.h))
        max_pts = max(8, int(self.target_len / SKY_TRAIL_SPACING))
        while len(self.trail) > max_pts:
            self.trail.pop()

        # 强化道具计时（先结算：护盾到期当帧即失效）
        self._update_buffs(dt)

        # 碰撞：世界边界 / 地形（球形蛇头 + 本帧扫掠，防止高速穿模）
        hr = SKY_HEAD_RADIUS
        dxm, dym, dhm = self.x - self.px, self.y - self.py, self.h - self.ph
        if self.buffs["shield"] > 0.0:
            # 护盾生效：越界自动掉头，前方山体把蛇头抬到地表之上，陨石可直接穿过
            if self.x < hr or self.y < hr or self.x > WORLD - hr or self.y > WORLD - hr:
                self.target_angle = math.atan2(WORLD / 2 - self.y, WORLD / 2 - self.x)
                self.angle = self.target_angle
                self.x = min(max(self.x, hr + 1), WORLD - hr - 1)
                self.y = min(max(self.y, hr + 1), WORLD - hr - 1)
            hit_f, _safe, lift = self._terrain_sweep(
                self.px, self.py, self.ph, self.x, self.y, self.h)
            if hit_f is not None:
                self.h = max(self.h, lift)
                self.pitch = max(self.pitch, 0.5)
                self.target_pitch = max(self.target_pitch, 0.5)
            top = self._column_top(self.x, self.y)
            if top is not None and self.h - HEAD_COL_R < top:
                self.h = top + HEAD_COL_R + 0.25
                self.pitch = max(self.pitch, 0.4)
                self.target_pitch = max(self.target_pitch, 0.4)
        elif self.x < hr or self.y < hr or self.x > WORLD - hr or self.y > WORLD - hr:
            self.die("bounds")
        else:
            hit_f, safe_f, lift = self._terrain_sweep(
                self.px, self.py, self.ph, self.x, self.y, self.h)
            if hit_f is not None:
                # 回退到接触前的位置并贴住地表：死亡瞬间蛇头不插进山体
                self.x = self.px + dxm * safe_f
                self.y = self.py + dym * safe_f
                self.h = self.ph + dhm * safe_f
                pen = self._terrain_penetration(self.x, self.y, self.h)
                if pen > 0.0:
                    self.h += pen + 0.01
                rm = added - int(moved_px * safe_f / SKY_TRAIL_SPACING)
                for _ in range(max(0, min(added, rm))):
                    if not self.trail:
                        break
                    self.trail.popleft()
                self.die("voxel")
            elif not self.over and self._near_meteor(self.x, self.y, self.h, HEAD_COL_R):
                self.die("meteor")
        if _pt:
            _a, _s = _s, _pt()
            self._sec["col"] += _s - _a
        if self.over:                   # 死亡帧只更新表现层，跳过后续玩法结算
            if not _SKIP_VIS:
                self._update_snake_visual(t)
            self._update_minimap_dot()
            self._update_energy_hud(t)
            self._update_camera(dt)
            return

        # 光环穿越 / 道具刷新与拾取
        self._update_rings(t)
        self._update_powerups(dt, t)

        # 果实刷新 / 拾取
        if _pt:
            _a, _s = _s, _pt()
            self._sec["move"] += _s - _a
        self.next_fruit -= dt
        if self.next_fruit <= 0:
            self.next_fruit = SKY_FRUIT_INTERVAL / 1000.0
            self._spawn_fruit()
        if self.buffs["magnet"] > 0.0:
            self._pull_fruits(dt)
        for f in self.fruit_pool:
            if not f["active"]:
                continue
            dx = self.x - f["x"]
            dy = self.y - f["y"]
            dh = (self.h - f["h"]) / SC          # 高度差换算像素尺度
            reach = hr + (8 if f["gold"] else 7)
            if dx * dx + dy * dy + dh * dh <= reach * reach:
                f["active"] = False
                f["core"].visible = False
                # 连击：在窗口内连续吃果实叠加倍率
                if self.combo >= 1 and t - self.combo_t <= COMBO_WINDOW:
                    self.combo = min(COMBO_MAX, self.combo + 1)
                else:
                    self.combo = 1
                self.combo_t = t
                mult = 1.0 + 0.25 * (self.combo - 1)
                val = f["value"] + (1 if self.buffs["lucky"] > 0.0 else 0)
                self._gain_xp(int(round(val * mult)), t)
                self._play(self.snd_eat)

        if _pt:
            _a, _s = _s, _pt()
            self._sec["fruit"] += _s - _a
        if not _SKIP_VIS:
            self._update_snake_visual(t)
        if _pt:
            _a, _s = _s, _pt()
            self._sec["snake"] += _s - _a
        if getattr(self, "safe_zone_enabled", False):
            # 安全区用真实帧间隔（不随物理 dt 钳制），联机双端时间线更贴近
            zdt = min(0.2, ursina_time.dt)
            self._update_safe_zone(zdt)
            self._apply_zone_damage(zdt)
            self._update_zone_visuals(t)
        self._update_minimap_dot()
        self._update_energy_hud(t)
        self._update_camera(dt)
        if _pt:
            self._sec["vis"] += _pt() - _s      # vis = 小地图 + 相机 + 平行光跟随

    def _turn(self, diff, max_turn):
        if abs(diff) <= max_turn:
            self.angle = self.target_angle
        else:
            self.angle += max_turn if diff > 0 else -max_turn
        self.angle %= math.pi * 2
        return self.angle

    # ---------- 玩法系统（光环 / 强化道具） ----------

    def _gain_xp(self, amount, t):
        """统一经验入口：双倍道具生效时翻倍，并同步等级 / 长度 / HUD"""
        if amount <= 0:
            return
        if self.buffs["double"] > 0.0:
            amount *= 2
        old = self.level
        self.xp += amount
        self.level = self.xp // SKY_XP_PER_LEVEL + 1
        if self.level > old:
            self.level_flash = t + 0.8
        self.target_len = min(SKY_MAX_LENGTH,
                              SKY_INITIAL_LENGTH + self.xp * SKY_LENGTH_PER_XP)
        self._update_hud_text()

    def _update_rings(self, t):
        """光环重生 + 穿越检测（带符号距离过零且交点落在环口内）"""
        rr2 = (RING_RADIUS - 0.14) ** 2
        for ring in self.rings:
            if not ring["active"]:
                if ring["respawn"] and t >= ring["respawn"]:
                    self._place_ring(ring, t)
                continue
            d1 = self._ring_sd(ring, self.x, self.y, self.h)
            d0 = ring["last_d"]
            ring["last_d"] = d1
            if d0 is None or d0 * d1 >= 0.0:
                continue
            f = d0 / (d0 - d1)
            ax = (self.px - ring["x"]) * SC
            az = (self.py - ring["y"]) * SC
            ah = self.ph - ring["h"]
            bx = (self.x - ring["x"]) * SC
            bz = (self.y - ring["y"]) * SC
            bh = self.h - ring["h"]
            cx = ax + (bx - ax) * f
            cz = az + (bz - az) * f
            ch = ah + (bh - ah) * f
            tang = cx * (-ring["nz"]) + cz * ring["nx"]
            if ch * ch + tang * tang <= rr2:
                self._collect_ring(ring, t)

    def _update_powerups(self, dt, t):
        # 定时刷新（场上数量不足时补充）
        self.next_powerup -= dt
        if self.next_powerup <= 0.0:
            self.next_powerup = POWERUP_INTERVAL
            if any(not p["active"] for p in self.powerup_pool):
                self._spawn_powerup()
        hr = SKY_HEAD_RADIUS
        for p in self.powerup_pool:
            if not p["active"]:
                continue
            p["life"] -= dt
            if p["life"] <= 0.0:
                p["active"] = False
                p["core"].visible = False
                p["glow"].visible = False
                continue
            dx = self.x - p["x"]
            dy = self.y - p["y"]
            dh = (self.h - p["h"]) / SC
            reach = hr + 14
            if dx * dx + dy * dy + dh * dh <= reach * reach:
                p["active"] = False
                p["core"].visible = False
                p["glow"].visible = False
                self._apply_powerup(p["kind"], t)

    def _item_speed_mult(self):
        """道具对前进速度的乘算：极速加速"""
        if self.buffs.get("turbo", 0.0) > 0.0:
            return TURBO_SPEED
        return 1.0

    def _update_buffs(self, dt):
        for k, v in self.buffs.items():
            if v > 0.0:
                self.buffs[k] = max(0.0, v - dt)
        on = self.buffs["shield"] > 0.0
        self.shield_bubble.visible = on
        if on:
            k = 1.25 + 0.07 * math.sin(self._anim_t * 6.5)
            self.shield_bubble.scale = k
            self.shield_bubble.color = color.rgba(
                0.35, 0.9, 1.0, 0.20 + 0.05 * math.sin(self._anim_t * 6.5))
        # 道具拾取提示淡出
        if self.item_toast_t > 0.0:
            self.item_toast_t = max(0.0, self.item_toast_t - dt)
            a = min(1.0, self.item_toast_t / 0.5)
            c = self.item_toast_col
            self.item_text.color = color.rgba(c[0], c[1], c[2], a)
            self.item_icon.color = color.rgba(1, 1, 1, a)
            if self.item_toast_t <= 0.0:
                self.item_icon.visible = False
        self._update_buff_hud()

    def _pull_fruits(self, dt):
        """磁铁：把范围内果实吸向蛇头"""
        r2 = MAGNET_RANGE * MAGNET_RANGE
        step = MAGNET_PULL_X * dt
        for f in self.fruit_pool:
            if not f["active"]:
                continue
            dx = f["x"] - self.x
            dy = f["y"] - self.y
            if dx * dx + dy * dy > r2:
                continue
            dist = math.sqrt(dx * dx + dy * dy) + 1e-6
            k = min(1.0, step / dist)
            f["x"] -= dx * k
            f["y"] -= dy * k
            dh = self.h - f["h"]
            f["h"] += max(-MAGNET_PULL_H * dt, min(MAGNET_PULL_H * dt, dh))

    # ---------- 世界动画 ----------

    def _animate_world(self, t, dt):
        # 云漂移 + 浮动
        span = self.cloud_span
        for cl in self.clouds:
            cx = (cl["x"] * SC + t * cl["sp"]) % span - 30
            cl["e"].position = (cx, cl["y"] + math.sin(t * 0.5 + cl["ph"]) * 0.5,
                                cl["z"] * SC)
        # 星辰闪烁（白天已隐藏，仅在启用时呼吸）
        for i, g in enumerate(getattr(self, "star_groups", ())):
            if not g.enabled:
                continue
            a = 0.45 + 0.45 * math.sin(t * (0.7 + i * 0.35) + i * 2.1)
            g.color = color.rgba(0.85, 0.9, 1.0, a)
        # 彩虹呼吸
        self.rainbow.color = color.rgba(1, 1, 1, 0.30 + 0.12 * math.sin(t * 0.6))
        # 太阳光晕与光柱脉动
        self.sun_halo.scale = 26 + 1.8 * math.sin(t * 0.9)
        for i, beam in enumerate(self.rays):
            beam.color = color.rgba(1, 0.94, 0.72, 0.07 + 0.05 * math.sin(t * 0.5 + i))
            beam.rotation_y = 12 * math.sin(t * 0.11 + i * 2.1)
        # 天马行空：椭圆巡游 + 振翅 + 星光拖尾
        pt = t
        px = _w(WORLD * 0.5) + math.sin(pt * 0.055) * _w(WORLD * 0.30)
        pz = _w(WORLD * 0.30) + math.sin(pt * 0.115) * _w(WORLD * 0.13)
        py = 11 + math.sin(pt * 0.23) * 2.0
        dx = math.cos(pt * 0.055) * 0.055 * _w(WORLD * 0.30)
        dz = math.cos(pt * 0.115) * 0.115 * _w(WORLD * 0.13)
        self.pegasus.position = (px, py, pz)
        self.pegasus.rotation_y = -math.degrees(math.atan2(dz, dx))
        flap = math.sin(pt * 7) * 0.7
        self.wing_l.rotation_x = -math.degrees(flap) * 0.5
        self.wing_r.rotation_x = math.degrees(flap) * 0.5
        if int(t * 9) != int((t - dt) * 9):     # 约 9Hz 投放星点
            slot = min(self.trail_pool, key=lambda s: s["t"])
            slot["t"] = t
            slot["e"].enabled = True
            slot["e"].position = (px - dx * 8, py + 0.4, pz - dz * 8)
        for s in self.trail_pool:
            if s["t"] >= 0:
                age = t - s["t"]
                if age > 2.2:
                    s["t"] = -1
                    s["e"].enabled = False
                else:
                    k = 1 - age / 2.2
                    s["e"].scale = 0.9 * k + 0.15
                    s["e"].color = color.rgba(0.7, 0.85, 1.0, 0.75 * k)
        # 陨石缓慢自转（仅视野附近的实体参与，远处静止以省开销）
        fx, fz = self.x, self.y
        for m in self.meteors:
            if (m["x"] - fx) ** 2 + (m["y"] - fz) ** 2 > 1440000:   # 1200 像素（120 单位）外
                continue
            sx, sy, sz = m["sp"]
            m["e"].rotation_x += sx * dt
            m["e"].rotation_y += sy * dt
            m["e"].rotation_z += sz * dt
        # 果实脉动（glow 挂在 core 上，只需设置 core 位置）
        for f in self.fruit_pool:
            if f["active"]:
                f["core"].setPos(_w(f["x"]), f["h"] + 0.14 * math.sin(t * 2.2 + f["ph"]),
                                 _w(f["y"]))
                f["glow"].scale = (3.0 + 0.5 * math.sin(t * 3.1 + f["ph"])) / 1.15
        # 光环辉光呼吸
        for ring in self.rings:
            if ring["active"]:
                k = 0.13 + 0.06 * math.sin(t * 2.0 + ring["ph"])
                ring["glow"].color = color.rgb(k, k * 0.78, k * 0.34)
        # 强化道具：漂浮 + 自转 + 光晕脉动
        for p in self.powerup_pool:
            if not p["active"]:
                continue
            yy = p["h"] + 0.22 * math.sin(t * 2.4 + p["ph"])
            p["core"].setPos(_w(p["x"]), yy, _w(p["y"]))
            p["core"].rotation_y = (t * 90 + p["ph"] * 57) % 360
            p["core"].rotation_x = (t * 42) % 360
            p["glow"].setPos(_w(p["x"]), yy, _w(p["y"]))
            p["glow"].scale = 1.9 + 0.3 * math.sin(t * 3.0 + p["ph"])
        # 水晶簇光晕脉动 + 湖面微光
        for i, g in enumerate(getattr(self, "crystal_glows", ())):
            k = 0.52 + 0.24 * math.sin(t * 1.7 + g["ph"])
            g["e"].scale = 4.6 + 0.8 * math.sin(t * 1.9 + g["ph"])
            c = g["col"]
            g["e"].color = color.rgb(c[0] * k, c[1] * k, c[2] * k)
        if getattr(self, "water", None) is not None:
            self.water.color = color.rgba(1.0, 1.0, 1.0,
                                          0.68 + 0.05 * math.sin(t * 1.1))
        # 软墙轮廓：靠近显形、贴墙呼吸
        self._update_soft_walls_visual(t)

    # ---------- 蛇与相机 ----------

    def _update_snake_visual(self, t):
        """逐帧只更新可见段位置（setPos 直连）；颜色/尺寸仅在段数变化时重算"""
        n = len(self.trail)
        bob0 = math.sin(t * 3.0) * 0.1
        self.head.setPos(_w(self.x), self.h + bob0, _w(self.y))
        self.head.rotation_y = -math.degrees(self.angle)
        self.head.rotation_z = math.degrees(self.pitch)     # 头部随俯仰倾斜
        prev_n = self._vis_n
        if n != prev_n:
            self._recolor_segments(n)
        camx, camy, camz = camera.position
        hide2 = NEAR_HIDE * NEAR_HIDE
        for i in range(n):
            tx, ty, th = self.trail[i]
            sx, sz = _w(tx), _w(ty)
            sy = th + math.sin(t * 3.0 - i * 0.3) * 0.1
            e = self.seg_ents[i]
            # 身体段的地面碰撞：段球心低于“地表 + 段半径”时贴地抬起，避免插进山体
            rad = e.scale_x * 0.5
            if sy - rad < TERRAIN_MAX:      # 高于全部地表时跳过采样
                top = self._column_top(tx, ty)
                if top is not None:
                    floor = top + rad - 0.02
                    if sy < floor:
                        sy = floor
            e.setPos(sx, sy, sz)
            dx, dy, dz = sx - camx, sy - camy, sz - camz
            e.visible = (dx * dx + dy * dy + dz * dz) > hide2
        for i in range(n, prev_n):          # 长度缩短时隐藏多余段（仅变化帧）
            self.seg_ents[i].visible = False
        self._vis_n = n

    def _recolor_segments(self, n):
        """段数变化时重设可见段的可见性/尺寸/渐变色"""
        c1, c2 = self.skin["c1"], self.skin["c2"]
        denom = max(1, n - 1)
        for i in range(n):
            e = self.seg_ents[i]
            k = i / denom
            e.visible = True
            e.scale = (0.92 * (1 - k) + 0.34 * k) * 2
            e.color = color.rgb(
                (c1[0] * (1 - k) + c2[0] * k) / 255,
                (c1[1] * (1 - k) + c2[1] * k) / 255,
                (c1[2] * (1 - k) + c2[2] * k) / 255)

    def _update_camera(self, dt):
        """追尾相机：跟随蛇头朝向（含俯仰），抬高越过蛇身，且不沉入地形"""
        fx, fz = _w(self.x), _w(self.y)
        cp = math.cos(self.pitch)
        fwdx = cp * math.cos(self.angle)
        fwdz = cp * math.sin(self.angle)
        sp = math.sin(self.pitch)
        d = self.cam_dist
        cx = fx - fwdx * d
        cz = fz - fwdz * d
        cy = self.h - sp * d + 3.2
        top = self._column_top(cx / SC, cz / SC)     # 相机不入地
        if top is not None:
            cy = max(cy, top + 1.5)
        # 地形顶起的分量单独平滑：贴地/越山时镜头不会被硬顶后瞬间回弹（原来那阵抖动就来自这里）
        prev_cy = getattr(self, "_cam_cy", cy)
        k_y = 1.0 - math.exp(-dt * (7.0 if cy > prev_cy else 16.0))
        self._cam_cy = prev_cy + (cy - prev_cy) * k_y
        # 水平位置刚性跟随：转向时镜头不再滞后（原来 1-exp(-dt*9) 有 111ms 延迟，
        # 高速转弯会甩尾，观感就是"镜头乱晃"）；垂直方向仍保留非对称平滑，爬升俯冲更顺
        camera.position = Vec3(cx, self._cam_cy, cz)
        camera.look_at(Vec3(fx + fwdx * 6.0, self.h + sp * 6.0 + 1.2, fz + fwdz * 6.0))
        camera.rotation_z = 0           # 消除 look_at 的滚转（世界为 Y-up，保持地平线水平）
        # 平行光跟随玩家（仅移动位置：look_at 会触发全场景 update_bounds，每帧调用极慢）
        self.sun_light.setPos(fx + 30, 50, fz + 20)

    # ---------- 安全区（联机 / 人机对战） ----------

    @staticmethod
    def _zone_wall_mesh(dashed=False, seg=96):
        """单位圆柱能量墙：整面保持可辨（深蓝半透），底边更实；dashed 生成虚线结界"""
        verts, tris, cols = [], [], []
        for i in range(seg):
            if dashed and (i % 2):
                continue
            a0 = math.tau * i / seg
            a1 = math.tau * (i + 1) / seg
            x0, z0 = math.cos(a0), math.sin(a0)
            x1, z1 = math.cos(a1), math.sin(a1)
            b = len(verts)
            verts += [(x0, 0.0, z0), (x1, 0.0, z1),
                      (x1, 1.0, z1), (x0, 1.0, z0)]
            tris += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
            cols += [(1, 1, 1, 1.0), (1, 1, 1, 1.0),
                     (1, 1, 1, 0.62), (1, 1, 1, 0.62)]
        return _mesh(verts, tris, cols)

    @staticmethod
    def _zone_bolt_mesh():
        """闪电面片：两个互相垂直的竖直面，任意角度都看得到"""
        verts, tris, uvs = [], [], []
        for axis in (0, 1):
            b = len(verts)
            if axis == 0:
                quad = [(-0.5, 0.0, 0.0), (0.5, 0.0, 0.0),
                        (0.5, 1.0, 0.0), (-0.5, 1.0, 0.0)]
            else:
                quad = [(0.0, 0.0, -0.5), (0.0, 0.0, 0.5),
                        (0.0, 1.0, 0.5), (0.0, 1.0, -0.5)]
            verts += quad
            tris += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
            uvs += [(0, 0), (1, 0), (1, 1), (0, 1)]
        return Mesh(vertices=verts, triangles=tris, uvs=uvs,
                    mode="triangle", static=True)

    def _make_zone_wall(self, dashed=False):
        e = Entity(model=self._zone_wall_mesh(dashed))
        e.setTwoSided(True)
        e.setTransparency(TransparencyAttrib.MAlpha)
        _unlit(e, depth_write=False)
        e.setFogOff(1)
        e.color = color.rgba(0.5, 0.8, 1.0, 0.2)
        e.visible = False
        return e

    def _enable_safe_zone(self):
        """在对战模式开局调用：建立/重置安全区状态与视觉"""
        if not getattr(self, "_zone_built", False):
            self._zone_built = True
            self.zone_wall = self._make_zone_wall(False)
            self.zone_next_wall = self._make_zone_wall(False)
            self.zone_danger_wall = self._make_zone_wall(True)
            # 圈外危险雾带：紧贴边界外侧的一层红紫暗雾
            self.zone_haze = self._make_zone_wall(False)
            self.zone_haze.color = color.rgba(0.34, 0.06, 0.20, 0.13)
            # 结界顶沿亮环：一小段浅蓝高光环，让"墙到哪里结束"一眼可见
            self.zone_rim = self._make_zone_wall(False)
            self.zone_rim.color = color.rgba(0.62, 0.86, 1.0, 0.66)
            # 边界能量光柱：各自独立网格（Ursina 中 Mesh 不能跨实体共用）
            self.zone_pillars = []
            for _ in range(ZONE_PILLARS):
                p = Entity(model=self._zone_wall_mesh(False, seg=14),
                           color=color.rgba(0.55, 0.88, 1.0, 0.0))
                _additive(p)
                p.setFogOff(1)
                p.visible = False
                self.zone_pillars.append(p)
            # 边界竖状闪电：随机位置电闪 + 快速淡出
            bolt_tex = Texture(_make_zone_bolt_texture())
            self.zone_bolts = []
            for i in range(ZONE_BOLTS):
                b = Entity(model=self._zone_bolt_mesh(), texture=bolt_tex,
                           color=color.rgba(0.80, 0.94, 1.0, 0.0))
                b.setTwoSided(True)
                _additive(b)
                b.setFogOff(1)
                b.visible = False
                self.zone_bolts.append({"e": b, "wait": 0.2 + i * 0.17,
                                        "life": 0.0})
            # 边界上升粒子（细小光点，沿结界向上飘散）
            spark_tex = Texture(_make_glow_texture(48))
            self.zone_sparks = []
            for _ in range(ZONE_SPARKS):
                s = Entity(model="quad", texture=spark_tex,
                           color=color.rgba(0.62, 0.92, 1.0, 0.0))
                _additive(s)
                s.setFogOff(1)
                s.visible = False
                self.zone_sparks.append({"e": s, "wait": 0.0, "life": 0.0,
                                         "ang": 0.0, "y": 0.0, "vy": 1.0,
                                         "r": 0.0})
        self._zone_reset_state()

    def _zone_reset_state(self):
        self.safe_zone_enabled = True
        self.zone_rng = random.Random(ZONE_SEED)
        self.zone_cx = self.zone_cy = WORLD / 2.0
        self.zone_r = ZONE_RADII[0] * WORLD
        self.zone_phase = 0
        self.zone_stage = "hold"
        self.zone_timer = ZONE_START_DELAY
        self.zone_to = None
        self.zone_from = (self.zone_cx, self.zone_cy, self.zone_r)
        self.zone_dmg_t = 0.0
        self.zone_acc = 0.0
        self.zone_outside = False
        # 结界特效：固定种子 + 重置计时，联机双端电弧表现一致
        self._zone_fx_rng = random.Random(ZONE_SEED + 7)
        self._zone_fx_t = None

    def _zone_pick_next(self):
        i = self.zone_phase + 1
        if i >= len(ZONE_RADII):
            return None
        nr = ZONE_RADII[i] * WORLD
        lim = max(0.0, self.zone_r - nr)
        ang = self.zone_rng.uniform(0.0, math.tau)
        dist = lim * self.zone_rng.uniform(0.35, 0.80)
        cx = self.zone_cx + math.cos(ang) * dist
        cy = self.zone_cy + math.sin(ang) * dist
        edge = max(0.0, WORLD / 2.0 - nr - LAND_BAND * 0.4)
        cx = min(max(cx, WORLD / 2.0 - edge), WORLD / 2.0 + edge)
        cy = min(max(cy, WORLD / 2.0 - edge), WORLD / 2.0 + edge)
        return (cx, cy, nr)

    def _update_safe_zone(self, dt):
        if getattr(self, "over", False) or self.zone_stage == "final":
            return
        self.zone_timer -= dt
        if self.zone_stage == "hold":
            # 收缩前 ZONE_WARN_TIME 秒进入预警：提前算出下一圈，显示旧/新双边界
            if self.zone_to is None and self.zone_timer <= ZONE_WARN_TIME:
                self.zone_to = self._zone_pick_next()
            if self.zone_timer <= 0.0:
                if self.zone_to is None:
                    self.zone_stage = "final"
                    self.zone_timer = 0.0
                else:
                    self.zone_stage = "shrink"
                    self.zone_timer = ZONE_SHRINK_TIME
                    self.zone_from = (self.zone_cx, self.zone_cy, self.zone_r)
        else:                                   # shrink：平滑收缩
            k = 1.0 - max(0.0, self.zone_timer) / ZONE_SHRINK_TIME
            k = k * k * (3.0 - 2.0 * k)         # 起步/收尾顺滑，中后段略快
            fx, fy, fr = self.zone_from
            tx, ty, tr = self.zone_to
            self.zone_cx = fx + (tx - fx) * k
            self.zone_cy = fy + (ty - fy) * k
            self.zone_r = fr + (tr - fr) * k
            if self.zone_timer <= 0.0:
                self.zone_cx, self.zone_cy, self.zone_r = self.zone_to
                self.zone_phase += 1
                self.zone_to = None
                if self.zone_phase >= len(ZONE_RADII) - 1:
                    self.zone_stage = "final"
                    self.zone_timer = 0.0
                else:
                    self.zone_stage = "hold"
                    self.zone_timer = ZONE_HOLD_TIME

    def _apply_zone_damage(self, dt):
        if getattr(self, "over", False):
            return
        d = math.hypot(self.x - self.zone_cx, self.y - self.zone_cy)
        outside = d > self.zone_r
        self.zone_outside = outside
        if outside:
            self.zone_dmg_t += dt
            if self.zone_dmg_t >= 0.45:
                # 累计小数伤害再结算，保证长期平均 DPS 与提示一致
                dps = ZONE_DPS[min(self.zone_phase, len(ZONE_DPS) - 1)]
                self.zone_acc += dps * self.zone_dmg_t
                self.zone_dmg_t = 0.0
                dmg = int(self.zone_acc)
                if dmg > 0:
                    self.zone_acc -= dmg
                    self._zone_hurt(dmg)
        else:
            self.zone_dmg_t = 0.0
            self.zone_acc = 0.0
        self._zone_tick_others(dt)

    def _zone_hurt(self, dmg):
        """圈外扣血（子类实现）"""
        pass

    def _zone_tick_others(self, dt):
        """子类可在此处理 AI / 远端角色的圈外伤害"""
        pass

    def _update_zone_visuals(self, t):
        if not getattr(self, "safe_zone_enabled", False):
            return
        vis = not getattr(self, "over", False)
        pulse = 0.5 + 0.5 * math.sin(t * 2.4)
        wx, wz, wr = _w(self.zone_cx), _w(self.zone_cy), _w(self.zone_r)
        # 当前安全区边界（淡蓝能量环，轻微呼吸）
        self.zone_wall.visible = vis
        self.zone_wall.position = (wx, ZONE_WALL_Y, wz)
        self.zone_wall.scale = (wr, ZONE_WALL_H, wr)
        if self.zone_stage == "shrink":
            self.zone_wall.color = color.rgba(0.10, 0.24, 0.72, 0.56 + 0.12 * pulse)
        elif self.zone_stage == "final":
            self.zone_wall.color = color.rgba(0.12, 0.27, 0.80, 0.54 + 0.10 * pulse)
        else:
            self.zone_wall.color = color.rgba(0.09, 0.21, 0.66, 0.48 + 0.10 * pulse)
        # 圈外红紫暗雾：紧贴边界外侧的一层
        self.zone_haze.visible = vis
        self.zone_haze.position = (wx, ZONE_WALL_Y, wz)
        self.zone_haze.scale = (wr * 1.10, ZONE_WALL_H, wr * 1.10)
        self.zone_haze.color = color.rgba(0.36, 0.05, 0.22, 0.13 + 0.05 * pulse)
        # 顶沿亮环
        rim = getattr(self, "zone_rim", None)
        if rim is not None:
            rim.visible = vis
            rim.position = (wx, ZONE_WALL_Y + ZONE_WALL_H - 1.1, wz)
            rim.scale = (wr, 1.1, wr)
            if self.zone_stage == "final":
                rim.color = color.rgba(0.78, 0.90, 1.0, 0.72 + 0.16 * pulse)
            else:
                rim.color = color.rgba(0.62, 0.86, 1.0, 0.60 + 0.18 * pulse)
        # 边界能量光柱：沿圈缓慢环绕 + 各自呼吸
        for i, p in enumerate(self.zone_pillars):
            if not vis:
                p.visible = False
                continue
            a = t * 0.16 + math.tau * i / ZONE_PILLARS
            p.visible = True
            p.position = (wx + math.cos(a) * wr, ZONE_WALL_Y, wz + math.sin(a) * wr)
            pl = 0.5 + 0.5 * math.sin(t * 3.1 + i * 1.7)
            p.scale = (1.7 + 0.5 * pl, 15.0 + 8.0 * pl, 1.7 + 0.5 * pl)
            p.color = color.rgba(0.24, 0.46, 0.95, 0.16 + 0.18 * pl)
        # 竖状闪电 + 上升粒子（沿结界随机出现）
        fdt = min(0.1, max(0.0, t - self._zone_fx_t)) \
            if self._zone_fx_t is not None else 0.0
        self._zone_fx_t = t
        rng = self._zone_fx_rng
        for b in self.zone_bolts:
            e = b["e"]
            if not vis:
                e.visible = False
                continue
            if b["life"] > 0.0:
                b["life"] -= fdt
                if b["life"] <= 0.0:
                    e.visible = False
                    b["wait"] = rng.uniform(0.35, 1.2)
                    continue
                k = b["life"] / max(1e-4, b["dur"])
                flick = 0.6 + 0.4 * abs(math.sin(t * 47.0 + b["ph"]))
                e.color = color.rgba(0.82, 0.95, 1.0, 0.95 * k * flick)
            else:
                b["wait"] -= fdt
                if b["wait"] <= 0.0:
                    a = rng.uniform(0.0, math.tau)
                    e.position = (wx + math.cos(a) * wr,
                                  ZONE_WALL_Y + 10.0 + rng.uniform(0.0, 3.0),
                                  wz + math.sin(a) * wr)
                    e.scale = (rng.uniform(3.0, 4.5),
                               rng.uniform(9.0, 15.0),
                               rng.uniform(3.0, 4.5))
                    e.rotation_y = math.degrees(-a) + rng.uniform(-35, 35)
                    b["dur"] = b["life"] = rng.uniform(0.16, 0.42)
                    b["ph"] = rng.uniform(0.0, 6.28)
                    e.visible = True
                    e.color = color.rgba(0.82, 0.95, 1.0, 0.9)
        for s in self.zone_sparks:
            e = s["e"]
            if not vis:
                e.visible = False
                continue
            if s["life"] > 0.0:
                s["life"] -= fdt
                s["y"] += s["vy"] * fdt
                k = max(0.0, s["life"] / max(1e-4, s["dur"]))
                e.position = (wx + math.cos(s["ang"]) * s["r"], s["y"],
                              wz + math.sin(s["ang"]) * s["r"])
                e.scale = 0.5 + 0.75 * k
                e.color = color.rgba(0.62, 0.92, 1.0, 0.7 * k)
                if s["life"] <= 0.0:
                    e.visible = False
                    s["wait"] = rng.uniform(0.05, 0.6)
            else:
                s["wait"] -= fdt
                if s["wait"] <= 0.0:
                    s["ang"] = rng.uniform(0.0, math.tau)
                    s["r"] = wr * rng.uniform(0.97, 1.04)
                    s["y"] = ZONE_WALL_Y + 8.0 + rng.uniform(0.0, 5.0)
                    s["vy"] = rng.uniform(3.0, 7.0)
                    s["dur"] = s["life"] = rng.uniform(0.7, 1.6)
                    e.visible = True
        has_next = self.zone_to is not None
        # 下一个圈：预警期亮白脉冲
        self.zone_next_wall.visible = vis and has_next
        if has_next:
            nx, ny, nr = self.zone_to
            self.zone_next_wall.position = (_w(nx), ZONE_WALL_Y, _w(ny))
            self.zone_next_wall.scale = (_w(nr), ZONE_WALL_H, _w(nr))
            a = (0.34 + 0.26 * pulse) if self.zone_stage == "hold" else 0.46
            self.zone_next_wall.color = color.rgba(0.26, 0.48, 0.96, a)
        # 旧边界：虚线红墙，危险过渡带
        self.zone_danger_wall.visible = vis and has_next
        if has_next:
            if self.zone_stage == "shrink":
                ox, oy, orr = self.zone_from
            else:
                ox, oy, orr = self.zone_cx, self.zone_cy, self.zone_r
            self.zone_danger_wall.position = (_w(ox), ZONE_WALL_Y, _w(oy))
            self.zone_danger_wall.scale = (_w(orr), ZONE_WALL_H, _w(orr))
            flick = 0.5 + 0.5 * math.sin(t * 9.0)
            self.zone_danger_wall.color = color.rgba(
                0.88, 0.16, 0.26, 0.09 + 0.13 * flick)
        # 圈外：红黑暗雾 + 暗角滤镜
        out = vis and self.zone_outside
        self.zone_tint.enabled = out
        self.zone_vignette.enabled = out
        if out:
            self.zone_tint.color = color.rgba(0.20, 0.01, 0.07, 0.20)
            flick = 0.5 + 0.5 * math.sin(t * 4.5)
            self.zone_vignette.color = color.rgba(1, 1, 1, 0.48 + 0.16 * flick)
        # 顶部状态文字
        total = len(ZONE_RADII) - 1
        if self.zone_stage == "final":
            status, col = "决赛圈", (0.58, 0.90, 1.0)
        elif self.zone_stage == "shrink":
            status = f"安全区缓慢收缩 {max(0, int(self.zone_timer + 0.99))}s · 第{self.zone_phase + 1}/{total} 次"
            col = (0.62, 0.90, 1.0)
        elif has_next:
            status = f"下一个安全区已标记 · {max(0, int(self.zone_timer + 0.99))}s 后收缩 · 第{self.zone_phase + 1}/{total} 次"
            col = (1.0, 0.72, 0.35)
        else:
            status = f"安全区 {max(0, int(self.zone_timer + 0.99))}s 后收缩 · 第{self.zone_phase + 1}/{total} 次"
            col = (0.58, 0.84, 1.0)
        self.zone_text.enabled = vis
        self.zone_text.text = status
        self.zone_text.color = color.rgba(col[0], col[1], col[2], 0.92)
        self.zone_warn.enabled = out
        if out:
            dps = ZONE_DPS[min(self.zone_phase, len(ZONE_DPS) - 1)]
            self.zone_warn.text = f"安全区外  -{dps:g} HP/s"
            a = 0.62 + 0.38 * abs(math.sin(t * 4.2))
            self.zone_warn.color = color.rgba(1.0, 0.30, 0.26, a)
        # 小地图圈
        if hasattr(self, "mm_zone"):
            self.mm_zone.enabled = vis
            self.mm_zone.position = (0.5 + (self.zone_cx / WORLD - 0.5) * 0.24,
                                     0.33 + (0.5 - self.zone_cy / WORLD) * (0.24 * 9 / 7))
            dd = 2.0 * self.zone_r / WORLD
            self.mm_zone.scale = (0.24 * dd, 0.24 * 9 / 7 * dd)
            self.mm_zone_next.enabled = vis and has_next
            if has_next:
                nx, ny, nr = self.zone_to
                self.mm_zone_next.position = (
                    0.5 + (nx / WORLD - 0.5) * 0.24,
                    0.33 + (0.5 - ny / WORLD) * (0.24 * 9 / 7))
                dn = 2.0 * nr / WORLD
                self.mm_zone_next.scale = (0.24 * dn, 0.24 * 9 / 7 * dn)

    # ---------- HUD / 菜单 ----------

    def _build_hud(self):
        kw = {"font": self.font} if self.font else {}
        self.hud_lv = Text(text="", position=(-0.62, 0.44), scale=1.4,
                           color=color.rgb(0.4, 0.95, 1.0), **kw)
        self.hud_hint = Text(
            text="移动鼠标转向 · Shift 冲刺 · 穿金色光环得分 · 拾取强化水晶 · 滚轮缩放 · F11 全屏 · P 暂停 · ESC 菜单",
            position=(0, -0.47), scale=0.9, color=color.rgba(1, 1, 1, 0.75),
            origin=(0, 0), **kw)
        # 准星（鼠标隐藏后的中心参照）
        Entity(parent=camera.ui, model="quad", color=color.rgba(1, 1, 1, 0.7),
               scale=(0.018, 0.0022))
        Entity(parent=camera.ui, model="quad", color=color.rgba(1, 1, 1, 0.7),
               scale=(0.0022, 0.018))
        # 经验条
        self.xp_bar_bg = Entity(parent=camera.ui, model="quad",
                                color=color.rgba(0.1, 0.15, 0.3, 0.7),
                                position=(-0.44, 0.395), scale=(0.36, 0.018))
        self.xp_bar = Entity(parent=camera.ui, model="quad",
                             color=color.rgb(0.3, 0.9, 0.5),
                             position=(-0.44, 0.395), scale=(0.001, 0.014))
        # 能量条（冲刺）
        self.energy_bar_bg = Entity(parent=camera.ui, model="quad",
                                    color=color.rgba(0.1, 0.15, 0.3, 0.7),
                                    position=(-0.44, 0.368), scale=(0.36, 0.012))
        self.energy_bar = Entity(parent=camera.ui, model="quad",
                                 color=color.rgb(0.35, 0.85, 1.0),
                                 position=(-0.44, 0.368), scale=(0.36, 0.009))
        # 连击提示
        self.combo_text = Text(text="", position=(0.0, 0.30), scale=1.5,
                               color=color.rgba(1, 0.86, 0.4, 0), origin=(0, 0), **kw)
        # 道具插图（assets/icons/*.png，运行时缺失则退回符号）
        self.icon_tex = {}
        for key in POWERUP_COLORS:
            path = resource_path(f"assets/icons/{key}.png")
            try:
                self.icon_tex[key] = Texture(path) if os.path.exists(path) else None
            except Exception:
                self.icon_tex[key] = None
        # 拾取道具提示（插图 + 文字，淡出）
        self.item_icon = Entity(parent=camera.ui, model="quad",
                                position=(-0.105, 0.245), scale=(0.052, 0.052),
                                color=color.rgba(1, 1, 1, 0), visible=False)
        self.item_text = Text(text="", position=(0.008, 0.245), scale=1.15,
                              color=color.rgba(1, 1, 1, 0), origin=(0, 0), **kw)
        self.item_toast_t = 0.0
        self.item_toast_col = (1.0, 1.0, 1.0)
        # 安全区状态 / 圈外警示（避开顶部房间号/对手血条）
        self.zone_text = Text(text="", position=(0.0, 0.366), scale=0.95,
                              color=color.rgba(0.58, 0.84, 1.0, 0.92),
                              origin=(0, 0), enabled=False, **kw)
        self.zone_warn = Text(text="", position=(0.0, 0.326), scale=1.0,
                              color=color.rgba(1.0, 0.30, 0.26, 0.0),
                              origin=(0, 0), enabled=False, **kw)
        # 圈外红黑暗雾 + 暗角（只覆盖世界，HUD 仍在最上层）
        self.zone_tint = Entity(parent=camera.ui, model="quad",
                                color=color.rgba(0.26, 0.02, 0.08, 0.0),
                                scale=(2.2, 2.2), z=0.10, enabled=False)
        self.zone_vignette = Entity(
            parent=camera.ui, model="quad",
            texture=Texture(_make_zone_vignette_texture()),
            color=color.rgba(1, 1, 1, 0.0),
            scale=(2.2, 2.2), z=0.05, enabled=False)
        # 强化状态图标（道具插图 + 类型色光晕 + 剩余时间条）
        self.buff_uis = []
        for i, key in enumerate(("shield", "magnet", "double", "turbo", "lucky")):
            x = -0.44 + i * 0.058
            col = POWERUP_COLORS[key]
            root = Entity(parent=camera.ui, enabled=False, position=(x, 0.330))
            # 外发光
            Entity(parent=root, model="quad", scale=(0.060, 0.060),
                   color=color.rgba(col[0], col[1], col[2], 0.20))
            # 深色底板
            Entity(parent=root, model="quad", scale=(0.050, 0.050),
                   color=color.rgba(0.04, 0.09, 0.19, 0.86))
            tex = self.icon_tex.get(key)
            if tex is not None:
                Entity(parent=root, model="quad", texture=tex,
                       scale=(0.048, 0.048), z=-0.01)
            else:
                Text(parent=root, text="◆", origin=(0, 0), scale=0.9,
                     color=color.rgb(col[0], col[1], col[2]), **kw)
            bar = Entity(parent=root, model="quad",
                         color=color.rgb(col[0], col[1], col[2]),
                         position=(-0.017, -0.031), scale=(0.034, 0.005))
            self.buff_uis.append((key, root, bar))
        # 小地图
        self._build_minimap()
        # 暂停 / 结算面板
        self._build_menus(kw)
        # 需要随菜单一起隐藏的 HUD 元素
        self._hud_roots = [self.hud_lv, self.hud_hint, self.xp_bar_bg,
                           self.xp_bar, self.energy_bar_bg, self.energy_bar,
                           self.combo_text, self.item_text, self.item_icon,
                           self.zone_text, self.zone_warn,
                           self.zone_tint, self.zone_vignette,
                           self.mm, self.mm_dot] + \
                          [root for _, root, _ in self.buff_uis]

    def _set_hud_visible(self, visible):
        for e in self._hud_roots:
            e.enabled = visible
        for i, dot in enumerate(self.mm_ring_dots):
            dot.enabled = visible and self.rings[i]["active"]
        for i, dot in enumerate(self.mm_pu_dots):
            dot.enabled = visible and self.powerup_pool[i]["active"]

    def _build_minimap(self):
        from PIL import Image, ImageDraw
        from ursina import Texture
        S = 190
        img = Image.new("RGBA", (S, S), (6, 12, 24, 200))
        d = ImageDraw.Draw(img)
        sc = S / WORLD
        for s in self.land:
            d.polygon([(x * sc, y * sc) for x, y in s["pts"]], fill=(96, 76, 52, 255))
        for s in self.walls:
            d.polygon([(x * sc, y * sc) for x, y in s["pts"]], fill=(122, 134, 158, 255))
        for m in self.meteors:              # 陨石点位（便于提前规划路线）
            mx, my = m["x"] * sc, m["y"] * sc
            r = max(1.5, m.get("cr", m["r"]) * sc * 2.2)
            d.ellipse((mx - r, my - r, mx + r, my + r), fill=(206, 200, 186, 170))
        d.rectangle((0, 0, S - 1, S - 1), outline=(150, 180, 220, 255))
        self.mm = Entity(parent=camera.ui, model="quad", texture=Texture(img),
                         position=(0.5, 0.33), scale=(0.24, 0.24 * 9 / 7))
        # 安全区圈：当前边界（实线）/ 下一个圈（虚线）
        self.mm_zone = Entity(parent=camera.ui, model="quad",
                              texture=Texture(_make_zone_ring_texture()),
                              color=color.rgba(0.62, 0.88, 1.0, 0.95),
                              position=(0.5, 0.33), scale=(0.0, 0.0),
                              enabled=False, z=-0.02)
        self.mm_zone_next = Entity(
            parent=camera.ui, model="quad",
            texture=Texture(_make_zone_ring_texture(dashed=True)),
            color=color.rgba(0.95, 0.98, 1.0, 0.9),
            position=(0.5, 0.33), scale=(0.0, 0.0),
            enabled=False, z=-0.03)
        self.mm_dot = Entity(parent=camera.ui, model="quad",
                             color=color.rgb(0.3, 1.0, 0.5), scale=(0.012, 0.012))
        self.mm_ring_dots = [Entity(parent=camera.ui, model="quad",
                                    color=color.rgb(1.0, 0.82, 0.30),
                                    scale=(0.009, 0.009), enabled=False)
                             for _ in range(RING_COUNT)]
        self.mm_pu_dots = [Entity(parent=camera.ui, model="quad",
                                  color=color.rgb(1.0, 0.62, 1.0),
                                  scale=(0.009, 0.009), enabled=False)
                           for _ in range(POWERUP_MAX)]

    def _update_minimap_dot(self):
        ux = 0.5 + (self.x / WORLD - 0.5) * 0.24
        uy = 0.33 + (0.5 - self.y / WORLD) * (0.24 * 9 / 7)
        self.mm_dot.position = (ux, uy)
        for i, ring in enumerate(self.rings):         # 金色光环点位
            dot = self.mm_ring_dots[i]
            dot.enabled = ring["active"]
            if ring["active"]:
                dot.position = (0.5 + (ring["x"] / WORLD - 0.5) * 0.24,
                                0.33 + (0.5 - ring["y"] / WORLD) * (0.24 * 9 / 7))
        for i, p in enumerate(self.powerup_pool):     # 强化道具点位（按类型着色）
            dot = self.mm_pu_dots[i]
            dot.enabled = p["active"]
            if p["active"]:
                dot.position = (0.5 + (p["x"] / WORLD - 0.5) * 0.24,
                                0.33 + (0.5 - p["y"] / WORLD) * (0.24 * 9 / 7))
                dot.color = color.rgb(*POWERUP_COLORS[p["kind"]])

    def _build_menus(self, kw):
        net_mode = getattr(self, "conn", None) is not None

        # 快捷暂停提示（P 键）
        self.pause_badge = Entity(parent=camera.ui, enabled=False, z=-0.40)
        Entity(parent=self.pause_badge, model="quad",
               color=color.rgba(0.30, 0.88, 1.0, 0.20), scale=(0.412, 0.162),
               z=0.04)
        Entity(parent=self.pause_badge, model="quad",
               color=color.rgba(0.02, 0.05, 0.12, 0.94), scale=(0.40, 0.15),
               z=0.02)
        Entity(parent=self.pause_badge, model="quad",
               color=color.rgba(0.30, 0.88, 1.0, 0.75),
               position=(0, 0.072), scale=(0.32, 0.003), z=-0.01)
        Text(parent=self.pause_badge, text="已暂停", position=(0, 0.028),
             origin=(0, 0), scale=1.5, color=color.rgb(0.5, 0.95, 1.0),
             z=-0.01, **kw)
        Text(parent=self.pause_badge, text="按 P 继续 · ESC 打开菜单",
             position=(0, -0.032), origin=(0, 0), scale=0.8,
             color=color.rgba(0.75, 0.88, 1.0, 0.85), z=-0.01, **kw)

        # 暂停面板
        self.pause_panel = Entity(parent=camera.ui, enabled=False, z=-0.45)
        self.pause_dim = Entity(parent=self.pause_panel, model="quad",
                                color=color.rgba(0.0, 0.0, 0.0, 0.0),
                                scale=(2, 2), z=0.02)
        self.pause_card = Entity(parent=self.pause_panel)
        card_h = 0.52 if net_mode else 0.60
        Entity(parent=self.pause_card, model="quad",
               color=color.rgba(0.30, 0.88, 1.0, 0.18),
               scale=(0.692, card_h + 0.016), z=0.004)
        Entity(parent=self.pause_card, model="quad",
               color=color.rgba(0.025, 0.05, 0.12, 0.98),
               scale=(0.68, card_h))
        top_y = card_h / 2 - 0.012
        Entity(parent=self.pause_card, model="quad",
               color=color.rgba(0.30, 0.88, 1.0, 0.85),
               position=(0, top_y), scale=(0.58, 0.004), z=-0.01)
        Text(parent=self.pause_card, text="游戏已暂停", position=(0, 0.185),
             origin=(0, 0), scale=2.3, color=color.rgb(0.45, 0.95, 1.0),
             z=-0.01, **kw)
        Text(parent=self.pause_card, text="P 继续 · ESC 关闭",
             position=(0, 0.125), origin=(0, 0), scale=0.9,
             color=color.rgba(0.75, 0.88, 1.0, 0.85), z=-0.01, **kw)
        Entity(parent=self.pause_card, model="quad",
               color=color.rgba(0.30, 0.85, 1.0, 0.30),
               position=(0, 0.095), scale=(0.50, 0.0025), z=-0.01)

        leave_title = "离开对战" if net_mode else "返回菜单"
        leave_info = ("离开后本局将判定为弃权" if net_mode
                      else "当前进度将不会保存")
        leave_confirm = "离开对战" if net_mode else "返回菜单"
        pause_items = [("继续游戏", (0.12, 0.52, 0.72, 1), self.resume)]
        if not net_mode:
            pause_items.append(("重新开始", (0.13, 0.24, 0.46, 1), self.reset))
        pause_items.append(("设置", (0.13, 0.32, 0.56, 1), self._open_settings_panel))
        pause_items.append(("返回菜单", (0.13, 0.24, 0.46, 1),
                            lambda: self._ask_dialog(
                                leave_title, leave_info,
                                lambda: self._exit(0), leave_confirm)))
        pause_items.append(("退出游戏", (0.46, 0.14, 0.14, 1),
                            lambda: self._ask_dialog(
                                "退出游戏", "确定要退出游戏吗？",
                                lambda: self._exit(EXIT_QUIT_GAME), "退出游戏")))
        y = 0.045
        self.pause_btns = []
        self._pause_sel = 0
        for text, col, cb in pause_items:
            btn = Button(parent=self.pause_card, text=text, position=(0, y),
                         scale=(0.28, 0.064), color=color.rgba(*col), z=-0.02,
                         on_click=cb)
            self.pause_btns.append((btn, col))
            y -= 0.074
        self._apply_menu_font(self.pause_panel)

        # 结算面板
        self.over_panel = Entity(parent=camera.ui, enabled=False, z=-0.50)
        self.over_dim = Entity(parent=self.over_panel, model="quad",
                               color=color.rgba(0.0, 0.0, 0.0, 0.0),
                               scale=(2, 2), z=0.02)
        self.over_card = Entity(parent=self.over_panel)
        if net_mode:
            over_h, title_y, info_y, div_y = 0.52, 0.185, 0.095, 0.045
        else:
            over_h, title_y, info_y, div_y = 0.60, 0.215, 0.115, 0.060
        self.over_border = Entity(parent=self.over_card, model="quad",
                                  color=color.rgba(1.0, 0.42, 0.36, 0.20),
                                  scale=(0.752, over_h + 0.016), z=0.004)
        Entity(parent=self.over_card, model="quad",
               color=color.rgba(0.025, 0.05, 0.12, 1.0),
               scale=(0.74, over_h))
        self.over_accent = Entity(parent=self.over_card, model="quad",
                                  color=color.rgba(1.0, 0.42, 0.36, 0.80),
                                  position=(0, over_h / 2 - 0.012),
                                  scale=(0.64, 0.004), z=-0.01)
        self.over_title = Text(parent=self.over_card, text="游戏结束",
                               position=(0, title_y), origin=(0, 0), scale=2.4,
                               color=color.rgb(1, 0.38, 0.32), z=-0.01, **kw)
        self.over_info = Text(parent=self.over_card, text="", position=(0, info_y),
                              origin=(0, 0), scale=1.0,
                              color=color.rgba(0.88, 0.94, 1.0, 0.92),
                              z=-0.01, **kw)
        Entity(parent=self.over_card, model="quad",
               color=color.rgba(1.0, 0.55, 0.40, 0.28),
               position=(0, div_y), scale=(0.54, 0.0025), z=-0.01)
        over_items = []
        if not net_mode:
            over_items.append(("重新开始", (0.13, 0.28, 0.50, 1), self.reset))
        over_items.append(("返回菜单", (0.13, 0.28, 0.50, 1),
                           lambda: self._ask_dialog(
                               leave_title, leave_info,
                               lambda: self._exit(0), leave_confirm)))
        over_items.append(("退出游戏", (0.46, 0.14, 0.14, 1),
                           lambda: self._ask_dialog(
                               "退出游戏", "确定要退出游戏吗？",
                               lambda: self._exit(EXIT_QUIT_GAME), "退出游戏")))
        y = 0.0
        self.over_btns = []
        self._over_sel = 0
        for text, col, cb in over_items:
            btn = Button(parent=self.over_card, text=text, position=(0, y),
                         scale=(0.28, 0.064), color=color.rgba(*col), z=-0.02,
                         on_click=cb)
            self.over_btns.append((btn, col))
            y -= 0.082
        Text(parent=self.over_card, text="ESC 返回菜单" if not net_mode
             else "ESC 离开对战", position=(0, y - 0.005), origin=(0, 0),
             scale=0.75, color=color.white, z=-0.01, **kw)
        self._apply_menu_font(self.over_panel)

        # 通用退出 / 返回菜单确认弹层
        self.dialog_panel = Entity(parent=camera.ui, enabled=False, z=-0.55)
        self.dialog_dim = Entity(parent=self.dialog_panel, model="quad",
                                 color=color.rgba(0.0, 0.0, 0.0, 0.0),
                                 scale=(2, 2), z=0.02)
        self.dialog_card = Entity(parent=self.dialog_panel)
        Entity(parent=self.dialog_card, model="quad",
               color=color.rgba(1.0, 0.45, 0.38, 0.22),
               scale=(0.582, 0.362), z=0.004)
        Entity(parent=self.dialog_card, model="quad",
               color=color.rgba(0.025, 0.05, 0.12, 1.0),
               scale=(0.57, 0.35))
        Entity(parent=self.dialog_card, model="quad",
               color=color.rgba(1.0, 0.48, 0.40, 0.70),
               position=(0, 0.163), scale=(0.50, 0.004), z=-0.01)
        self.dialog_title = Text(parent=self.dialog_card, text="退出游戏",
                                 position=(0, 0.09), origin=(0, 0), scale=1.8,
                                 color=color.rgb(1.0, 0.55, 0.45),
                                 z=-0.01, **kw)
        self.dialog_info = Text(parent=self.dialog_card, text="",
                                position=(0, 0.02), origin=(0, 0), scale=0.9,
                                color=color.rgba(0.9, 0.94, 1.0, 0.9),
                                z=-0.01, **kw)
        self.dialog_yes = Button(parent=self.dialog_card, text="退出游戏",
                                 position=(-0.12, -0.10), scale=(0.22, 0.06),
                                 color=color.rgba(0.46, 0.14, 0.14, 1),
                                 z=-0.02, on_click=self._confirm_dialog)
        Button(parent=self.dialog_card, text="取消", position=(0.12, -0.10),
               scale=(0.22, 0.06), color=color.rgba(0.13, 0.28, 0.50, 1),
               z=-0.02, on_click=self._close_dialog)
        self._dialog_action = None
        self._apply_menu_font(self.dialog_panel)

        # 设置面板（暂停菜单里打开）：鼠标转向灵敏度可调
        self.settings_panel = Entity(parent=camera.ui, enabled=False, z=-0.52)
        self.set_dim = Entity(parent=self.settings_panel, model="quad",
                              color=color.rgba(0.0, 0.0, 0.0, 0.0),
                              scale=(2, 2), z=0.02)
        self.set_card = Entity(parent=self.settings_panel)
        Entity(parent=self.set_card, model="quad",
               color=color.rgba(0.30, 0.88, 1.0, 0.22),
               scale=(0.612, 0.416), z=0.004)
        Entity(parent=self.set_card, model="quad",
               color=color.rgba(0.025, 0.05, 0.12, 1.0),
               scale=(0.60, 0.40))
        Entity(parent=self.set_card, model="quad",
               color=color.rgba(0.30, 0.88, 1.0, 0.85),
               position=(0, 0.172), scale=(0.52, 0.004), z=-0.01)
        Text(parent=self.set_card, text="设置", position=(0, 0.120), origin=(0, 0),
             scale=1.8, color=color.rgb(0.45, 0.95, 1.0), z=-0.01, **kw)
        Text(parent=self.set_card, text="鼠标转向灵敏度", position=(0, 0.058),
             origin=(0, 0), scale=0.9, color=color.rgba(0.85, 0.93, 1.0, 0.9),
             z=-0.01, **kw)
        self.sens_text = Text(parent=self.set_card, text="1.6", position=(0, 0.002),
                              origin=(0, 0), scale=1.4,
                              color=color.rgb(1.0, 0.86, 0.4), z=-0.01, **kw)
        Button(parent=self.set_card, text="-", position=(-0.115, 0.002),
               scale=(0.058, 0.052), color=color.rgba(0.13, 0.28, 0.50, 1),
               z=-0.02, on_click=lambda: self._change_sens(-SKY_SENS_STEP))
        Button(parent=self.set_card, text="+", position=(0.115, 0.002),
               scale=(0.058, 0.052), color=color.rgba(0.13, 0.28, 0.50, 1),
               z=-0.02, on_click=lambda: self._change_sens(SKY_SENS_STEP))
        self.invert_btn = Button(parent=self.set_card, text="上下反转：关",
                                 position=(0, -0.068), scale=(0.19, 0.050),
                                 color=color.rgba(0.13, 0.28, 0.50, 1),
                                 z=-0.02, on_click=self._toggle_invert)
        Button(parent=self.set_card, text="返回", position=(0, -0.152),
               scale=(0.22, 0.054), color=color.rgba(0.12, 0.52, 0.72, 1),
               z=-0.02, on_click=self._back_to_pause)
        self._apply_menu_font(self.settings_panel)

    def _open_settings_panel(self):
        """暂停菜单 → 设置页"""
        self.pause_panel.enabled = False
        self.settings_panel.enabled = True
        self._sync_sens_text()
        self._sync_invert_text()
        self._pop_menu(self.set_card, self.set_dim)

    def _back_to_pause(self):
        """设置页 → 回暂停菜单"""
        self.settings_panel.enabled = False
        self.pause_panel.enabled = True
        self._pop_menu(self.pause_card, self.pause_dim)

    def _sync_sens_text(self):
        cur = float(SETTINGS.get("sky_sens", MOUSE_SENS))
        self.mouse_sens = cur
        self.sens_text.text = f"{cur:.1f}"

    def _change_sens(self, delta):
        """± 一档灵敏度：即时生效并写入 settings.json"""
        cur = float(SETTINGS.get("sky_sens", MOUSE_SENS))
        cur = round(min(SKY_SENS_MAX, max(SKY_SENS_MIN, cur + delta)), 2)
        SETTINGS["sky_sens"] = cur
        self.mouse_sens = cur
        self.sens_text.text = f"{cur:.1f}"
        save_settings()

    def _sync_invert_text(self):
        self.invert_y = bool(int(SETTINGS.get("sky_invert_y", 0)))
        self.invert_btn.text = "上下反转：" + ("开" if self.invert_y else "关")

    def _toggle_invert(self):
        """上下反转开关：即时生效并写入 settings.json"""
        self.invert_y = not bool(self.invert_y)
        SETTINGS["sky_invert_y"] = 1 if self.invert_y else 0
        self._sync_invert_text()
        save_settings()

    def _ask_dialog(self, title, info, action, confirm_text):
        """打开退出 / 返回菜单确认弹层"""
        self.dialog_title.text = title
        self.dialog_info.text = info
        self.dialog_yes.text = confirm_text
        self._dialog_action = action
        # 弹层打开期间，把底下的暂停 / 结算面板整个禁用，避免底层按钮抢走鼠标射线
        self._dialog_parent = "pause" if self.pause_menu else ("over" if self.over else None)
        if self._dialog_parent == "pause":
            self.pause_panel.enabled = False
        elif self._dialog_parent == "over":
            self.over_panel.enabled = False
        self.dialog_panel.enabled = True
        self._pop_menu(self.dialog_card, self.dialog_dim)

    def _restore_dialog_parent(self):
        if getattr(self, "_dialog_parent", None) == "pause":
            self.pause_panel.enabled = True
        elif self._dialog_parent == "over":
            self.over_panel.enabled = True
        self._dialog_parent = None

    def _close_dialog(self):
        self.dialog_panel.enabled = False
        self._dialog_action = None
        self._restore_dialog_parent()

    def _confirm_dialog(self):
        action = self._dialog_action
        self.dialog_panel.enabled = False
        self._dialog_action = None
        self._restore_dialog_parent()
        if action is not None:
            action()

    def _apply_menu_font(self, root):
        if not self.font:
            return
        for e in root.children:
            if hasattr(e, "text_entity"):
                e.text_entity.font = self.font
            self._apply_menu_font(e)

    def _pop_menu(self, card, dim):
        """菜单出现时的淡入 + 缩放过渡"""
        dim.color = color.rgba(0.0, 0.0, 0.0, 0.0)
        dim.animate_color(color.rgba(0.0, 0.0, 0.0, 0.68), duration=0.16)
        card.scale = (0.94, 0.94, 1)
        card.animate_scale((1, 1, 1), duration=0.18, curve=curve.out_back)

    def _menu_highlight(self, btns, idx):
        """键盘选中项高亮：加亮底色 + 轻微放大（鼠标仍可直接点）"""
        if not btns:
            return 0
        idx %= len(btns)
        for i, (b, col) in enumerate(btns):
            if i == idx:
                b.color = color.rgba(min(1.0, col[0] + 0.35), min(1.0, col[1] + 0.35),
                                     min(1.0, col[2] + 0.35), col[3])
                b.scale = (0.31, 0.072)
            else:
                b.color = color.rgba(*col)
                b.scale = (0.28, 0.064)
        return idx

    def _menu_key(self, key, btns, idx):
        """方向键上下移动选中，回车/空格执行 —— 鼠标失灵时的键盘通路"""
        if not btns:
            return idx
        if key in ("down arrow", "tab"):
            idx = self._menu_highlight(btns, idx + 1)
        elif key == "up arrow":
            idx = self._menu_highlight(btns, idx - 1)
        elif key in ("enter", "return", "space"):
            cb = getattr(btns[idx][0], "on_click", None)
            if callable(cb):
                cb()
        return idx

    def _hide_menus(self):
        self.pause_panel.enabled = False
        self.over_panel.enabled = False
        self.dialog_panel.enabled = False
        settings_panel = getattr(self, "settings_panel", None)
        if settings_panel is not None:
            settings_panel.enabled = False
        self._dialog_action = None
        self._dialog_parent = None
        # 关菜单后恢复游戏内鼠标锁定（光标隐藏 + 居中，转向不受屏幕边缘限制）

    def _show_gameover(self):
        reason = {"voxel": "撞上了地面或山体", "bounds": "飞出了天空边界",
                  "meteor": "撞上了空中陨石"}.get(self.death_reason, "")
        self.over_info.text = (f"{reason}\n经验: {self.xp}   等级: {self.level}   最高: {self.best}"
                               f"\n光环 {self.rings_passed}   强化 {self.powerups_taken}")
        self._set_cursor_locked(False)
        self._set_hud_visible(False)
        self.over_accent.color = color.rgba(1.0, 0.42, 0.36, 0.80)
        self.over_panel.enabled = True
        self.pause_badge.enabled = False
        self._pop_menu(self.over_card, self.over_dim)
        self._over_sel = self._menu_highlight(getattr(self, "over_btns", []), 0)

    def _set_bgm_paused(self, paused):
        """暂停 / 恢复背景音乐（三个模式共用）。

        不能用 Audio.play() 来"恢复"：ursina 的 Audio.pause() 实现是
        「记下播放进度 → 把 pitch 置 0 → stop」，pitch=0 等于 0 倍速播放，
        声音仍在走但完全听不到 —— 这就是关掉暂停菜单后 BGM 消失的原因。
        Audio.resume() 会还原 pitch / 音量 / 声道并从断点续播。
        """
        if self.bgm is None or self.test_mode:
            return
        try:
            if paused:
                self.bgm.pause()
                return
            if getattr(self.bgm, "temp_time", None) is not None:
                self.bgm.resume()
                if getattr(self.bgm, "playing", 0):
                    return
            self.bgm.pitch = 1.0          # 兜底：clip 重载失败时也不留在 0 倍速
            self.bgm.play()
        except Exception:
            pass

    def _open_pause_menu(self):
        self.pause_menu = True
        self.paused = True
        self.pause_badge.enabled = False
        self._set_hud_visible(False)
        self.pause_panel.enabled = True
        self._pop_menu(self.pause_card, self.pause_dim)
        self._pause_sel = self._menu_highlight(getattr(self, "pause_btns", []), 0)
        self._set_cursor_locked(False)
        self._set_bgm_paused(True)

    def _update_hud_text(self):
        self.hud_lv.text = f"Lv {self.level}   经验 {self.xp}   最高 {self.best}"
        k = (self.xp % SKY_XP_PER_LEVEL) / SKY_XP_PER_LEVEL
        self.xp_bar.scale = (max(0.001, 0.36 * k), 0.014)
        self.xp_bar.position = (-0.62 + 0.18 * k, 0.395)

    def _update_energy_hud(self, t):
        """每帧刷新能量条与连击提示（文本仅在连击数变化时重建）"""
        e = self.energy / 100.0
        self.energy_bar.scale = (max(0.001, 0.36 * e), 0.009)
        self.energy_bar.position = (-0.62 + 0.18 * e, 0.368)
        self.energy_bar.color = (color.rgb(1.0, 0.66, 0.22) if self.dashing
                                 else color.rgb(0.35, 0.85, 1.0))
        if self.combo >= 2:
            if self._combo_shown != self.combo:
                self._combo_shown = self.combo
                self.combo_text.text = f"连击 x{self.combo}  经验+{25 * (self.combo - 1)}%"
            fade = (self.combo_t + COMBO_WINDOW - t) / COMBO_WINDOW
            a = 0.15 + 0.85 * max(0.0, min(1.0, fade))
            self.combo_text.color = color.rgba(1.0, 0.86, 0.4, a)
        else:
            if self.combo_text.text:        # 重新开始时也能清掉上一局的连击文本
                self._combo_shown = -1
                self.combo_text.text = ""

    def _update_buff_hud(self):
        """强化图标显隐 + 剩余时间条（只在 UI 层，无文本重建）"""
        for key, root, bar in self.buff_uis:
            rem = self.buffs[key]
            root.enabled = rem > 0.0
            if rem <= 0.0:
                continue
            frac = rem / BUFF_DUR[key]
            bar.scale = (max(0.004, 0.034 * frac), 0.005)
            bar.position = (-0.017 + 0.017 * frac, -0.020)

    def resume(self):
        self.paused = False
        self.pause_menu = False
        self.pause_badge.enabled = False
        self._set_hud_visible(True)
        self._hide_menus()
        self._set_cursor_locked(True)
        if not self.over:
            self._set_bgm_paused(False)

    def _exit(self, code):
        self._record_best()
        save_settings()
        rm = getattr(self, "raw_mouse", None)
        if rm is not None:
            rm.unlock()                 # 解除光标裁剪并还原系统鼠标加速
        os._exit(code)          # 子进程直接退出，跳过引擎清理

    # ---------- 输入 ----------

    def input(self, key):
        if key == "f11":                # 全屏/窗口切换（任何状态下可用）
            _set_fullscreen(not _is_fullscreen())
            return
        if self.dialog_panel.enabled:
            if key == "escape":
                self._close_dialog()
            elif key in ("enter", "return", "space"):
                self._confirm_dialog()      # 键盘也能确认，鼠标失灵时不至于卡死
            return
        if self.over:
            if key == "escape":
                if getattr(self, "conn", None) is not None:
                    self._ask_dialog("离开对战", "离开后本局将判定为弃权",
                                     lambda: self._exit(0), "离开对战")
                else:
                    self._ask_dialog("返回菜单", "当前进度将不会保存",
                                     lambda: self._exit(0), "返回菜单")
            else:
                self._over_sel = self._menu_key(key, getattr(self, "over_btns", []),
                                                self._over_sel)
            return
        if key == "p":
            if self.pause_menu:
                self.resume()
            else:
                self.paused = not self.paused
                self.pause_badge.enabled = self.paused
                # P 暂停同样要放开光标：旧版只切徽标，鼠标仍被隐藏钉在中心，
                # 玩家看着"已暂停"却找不到指针。继续游戏时再锁定回中。
                self._set_cursor_locked(not self.paused)
                self._set_bgm_paused(self.paused)
            return
        if key == "escape":
            sp = getattr(self, "settings_panel", None)
            if sp is not None and sp.enabled:      # 设置页 → 回暂停菜单
                self._back_to_pause()
            elif self.pause_menu:
                self.resume()
            else:
                self._open_pause_menu()
            return
        if self.paused or self.pause_menu:
            if self.pause_menu:      # 暂停菜单支持方向键 + 回车
                self._pause_sel = self._menu_key(key, getattr(self, "pause_btns", []),
                                                 self._pause_sel)
            return
        if key == "scroll up":
            self.cam_dist = max(9, self.cam_dist - 2.0)
        elif key == "scroll down":
            self.cam_dist = min(36, self.cam_dist + 2.0)

    def steer(self):
        """鼠标转向：采用主流 FPS/第三人称的开源标准做法。

        参考 GLFW 的 GLFW_CURSOR_DISABLED、SDL 的 SDL_SetRelativeMouseMode、
        Panda3D 官方 camera-control 示例 —— 三者都是「隐藏光标 + 每帧取物理
        位移增量 + 光标贴边时回中」。旧实现走 ursina 的 mouse.locked，本质是
        把指针的**绝对位置**当位移（指针越靠边转得越快，越过阈值就像被推着
        乱转），而回中用的 move_pointer 又带 1.124 魔数、DPI 缩放下落点偏，
        于是回中那一帧产生尖峰。这里换成本机 Win32 的 GetCursorPos /
        SetCursorPos：读写同一套桌面物理像素坐标，回中零误差，且已隐藏光标
        不会打扰玩家。

        转角按「弧度/屏幕宽」标定，因此换分辨率、缩放或全屏后手感一致。
        """
        if self.paused or self.pause_menu or self.over:
            return
        rm = getattr(self, "raw_mouse", None)
        if rm is None:
            return
        dx, dy, cw = rm.read()
        if not (dx or dy):
            return
        sens = float(getattr(self, "mouse_sens", MOUSE_SENS))
        k = sens / float(max(1, cw))                  # 弧度/像素（与分辨率、缩放、全屏无关）
        ysign = 1.0 if getattr(self, "invert_y", False) else -1.0
        # 鼠标右移 → 机头右转。
        # 符号由离屏实测确定（固定世界点的屏幕位移）：angle 增大时画面内容右移，
        # 也就是视线在左转；因此"右转"必须让 angle 减小。
        yaw = max(-MOUSE_MAX_STEP, min(MOUSE_MAX_STEP, dx * k))
        self.target_angle -= yaw
        # 鼠标上移 → 抬头（实测 pitch 增大 = 抬头）；invert_y 可在设置里反向
        pit = max(-MOUSE_MAX_STEP, min(MOUSE_MAX_STEP, ysign * dy * k))
        self.target_pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT,
                                self.target_pitch + pit))


# ---------------- 全屏控制 ----------------
# ursina 8.3 的 window.fullscreen 相关代码在源码中被注释，赋值不生效；
# 此处直接构造 WindowProperties 请求 Panda3D 窗口切换全屏。

def _set_fullscreen(on):
    on = bool(on)
    if _APP is None or TEST_MODE:
        return
    try:
        from panda3d.core import WindowProperties
        props = WindowProperties()
        props.setFullscreen(on)
        if on:                                  # 全屏需同时给出目标尺寸，否则沿用旧窗口大小
            fs = getattr(window, "fullscreen_size", None)
            if fs:
                props.setSize(int(fs[0]), int(fs[1]))
        _APP.win.requestProperties(props)
        if not on:                              # 退出全屏时恢复合理的窗口尺寸
            ws = getattr(window, "windowed_size", None)
            if ws:
                p2 = WindowProperties()
                p2.setSize(int(ws[0]), int(ws[1]))
                _APP.win.requestProperties(p2)
        window._fullscreen = on
    except Exception:
        pass


def _is_fullscreen():
    try:
        return bool(_APP.win.getProperties().getFullscreen())
    except Exception:
        return False


# ---------------- 入口 ----------------

def make_fps_display():
    """天空模式左下角 FPS 显示（仅 3D 天空系列使用）"""
    label = Text(text="FPS：--", position=window.bottom_left + Vec2(0.024, 0.022),
                 origin=(-0.5, -0.5), scale=0.95,
                 color=color.rgba(0.80, 0.98, 1.0, 0.95))
    state = {"acc": 0.0, "frames": 0}

    def _update():
        state["acc"] += ursina_time.dt
        state["frames"] += 1
        if state["acc"] >= 0.25:
            label.text = f"FPS：{state['frames'] / state['acc']:.0f}"
            state["acc"] = 0.0
            state["frames"] = 0

    return Entity(update=_update, eternal=True)


def main():
    loadPrcFileData("", "window-title 天空贪吃蛇 3D")
    # 多重采样抗锯齿（MSAA）：用环境变量 SKY3D_MSAA 调整采样数，0 关闭
    msaa = os.environ.get("SKY3D_MSAA", "4")
    if msaa != "0":
        loadPrcFileData("", "framebuffer-multisample 1")
        loadPrcFileData("", f"multisamples {msaa}")
    if TEST_MODE:
        loadPrcFileData("", "window-type offscreen")   # 离屏渲染，截图不受遮挡
    if os.environ.get("SKY3D_SMALL") == "1":
        size, full = (320, 240), False
    elif TEST_MODE:
        size, full = (900, 700), False
    else:
        size, full = None, True            # 全屏，分辨率取显示器原生
    app = Ursina(size=size, title="天空贪吃蛇 3D", vsync=True, fullscreen=full)
    global _APP
    _APP = app
    if full:
        _set_fullscreen(True)
    from pathlib import Path
    if getattr(sys, "frozen", False):
        application.asset_folder = Path(sys._MEIPASS)   # exe 内资源目录（须为 Path，ursina 用 .glob 检索）
    else:
        # 源码运行：以脚本目录为资源根，不再依赖启动时的 cwd
        application.asset_folder = Path(os.path.abspath(__file__)).parent
    window.exit_button.visible = False
    window.fps_counter.enabled = False
    window.entity_counter.enabled = False
    window.collider_counter.enabled = False
    camera.clip_plane_far = 3000        # 天空穹顶 R=900，需足够远平面
    if TEST_MODE:
        window.borderless = False

    game = Sky3DGame(test_mode=TEST_MODE)
    make_fps_display()

    def frame():
        game.steer()
        if TEST_MODE:
            _test_driver(game)

    # 用实体包装帧回调，保证与引擎循环绑定
    Entity(update=lambda: frame(), eternal=True)

    app.run()


def _test_driver(game):
    """自动化测试：出生点巡航 → 绕最大岛屿看植被 → 低俯角看天空；截图 + FPS"""
    t = game._anim_t
    if not hasattr(_test_driver, "phase"):
        _test_driver.phase = 0
        _test_driver.t0 = _time.perf_counter()
        _test_driver.frames = 0
    _test_driver.frames += 1
    from panda3d.core import Filename

    def grab(path):
        img = _APP.win.getScreenshot()   # 抓取上一帧离屏缓冲
        if img:
            img.write(Filename.fromOsSpecific(path))

    ph = _test_driver.phase
    if ph == 0:                     # 出生点附近圆周巡航
        game.target_angle = -t * 0.9          # 圆周巡航 + 俯冲爬升摆动
        game.target_pitch = 0.3 * math.sin(t * 0.9)
        if t > 2.5:
            grab(r"C:\Users\liukang\AppData\Local\Temp\sky3d_a.png")
            _test_driver.phase = 1
    elif ph == 1:                   # 传送到最大山丘旁，切线绕丘低空飞行
        big = max(game.land[4:], key=lambda s: s["r"])
        game._t_island = big
        game.x = float(big["cx"])
        game.y = float(big["cy"] - big["r"] - 300)
        game.h = 6.0
        game.pitch = game.target_pitch = 0.0
        game.angle = game.target_angle = math.atan2(
            big["cy"] - game.y, big["cx"] - game.x) + math.pi / 2
        game.trail.clear()
        for i in range(34):
            game.trail.append((game.x - math.cos(game.angle) * 3 * i,
                               game.y - math.sin(game.angle) * 3 * i, game.h))
        _test_driver.phase = 2
    elif ph == 2:                   # 绕丘切线（相机已跟随蛇头朝向）
        big = game._t_island
        game.target_angle = math.atan2(
            big["cy"] - game.y, big["cx"] - game.x) + math.pi / 2
        game.target_pitch = 0.0
        if t > 5.5:
            grab(r"C:\Users\liukang\AppData\Local\Temp\sky3d_b.png")
            _test_driver.phase = 3
    elif ph == 3:                   # 回中心，朝最近的陨石带爬升（验证空中障碍）
        game.x, game.y = WORLD / 2.0, WORLD / 2.0
        game.h = 5.0
        m0 = min(game.meteors, key=lambda m: (m["x"] - game.x) ** 2 +
                 (m["y"] - game.y) ** 2)
        hd = math.hypot(m0["x"] - game.x, m0["y"] - game.y)
        game._t_angle = math.atan2(m0["y"] - game.y, m0["x"] - game.x)
        game._t_pitch = math.atan2(m0["h"] - game.h, hd * SC)
        game.angle = game.target_angle = game._t_angle
        game.pitch = game.target_pitch = game._t_pitch
        game.trail.clear()
        for i in range(34):
            game.trail.append((game.x - math.cos(game.angle) * 3 * i,
                               game.y - math.sin(game.angle) * 3 * i, game.h))
        _test_driver.phase = 4
        held_keys["shift"] = 1          # 模拟按住 Shift 冲刺（验证能量条 / 视野扩张）
    elif ph == 4:
        game.target_angle = game._t_angle - (t - 5.5) * 0.06
        game.target_pitch = game._t_pitch
        if t > 7.6:
            grab(r"C:\Users\liukang\AppData\Local\Temp\sky3d_c.png")
            _test_driver.t0 = _time.perf_counter()     # 截图后开始干净 FPS 窗口
            _test_driver.frames = 0
            for k in game._sec:                         # 分段耗时统计对齐同一窗口
                game._sec[k] = 0.0
            game._prof_n = 0
            held_keys["shift"] = 0      # 松开冲刺，恢复常规视野
            _test_driver.phase = 5
    elif ph == 5:
        game.target_angle = game._t_angle - (t - 5.5) * 0.06
        game.target_pitch = game._t_pitch
        if t > 10.4:
            el = _time.perf_counter() - _test_driver.t0
            n = max(1, game._prof_n)
            sec_ms = " ".join(f"{k}={v / n * 1000:.1f}" for k, v in game._sec.items())
            print(f"[TEST] fps={_test_driver.frames / el:.1f} "
                  f"ms/frame: {sec_ms} cursor_locked={mouse.locked} "
                  f"cursor_visible={mouse.visible}", flush=True)
            # 光环穿越验证：从环后方沿法线直飞穿过
            ring = min((r for r in game.rings if r["active"]),
                       key=lambda r: (r["x"] - game.x) ** 2 + (r["y"] - game.y) ** 2,
                       default=None)
            if ring is not None:
                ang = math.atan2(ring["nz"], ring["nx"])
                sx = ring["x"] - ring["nx"] * 300
                sy = ring["y"] - ring["nz"] * 300
                if not (60 < sx < WORLD - 60 and 60 < sy < WORLD - 60):
                    ang = math.atan2(-ring["nz"], -ring["nx"])
                    sx = ring["x"] + ring["nx"] * 300
                    sy = ring["y"] + ring["nz"] * 300
                game.x, game.y = sx, sy
                game.h = ring["h"]
                game.ph = ring["h"]
                game.angle = game.target_angle = ang
                game.pitch = game.target_pitch = 0.0
                game.trail.clear()
                for i in range(34):
                    game.trail.append((game.x - math.cos(ang) * 3 * i,
                                       game.y - math.sin(ang) * 3 * i, game.h))
                game._t_ring = ring
                ring["last_d"] = game._ring_sd(ring, game.x, game.y, game.h)
                game._update_camera(1.0)            # 传送后镜头立即对齐
                game._t_ring_t0 = t
                print(f"[TEST] ring test start: r={RING_RADIUS:.1f} "
                      f"h={ring['h']:.1f}", flush=True)
                _test_driver.phase = 6
            else:
                _test_driver.phase = 7
    elif ph == 6:
        ring = game._t_ring
        if t - game._t_ring_t0 > 0.12 and not getattr(_test_driver, "shot_d", False):
            _test_driver.shot_d = True
            grab(r"C:\Users\liukang\AppData\Local\Temp\sky3d_d.png")
        if game.rings_passed > 0 or t - game._t_ring_t0 > 1.8:
            print(f"[TEST] ring passed={game.rings_passed} "
                  f"energy={game.energy:.0f} xp={game.xp}", flush=True)
            _test_driver.phase = 7
    elif ph == 7:                   # 强化道具拾取验证
        pu = next((p for p in game.powerup_pool if p["active"]), None)
        if pu is None:
            game._spawn_powerup()
            pu = next((p for p in game.powerup_pool if p["active"]), None)
        if pu is None:
            print("[TEST] no powerup available", flush=True)
            game._t_pu = {"kind": "none"}
            game._t_pu_t0 = t
            game._t_pu_taken = game.powerups_taken
            _test_driver.phase = 9
            return
        ang = math.atan2(pu["y"] - game.y, pu["x"] - game.x)
        game.x = pu["x"] - math.cos(ang) * 280
        game.y = pu["y"] - math.sin(ang) * 280
        game.h = pu["h"]
        game.ph = game.h
        game.angle = game.target_angle = ang
        game.pitch = game.target_pitch = 0.0
        game.trail.clear()
        for i in range(34):
            game.trail.append((game.x - math.cos(ang) * 3 * i,
                               game.y - math.sin(ang) * 3 * i, game.h))
        game._update_camera(1.0)            # 传送后镜头立即对齐
        game._t_pu = pu
        game._t_pu_t0 = t
        game._t_pu_taken = game.powerups_taken
        _test_driver.pick_t = None
        _test_driver.phase = 8
    elif ph == 8:
        if _test_driver.pick_t is None and game.powerups_taken > game._t_pu_taken:
            _test_driver.pick_t = t
            print(f"[TEST] powerup={game._t_pu['kind']} taken={game.powerups_taken}",
                  flush=True)
        if (_test_driver.pick_t is not None and t - _test_driver.pick_t > 0.35
                and not getattr(_test_driver, "shot_f", False)):
            _test_driver.shot_f = True
            grab(r"C:\Users\liukang\AppData\Local\Temp\sky3d_f.png")
        if (_test_driver.pick_t is not None and t - _test_driver.pick_t > 0.75) \
                or t - game._t_pu_t0 > 2.8:
            print(f"[TEST] buffs={ {k: round(v, 1) for k, v in game.buffs.items()} }",
                  flush=True)
            # 追加验证护盾与能量道具
            game._apply_powerup("shield", t)
            game.energy = 20.0
            game._apply_powerup("energy", t)
            _test_driver.phase = 9
            _test_driver.phase9_t = t
    elif ph == 9:
        if t - _test_driver.phase9_t > 0.4 and not getattr(_test_driver, "shot_g", False):
            _test_driver.shot_g = True
            grab(r"C:\Users\liukang\AppData\Local\Temp\sky3d_g.png")
        if t - _test_driver.phase9_t > 1.0:
            print(f"[TEST] final rings={game.rings_passed} "
                  f"powerups={game.powerups_taken} shield={game.buffs['shield']:.1f} "
                  f"energy={game.energy:.0f} over={game.over}", flush=True)
            # 地形碰撞验证：低空直冲最大山丘，检查死亡瞬间蛇头是否仍插进地表
            big = max(game.land[4:], key=lambda s: s["r"])
            setup = None
            for a0 in (0.0, math.pi * 0.5, math.pi, math.pi * 1.5):
                sx = big["cx"] + math.cos(a0) * (big["r"] + 140)
                sy = big["cy"] + math.sin(a0) * (big["r"] + 140)
                if not (60 < sx < WORLD - 60 and 60 < sy < WORLD - 60):
                    continue
                t0 = game._column_top(sx, sy)
                if t0 is not None and t0 < -2.0:        # 起点必须位于低地，保证能撞上山坡
                    setup = (sx, sy, math.atan2(big["cy"] - sy, big["cx"] - sx))
                    break
            if setup is None:
                print("[TEST] terrain collision skipped (no clear approach)", flush=True)
                os._exit(0)
            sx, sy, ang0 = setup
            game.x, game.y = sx, sy
            game.h = game.ph = 1.8
            game.angle = game.target_angle = ang0
            game.pitch = game.target_pitch = 0.0
            game.trail.clear()
            for i in range(34):
                game.trail.append((sx - math.cos(ang0) * 3 * i,
                                   sy - math.sin(ang0) * 3 * i, game.h))
            for k in game.buffs:                        # 清掉护盾，验证真实死亡判定
                game.buffs[k] = 0.0
            game.shield_bubble.visible = False
            game._update_buff_hud()
            game.invincible = False
            game._update_camera(1.0)
            _test_driver.phase10_t = t
            _test_driver.phase = 10
            print("[TEST] terrain collision test start", flush=True)
    elif ph == 10:
        if game.over and getattr(_test_driver, "death_t", None) is None:
            _test_driver.death_t = t
        if getattr(_test_driver, "death_t", None) is not None:
            if t - _test_driver.death_t > 0.45:     # 等结算面板绘制完成再截图
                top = game._column_top(game.x, game.y)
                clear = (game.h - top) if top is not None else HEAD_COL_R
                pen = game._terrain_penetration(game.x, game.y, game.h)
                body_pen = max((game._terrain_penetration(tx, ty, th)
                                for tx, ty, th in list(game.trail)[:12]), default=0.0)
                cam_top = game._column_top(camera.x / SC, camera.z / SC)
                ok = game.death_reason == "voxel" and pen <= 0.02
                print(f"[TEST] terrain collision over={game.over} "
                      f"reason={game.death_reason} head=({game.x:.0f},{game.y:.0f},"
                      f"{game.h:.2f}) cam=({camera.x:.1f},{camera.y:.1f},{camera.z:.1f}) "
                      f"cam_top={cam_top if cam_top is None else round(cam_top, 2)} "
                      f"clearance={clear:.2f} penetration={pen:.2f} "
                      f"body_penetration={body_pen:.2f} "
                      f"panel={game.over_panel.enabled} -> {'PASS' if ok else 'FAIL'}",
                      flush=True)
                grab(r"C:\Users\liukang\AppData\Local\Temp\sky3d_h.png")
                os._exit(0 if ok else 1)
        elif t - _test_driver.phase10_t > 2.6:
            print("[TEST] terrain collision timeout -> FAIL", flush=True)
            os._exit(1)


if __name__ == "__main__":
    main()
