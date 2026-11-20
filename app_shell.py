# -*- coding: utf-8 -*-
"""单窗口外壳（路线 B）：把 pygame 的整幅画面当纹理，显示在 ursina 窗口里。

main.py 一行都不用改：启动前把下面四个函数换成桥接实现

    pygame.display.flip      -> 上传整幅画面 + 渲染 ursina 一帧
    pygame.mouse.get_pos     -> 由 ursina 的指针位置换算（dummy 驱动没有真鼠标）
    main.create_display      -> dummy 驱动下按尺寸重建离屏画布
    main.toggle_fullscreen   -> 改由 Panda3D 切全屏（dummy 驱动切不了窗口）

于是原来的主循环、事件处理、页面状态机全部照旧，只是"出帧"从翻窗口变成
"上传纹理 + 跑一帧引擎"。3D 部分下一阶段再挂到同一个 app 上。

运行：.\\venv\\Scripts\\python.exe app_shell.py
自检：SHELL_SHOT=路径 SHELL_SHOT_FRAMES=40 时截图后自动退出
"""
import os
import sys
from pathlib import Path

# 必须早于 import pygame：dummy 视频驱动让 pygame 只画到内存表面，不建窗口
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import numpy as np  # noqa: E402
import pygame  # noqa: E402
from PIL import Image  # noqa: E402
from panda3d.core import Filename, WindowProperties  # noqa: E402
from ursina import (Ursina, Entity, Texture, camera, window, application, color,
                   destroy)  # noqa: E402

W, H = 960, 540

# ursina 的按键名 -> pygame 键码
KEYMAP = {
    "escape": pygame.K_ESCAPE, "return": pygame.K_RETURN, "enter": pygame.K_RETURN,
    "space": pygame.K_SPACE, "backspace": pygame.K_BACKSPACE, "tab": pygame.K_TAB,
    "delete": pygame.K_DELETE, "insert": pygame.K_INSERT,
    "up arrow": pygame.K_UP, "down arrow": pygame.K_DOWN,
    "left arrow": pygame.K_LEFT, "right arrow": pygame.K_RIGHT,
    "home": pygame.K_HOME, "end": pygame.K_END,
    "page up": pygame.K_PAGEUP, "page down": pygame.K_PAGEDOWN,
    "f11": pygame.K_F11,
}
BUTTONS = {"left": 1, "middle": 2, "right": 3}

# 自检用：每个页面实际参与点击判定的按钮列表
STATE_LISTS = {
    "menu": ("btns_main", "btn_more_main"),
    "menu_classic": ("btns_classic",),
    "menu_sky": ("btns_sky",),
    "menu_more": ("btns_more",),
    "settings": ("btns_settings",),
    "diff": ("btns_diff_art",),
    "versus_diff": ("btns_diff_art",),
    "sky3d_ai_diff": ("btns_diff_art",),
    "net_menu": ("btns_net_menu",),
    "net_host_cfg": ("btns_net_menu", "btns_room"),
    "net_join": ("btns_net_menu",),
}

FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "fonts", "msyh.ttc")


def _stack_frames():
    import inspect
    return [fi.frame.f_locals for fi in inspect.stack()]


def _find_game():
    """从调用栈里找 main.main() 的局部 game 对象"""
    for fl in _stack_frames():
        game = fl.get("game")
        if game is not None and hasattr(game, "btns_main"):
            return game
    return None


def _collect(game, names):
    """按名字取按钮列表，兼顾 game 属性与 main() 局部变量"""
    out = []
    frames = _stack_frames() if game is not None else []
    for name in names:
        val = getattr(game, name, None) if game is not None and hasattr(game, name) else None
        if val is None:
            for fl in frames:
                if name in fl:
                    val = fl[name]
                    break
        if val is None:
            continue
        items = list(val.values()) if isinstance(val, dict) else (
            [val] if hasattr(val, "rect") else list(val))
        out.append((name, [i for i in items if hasattr(i, "rect")]))
    return out


class Bridge:
    """pygame 离屏画布 <-> ursina 窗口"""

    def __init__(self):
        self.app = Ursina(size=(W, H), title="贪吃蛇大作战", borderless=False,
                          fullscreen=False, vsync=True, development_mode=False)
        window.color = color.black
        window.exit_button.visible = False
        window.fps_counter.enabled = False
        self.quad = Entity(parent=camera.ui, model="quad", scale=(1, 1), z=0.5)
        self.tex = None
        self.tsize = None
        self.mpos = (0, 0)
        self._last_mpos = (0, 0)
        self._keys = []
        self._inj = []          # 自检注入的键（与真实按键分开，3D 里只回放这些）
        self._buttons = []
        self._wheel = 0
        self._frames = 0
        self._full = False
        self._force_mouse = None
        # 让 main.py 里的 pygame 调用直接落到桥接层
        pygame.mouse.get_pos = self.get_pos
        pygame.display.flip = self.flip
        # 让窗口能自由拖动、缩放、最大化：补上标准窗口样式
        # （Panda3D 建出来的窗口默认没有 thick frame / maximize box）
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            h = self._own_hwnd(require_visible=False)
            if h:
                WS_THICKFRAME, WS_MAXIMIZEBOX, WS_MINIMIZEBOX = 0x00040000, 0x00010000, 0x00020000
                style = u.GetWindowLongW(h, -16)          # GWL_STYLE
                style |= WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX
                u.SetWindowLongW(h, -16, style)
                # 重新计算非客户区，让新边框立刻生效
                rect = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(rect))
                u.SetWindowPos(wintypes.HWND(h), None, rect.left, rect.top,
                               rect.right - rect.left, rect.bottom - rect.top,
                               0x0001 | 0x0002 | 0x0020)   # NOSIZE|NOMOVE|FRAMECHANGED
        except Exception as exc:
            print("[SHELL] window style setup failed %r" % (exc,), flush=True)

    # ---------- 显示 ----------
    def create_display(self, size, caption=None):
        """main.create_display 的替身：只建离屏表面，不碰真实窗口"""
        return pygame.display.set_mode((int(size[0]), int(size[1])), 0)

    def get_pos(self):
        return self.mpos

    def _own_hwnd(self, require_visible=True):
        """按本进程 PID + 客户区够大，找我们自己的窗口句柄（带缓存）

        require_visible=False 时连还没映射出来的窗口也算，用来在窗口
        第一次显示之前就把位置摆好。
        """
        cached = getattr(self, "_hwnd_cache", None)
        if cached:
            try:
                import ctypes
                import time as _t
                if (ctypes.windll.user32.IsWindow(cached)
                        and _t.time() - getattr(self, "_hwnd_t", 0) < 0.4):
                    return cached
            except Exception:
                pass
            self._hwnd_cache = None
        try:
            import ctypes
            import time as _t
            from ctypes import wintypes
            u = ctypes.windll.user32
            pid = os.getpid()
            # 前台窗口属于自己就是它，最省事也最准（全屏/resize 时 Panda 会
            # 新建 HWND 再销毁旧的，只按"尺寸接近 960x540"挑会挑到废弃的小窗）
            try:
                fg = u.GetForegroundWindow()
                if fg:
                    p = wintypes.DWORD()
                    u.GetWindowThreadProcessId(fg, ctypes.byref(p))
                    if p.value == pid:
                        r = wintypes.RECT()
                        u.GetClientRect(fg, ctypes.byref(r))
                        if r.right - r.left >= 320 and r.bottom - r.top >= 240:
                            self._hwnd_cache = fg
                            self._hwnd_t = _t.time()
                            return fg
            except Exception:
                pass
            found = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def cb(hwnd, _):
                p = wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
                if p.value != pid:
                    return True
                vis = bool(u.IsWindowVisible(hwnd))
                if require_visible and not vis:
                    return True
                r = wintypes.RECT()
                u.GetClientRect(hwnd, ctypes.byref(r))
                cw, ch = r.right - r.left, r.bottom - r.top
                if cw >= 320 and ch >= 240:
                    # 可见的优先；同样可见时挑尺寸最接近 960x540 的
                    found.append((0 if vis else 1,
                                  abs(cw - W) + abs(ch - H), hwnd))
                return True

            u.EnumWindows(cb, 0)
            found.sort(key=lambda t: (t[0], t[1]))
            self._hwnd_cache = found[0][2] if found else None
            self._hwnd_t = _t.time()
            return self._hwnd_cache
        except Exception:
            return None

    def _client_box(self, hwnd):
        """客户区在屏幕上的 (ox, oy, w, h)，物理像素"""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            r = wintypes.RECT()
            if not u.GetClientRect(hwnd, ctypes.byref(r)):
                return None
            pt = wintypes.POINT(0, 0)
            if not u.ClientToScreen(hwnd, ctypes.byref(pt)):
                return None
            return (pt.x, pt.y, r.right - r.left, r.bottom - r.top)
        except Exception:
            return None

    def _cursor_client(self, hwnd):
        """系统光标在客户区内的坐标；光标不在窗口里时返回 None"""
        box = self._client_box(hwnd)
        if box is None:
            return None
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            pt = wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            ox, oy, w, h = box
            cx, cy = pt.x - ox, pt.y - oy
            if 0 <= cx < w and 0 <= cy < h:
                return (cx, cy)
            return None
        except Exception:
            return None

    def _cursor_on_window(self, hwnd):
        """光标此刻是不是真的压在游戏窗口上（被别的窗口盖住就不算）"""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            pt = wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            hit = u.WindowFromPoint(pt)
            if not hit:
                return False
            root = u.GetAncestor(hit, 2)          # GA_ROOT
            return int(root or hit) == int(hwnd)
        except Exception:
            return None

    def ui_from_cursor(self, hwnd):
        """系统光标换算成 ursina UI 坐标（和 mouse.x / mouse.y 同一套空间）

        ux = (cx/w - 0.5) * aspect, uy = -(cy/h - 0.5)，与 ursina 的
        Mouse.x / Mouse.y 公式一致，用来做不依赖 hover 的命中判定。
        """
        box = self._client_box(hwnd)
        pos = self._cursor_client(hwnd)
        if box is None or pos is None:
            return None
        ox, oy, w, h = box
        if w <= 0 or h <= 0:
            return None
        return ((pos[0] / float(w) - 0.5) * (w / float(h)),
                -(pos[1] / float(h) - 0.5))

    def _menu_candidates(self, game):
        """当前可见菜单里的按钮（弹层优先，其次暂停，其次结算）。"""
        try:
            if getattr(game, "dialog_panel", None) is not None \
                    and game.dialog_panel.enabled:
                return [b for b in getattr(game.dialog_card, "children", [])
                        if callable(getattr(b, "on_click", None)) and b.enabled]
            if getattr(game, "pause_menu", False):
                return [b for b, _c in getattr(game, "pause_btns", [])]
            if getattr(game, "over", False):
                return [b for b, _c in getattr(game, "over_btns", [])]
        except Exception:
            pass
        return []

    def _all_menu_buttons(self, game):
        out = []
        try:
            out.extend(b for b, _c in getattr(game, "pause_btns", []))
            out.extend(b for b, _c in getattr(game, "over_btns", []))
            card = getattr(game, "dialog_card", None)
            if card is not None:
                out.extend(b for b in getattr(card, "children", [])
                           if callable(getattr(b, "on_click", None)))
        except Exception:
            pass
        return out

    def _lbutton_down(self):
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        except Exception:
            return False

    def _pointer_trusted(self, hwnd, ux, uy):
        """ursina 自己的指针数据是否可信（窗口失焦时它会恒为 0,0）"""
        try:
            from ursina import mouse as _um
            win = application.base.win
            if not win.get_pointer(0).in_window:
                return False
            if abs(_um.x - ux) > 0.05 or abs(_um.y - uy) > 0.06:
                return False
        except Exception:
            return False
        return True

    def _click_at(self, game, ux_uy):
        ux, uy = ux_uy
        for b in self._menu_candidates(game):
            try:
                if not b.enabled:
                    continue
                wp = b.world_position
                ws = b.world_scale
                if (abs(ux - wp.x) <= max(0.012, ws.x * 0.5)
                        and abs(uy - wp.y) <= max(0.012, ws.y * 0.5)):
                    print("[CLICK] physical hit -> %s"
                          % (getattr(b, "text", "?"),), flush=True)
                    b.on_click()
                    return True
            except Exception:
                continue
        return False

    def install_mouse_override(self):
        """让 ursina 的 mouse.x / mouse.y 可以被真实光标坐标覆盖。

        ursina 的 Mouse.x 直接读 base.win.get_pointer(0)：窗口一失焦就
        in_window=False、坐标返回 0，结果所有 UI 按钮的 hover 都落在屏幕
        正中 —— 用户点「返回菜单」却触发了中心的「重新开始」。这里给
        Mouse 类的 x/y 属性加一层"可强制"的 getter，菜单期间由外壳直接
        灌入 Win32 光标换算出来的坐标。
        """
        if getattr(self, "_mo_done", False):
            return
        try:
            from ursina import mouse as um
            cls = type(um)
            ox, oy = cls.x, cls.y

            def gx(self):
                f = getattr(self, "_shell_forced_xy", None)
                if f is not None:
                    return f[0]
                return ox.fget(self)

            def gy(self):
                f = getattr(self, "_shell_forced_xy", None)
                if f is not None:
                    return f[1]
                return oy.fget(self)

            def sx(self, v):
                self._shell_forced_xy = None
                ox.fset(self, v)

            def sy(self, v):
                self._shell_forced_xy = None
                oy.fset(self, v)

            cls.x = property(gx, sx)
            cls.y = property(gy, sy)
            um._shell_forced_xy = None
            self._umouse = um
            self._mo_done = True
            print("[SHELL] mouse override installed", flush=True)
        except Exception as exc:
            print("[SHELL] mouse override failed %r" % (exc,), flush=True)

    def poll_menu_click(self, game):
        """3D 菜单的点击兜底（每帧调用）。

        问题：ursina 的 Button 只有 hovered 为 True 才认点击，而 hovered
        依赖 Panda 的指针数据。窗口不是"真"前台时 pointer.in_window=False，
        mouse 恒为 (0,0)，hover 落在屏幕正中的按钮上 —— 用户点「返回菜单」
        实际触发的是「重新开始」，表现就是点不动、画面闪一下。

        处理分两种情况：
          * 指针数据可用：把真实光标坐标强制灌进 ursina，hover 自然正确；
          * 指针数据不可用：关掉按钮的 collide 并清掉残留 hover（避免误触
            中心按钮），再用 GetAsyncKeyState 抓左键按下沿，按真实光标位置
            物理命中按钮回调。
        """
        um = getattr(self, "_umouse", None) or self._umouse_fallback()
        hwnd = self._own_hwnd()
        cands = self._menu_candidates(game)
        if um is not None:
            um._shell_forced_xy = None
        if not hwnd:
            return False
        ux_uy = self.ui_from_cursor(hwnd)
        trusted = True
        if cands:
            trusted = bool(ux_uy) and self._pointer_trusted(hwnd, ux_uy[0], ux_uy[1])
        has_mouse = False
        try:
            has_mouse = bool(application.base.mouseWatcherNode.has_mouse())
        except Exception:
            pass

        if cands and not trusted and ux_uy and um is not None:
            # 强制坐标：让 ursina 的 hover / 命中判定跟真实光标一致
            um._shell_forced_xy = ux_uy
        for b in self._all_menu_buttons(game):
            try:
                b.collide = True if (not cands or trusted) else False
                if cands and not trusted and not has_mouse:
                    b.hovered = False      # 清掉失焦期间残留的 hover
            except Exception:
                pass

        lb = self._lbutton_down()
        prev = getattr(self, "_last_lb", False)
        self._last_lb = lb
        if not cands or trusted:
            return False
        if not (lb and not prev) or not ux_uy:
            return False
        return self._click_at(game, ux_uy)

    def _umouse_fallback(self):
        try:
            from ursina import mouse as um
            self._umouse = um
            return um
        except Exception:
            return None

    def _refresh_pointer(self, hwnd):
        """窗口已在前台时，用真实光标位置刷新一次 Panda 的指针缓存。

        Panda 的 win.get_pointer() 只在收到鼠标事件时更新。窗口从后台被
        点活的那一瞬间，pointer 还停在 in_window=False（ursina 的
        mouse.x/mouse.y 因此恒等于 0），菜单里所有按钮的 hover 都变成
        False，点哪个位置都落空 —— 这就是"点返回菜单/退出游戏没反应"。
        move_pointer 到光标当前位置会补发一次指针数据，读取立刻正确。
        """
        pos = self._cursor_client(hwnd)
        if pos is None:
            return False
        try:
            application.base.win.move_pointer(0, int(pos[0]), int(pos[1]))
            return True
        except Exception:
            return False

    def keep_menu_focus(self, hwnd):
        """菜单打开期间保证"鼠标在窗口里就有焦点、指针就是真实的"。

        只在系统光标确实落在窗口客户区内时才抢前台，用户在别的窗口打字
        不会被打断。返回当前是否前台。
        """
        if not hwnd:
            return False
        try:
            import ctypes
            import time as _tm
            u = ctypes.windll.user32
            fg = u.GetForegroundWindow()
            if fg != hwnd:
                # 只有当光标真的压在游戏窗口上（没被别的窗口盖住）才抢前台，
                # 用户在别的程序里打字不会被打断。
                on = self._cursor_on_window(hwnd)
                if os.environ.get("SHELL_DEBUG"):
                    print("[FOCUS] hwnd=%s fg=%s on=%s" % (hwnd, fg, on), flush=True)
                if not on:
                    return False
                self._grab_focus(silent=True)
                fg = u.GetForegroundWindow()
                if fg == hwnd:
                    self._fg_since = _tm.time()
            if fg == hwnd:
                # 只在"刚变前台"那一刻补刷一次指针（此时 Panda 的指针数据
                # 还是后台时的 (0,0)）。之后不再反复碰系统光标，否则光标
                # 会被我们的 move_pointer 拽着抖，画面也跟着闪。
                if getattr(self, "_last_fg", None) != hwnd:
                    self._fg_since = _tm.time()
                    self._refresh_pointer(hwnd)
                self._last_fg = fg
                return True
            self._last_fg = fg
            return False
        except Exception:
            return False

    def _grab_focus(self, silent=False):
        """把本窗口抢到前台并拿到键盘/鼠标焦点。

        Panda 起窗时自带的 SetForegroundWindow 经常失败（日志里的
        `:display:windisplay(warning): SetForegroundWindow() failed!`），窗口
        停在"可见但没焦点"的状态：Panda 收不到任何按键和鼠标移动 —— 表现就是
        Shift 冲刺无效、3D 里的暂停/结算按钮点不动（返回菜单点不出去）。

        Windows 的前台锁只允许"当前前台进程"改前台窗口，这里用标准的
        AttachThreadInput 把自己的输入队列临时挂到前台线程上，借用它的
        前台权限，设完立刻解挂。
        """
        hwnd = self._own_hwnd()
        if not hwnd:
            if not silent:
                print("[SHELL] focus: window not found", flush=True)
            return
        try:
            import ctypes
            import time as _tm
            from ctypes import wintypes
            # 冷却：反复抢前台会让窗口不停重绘（画面闪），1 秒最多一次
            now = _tm.time()
            if now - getattr(self, "_last_grab_t", 0.0) < 1.0:
                return
            self._last_grab_t = now
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            tid = k.GetCurrentThreadId()
            fg = u.GetForegroundWindow()
            fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = False
            if fg_tid and fg_tid != tid:
                attached = bool(u.AttachThreadInput(tid, fg_tid, True))
            if not u.IsWindowVisible(hwnd):
                u.ShowWindow(hwnd, 5)      # SW_SHOW：被 hide 掉的窗口要先显示
            if u.IsIconic(hwnd):
                u.ShowWindow(hwnd, 9)      # SW_RESTORE
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            u.SetFocus(hwnd)
            if attached:
                u.AttachThreadInput(tid, fg_tid, False)
            ok = u.GetForegroundWindow() == hwnd
            if not silent:
                print("[SHELL] focus hwnd=%s attached=%s ok=%s"
                      % (int(hwnd), attached, ok), flush=True)
        except Exception as exc:
            if not silent:
                print("[SHELL] focus failed %r" % (exc,), flush=True)

    def _scene_baseline(self):
        """记下"还没进 3D 之前"场景里有哪些实体。

        第二次进 3D 时必须把上一次留下的东西全部清掉：天空穹顶、地形、
        光照这些都是以 parent=None 建的（挂在场景根上），不在 game 的子树
        里，destroy(game) 碰不到。残留一个 900 半径的双面穹顶 + 第二盏平行
        光，画面就会变成半透明、发灰的"剪纸"效果。
        """
        from ursina import scene
        b = getattr(self, "_scene_base", None)
        if b is None:
            b = set(id(e) for e in scene.entities)
            self._scene_base = b
        return b

    def _clear_3d_leftovers(self):
        """把 3D 期间新加进场景的实体全部销毁（保留基线里的窗口/UI）

        ursina 的 destroy 对 eternal=True 的实体直接 return（destroy.py 第 27
        行），而 3D 场景里的地形/山体/植被/云/影子/天空更新器全是 eternal，
        所以普通 destroy 清不掉——必须 force_destroy=True。另外 ursina 只是
        把它们标记进 _entities_marked_for_removal 等下一帧才算数，这里顺手
        同步一下，保证"进 3D 之前场景一定是干净的"。
        """
        try:
            from ursina import scene, destroy as _destroy
        except Exception:
            return
        base = self._scene_baseline()
        removed = 0
        skipped = 0
        for e in list(scene.entities):
            if id(e) in base:
                continue
            try:
                _destroy(e, force_destroy=True)
                removed += 1
            except Exception:
                skipped += 1
        # 立即把标记删除的从 entities 里摘掉（不等下一帧）
        marked = getattr(scene, "_entities_marked_for_removal", None)
        if marked is not None:
            for e in list(marked):
                try:
                    scene.entities.remove(e)
                except ValueError:
                    pass
            try:
                marked.clear()
            except Exception:
                pass
        if removed or skipped:
            print("[SHELL] cleared %d leftover 3D entities (fail=%d)" % (removed, skipped),
                  flush=True)

    def _center_window(self, hwnd):
        """把窗口摆到屏幕正中（只在启动的头几帧做一次）

        之后窗口位置完全交给用户：拖动、缩放、最大化、全屏都不再干预。
        """
        if not hwnd:
            return False
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
            wr = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(wr))
            ww, wh = wr.right - wr.left, wr.bottom - wr.top
            x = max(0, (sw - ww) // 2)
            y = max(0, (sh - wh) // 2)
            if abs(wr.left - x) <= 2 and abs(wr.top - y) <= 2:
                return True                  # 已经在正中间
            # 不改变 Z 序（None），不激活，尺寸保持原样
            u.SetWindowPos(wintypes.HWND(hwnd), None, int(x), int(y), ww, wh,
                           0x0004 | 0x0010 | 0x0040)
            # 同步 Panda3D 内部 origin，免得它下一次 requestProperties 又挪回去
            try:
                application.base.win.set_origin(int(x), int(y))
            except Exception:
                try:
                    application.base.win.setOrigin(int(x), int(y))
                except Exception:
                    pass
            rr = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(rr))
            print("[SHELL] centered at %s,%s (%dx%d screen %dx%d) now=%s"
                  % (x, y, ww, wh, sw, sh,
                     (rr.left, rr.top, rr.right, rr.bottom)), flush=True)
            return True
        except Exception as exc:
            print("[SHELL] center failed %r" % (exc,), flush=True)
            return False

    def flip(self):
        surf = pygame.display.get_surface()
        if surf is not None:
            self._overlay_hotzones(surf)
            self._upload(surf)
        application.base.taskMgr.step()      # 渲染一帧，并派发窗口事件 -> input()
        self._drain()
        self._sync_mouse()
        self._frames += 1
        # 窗口尺寸变了（手动拖边缘 / 最大化 / F11 全屏）就重新算一次画面比例，
        # 让 960x540 的画布始终等比铺满、两边留黑边而不是被拉变形
        try:
            wsize = (int(window.size[0]), int(window.size[1]))
        except Exception:
            wsize = None
        if wsize and wsize != getattr(self, "_wsize", None):
            self._wsize = wsize
            self._layout()
        # 启动头两秒：窗口尺寸一稳定就摆到屏幕正中（Panda3D 是先建 1920x1080
        # 大窗再缩到 960x540，左上角不跟着动，所以会偏到左上角）。
        # 只在这段时间干预；之后窗口位置完全归用户，拖到哪就是哪。
        if self._frames <= 90 and wsize != getattr(self, "_centered_size", None):
            if self._center_window(self._own_hwnd(require_visible=False)):
                self._centered_size = wsize
        if os.environ.get("SHELL_PERF"):
            import time
            now = time.perf_counter()
            t0 = getattr(self, "_tperf0", None)
            if t0 is None:
                self._tperf0, self._nperf = now, 0
            self._nperf = getattr(self, "_nperf", 0) + 1
            if self._nperf % 60 == 0:
                print("[SHELL] per-frame %.1f ms (%dx%d)" % (
                    (now - self._tperf0) / 60 * 1000,
                    (self.tsize or (0, 0))[0], (self.tsize or (0, 0))[1]), flush=True)
                self._tperf0 = now
        self._maybe_click()
        self._maybe_key()
        self._maybe_shot()
        if self._frames in (3, 20, 40, 80, 120):
            self._grab_focus()
        # 2D 菜单每秒抽查几次：光标在窗口里却丢了前台时补回来并刷新指针，
        # 否则点击会用上一帧（甚至 (0,0)）的坐标判定，按钮点了没反应。
        # 注意这里不再摆窗口：位置是用户自己拖的，不能被程序抢回去
        if self._frames % 4 == 0:
            hwnd = self._own_hwnd()
            if hwnd:
                self.keep_menu_focus(hwnd)

    def _maybe_click(self):
        """自检用：SHELL_CLICK="x,y" 在第 N 帧注入一次点击（画布坐标）

        先把指针挪过去，过几帧再按下：Button.is_clicked() 依赖绘制阶段刷新的
        hover 状态，同一帧"移动 + 按下"时 hover 还没更新，点不中。
        """
        spec = os.environ.get("SHELL_CLICK")
        if not spec:
            return
        for seg in spec.split(";"):
            seg = seg.strip()
            if not seg:
                continue
            try:
                xy, _, fr = seg.partition("@")
                x, y = [int(v) for v in xy.split(",")]
                at = int(fr) if fr else 30
            except ValueError:
                print("[SHELL] bad SHELL_CLICK segment %r" % seg, flush=True)
                continue
            if self._frames == max(1, at - 4):
                self._force_mouse = (x, y)
                self.mpos = (x, y)
                self._last_mpos = (x, y)
            elif self._frames == at:
                if os.environ.get("SHELL_DEBUG"):
                    self._debug_buttons()
                self._buttons.append((1, True))
                print("[SHELL] click at %s" % (self.mpos,), flush=True)

    def _overlay_hotzones(self, surf):
        """自检用：把当前页面按钮的点击热区画到画面上（SHELL_HOTZONES=1）"""
        if not os.environ.get("SHELL_HOTZONES"):
            return
        game = _find_game()
        state = getattr(game, "state", None) if game is not None else None
        spec = os.environ["SHELL_HOTZONES"]
        names = tuple(spec.split(",")) if spec not in ("1", "all") else STATE_LISTS.get(state, ())
        groups = _collect(game, names) if names else []
        k = surf.get_width() / float(W)
        font = pygame.font.Font(FONT_PATH, max(12, int(14 * k))) if os.path.exists(FONT_PATH) else None
        print("[SHELL] hotzones state=%s k=%.3f" % (state, k), flush=True)
        for gi, (name, btns) in enumerate(groups):
            tint = [(255, 60, 60), (60, 200, 255), (255, 210, 60), (120, 255, 120)][gi % 4]
            for bi, btn in enumerate(btns):
                r = pygame.Rect(int(btn.rect[0] * k), int(btn.rect[1] * k),
                                int(btn.rect[2] * k), int(btn.rect[3] * k))
                fill = pygame.Surface(r.size, pygame.SRCALPHA)
                fill.fill((tint[0], tint[1], tint[2], 46))
                surf.blit(fill, r.topleft)
                pygame.draw.rect(surf, tint, r, max(1, int(2 * k)))
                label = "%s%d %s" % (name, bi + 1, getattr(btn, "text", ""))
                if font:
                    tag = font.render(label, True, (255, 255, 255))
                    bg = pygame.Surface((tag.get_width() + 4, tag.get_height() + 2),
                                        pygame.SRCALPHA)
                    bg.fill((0, 0, 0, 190))
                    surf.blit(bg, (r.x, max(0, r.y - tag.get_height() - 2)))
                    surf.blit(tag, (r.x + 2, max(0, r.y - tag.get_height() - 2)))
                print("[SHELL]   %s[%d] %r rect=%s center=%s" % (
                    name, bi + 1, getattr(btn, "text", ""), tuple(r), r.center), flush=True)

    def _debug_buttons(self):
        game = _find_game()
        if game is None:
            print("[SHELL] game not found", flush=True)
            return
        print("[SHELL] state=%s mouse=%s surface=%s" % (
            game.state, pygame.mouse.get_pos(),
            pygame.display.get_surface().get_size() if pygame.display.get_surface() else None),
            flush=True)
        for name, btns in _collect(game, tuple(STATE_LISTS.get(game.state, ()))):
            print("[SHELL] %s: %s" % (name, [
                (getattr(b, "text", ""), tuple(b.rect), getattr(b, "hover", None))
                for b in btns]), flush=True)

    def _maybe_key(self):
        """自检用：SHELL_KEY="up arrow@40;escape@60" 在指定帧注入按键"""
        for seg in (os.environ.get("SHELL_KEY") or "").split(";"):
            seg = seg.strip()
            if not seg:
                continue
            key, _, fr = seg.rpartition("@")
            if not key:
                key, fr = seg, "40"
            if self._frames == int(fr or 40):
                self._keys.append(key.strip())
                self._inj.append(key.strip())
                print("[SHELL] key %r" % key.strip(), flush=True)

    def _upload(self, surf):
        w, h = surf.get_size()
        if self.tex is None or (w, h) != self.tsize:
            img = Image.frombytes("RGBA", (w, h), pygame.image.tostring(surf, "RGBA"))
            self.tex = Texture(img)
            self.quad.texture = self.tex
            self.tsize = (w, h)
            self._layout()
            return
        # 同尺寸：直接把像素推回已有纹理（避免每帧新建 Texture 泄漏显存）
        data = pygame.image.tostring(surf, "RGBA", False)
        arr = np.frombuffer(data, np.uint8).reshape(h, w, 4)[::-1]
        self.tex._texture.setRamImageAs(arr.tobytes(), "RGBA")

    def _layout(self):
        """等比铺满窗口，多余的一边留黑边（letterbox）"""
        if self.tsize:
            ta = self.tsize[0] / float(self.tsize[1])
            wa = window.aspect_ratio
            self.quad.scale = (ta, 1) if wa >= ta else (wa, wa / ta)

    def _region(self):
        """画布在窗口里的像素矩形 (ox, oy, dw, dh)"""
        ww, wh = window.size
        ta = self.tsize[0] / float(self.tsize[1]) if self.tsize else W / float(H)
        if ww / float(wh) >= ta:
            dw, dh = ta * wh, wh
        else:
            dw, dh = ww, ww / ta
        return (ww - dw) / 2.0, (wh - dh) / 2.0, dw, dh

    def _sync_mouse(self):
        if self._force_mouse is not None:
            self.mpos = self._force_mouse
            return
        px = py = None
        try:
            p = application.base.win.get_pointer(0)
            if p.in_window:
                px, py = p.get_x(), p.get_y()
        except Exception:
            px = None
        if px is None:
            # 窗口在后台时 Panda 不更新指针（in_window=False），2D 层拿到的
            # 还是上一帧的位置 —— 点哪里都可能落到别的按钮上。这里直接用
            # 系统光标坐标兜底，保证"看到哪里就点中哪里"。
            hwnd = self._own_hwnd()
            pos = self._cursor_client(hwnd) if hwnd else None
            if pos is None:
                return
            px, py = pos
        ox, oy, dw, dh = self._region()
        tw, th = self.tsize or (W, H)
        x = int((px - ox) / dw * tw) if dw else 0
        y = int((py - oy) / dh * th) if dh else 0
        self.mpos = (max(0, min(tw - 1, x)), max(0, min(th - 1, y)))

    def toggle_fullscreen(self):
        self._full = not self._full
        try:
            props = WindowProperties()
            props.setFullscreen(self._full)
            if self._full:
                props.setSize(int(application.base.win.get_x_size()),
                              int(application.base.win.get_y_size()))
            application.base.win.requestProperties(props)
        except Exception:
            pass
        self._layout()

    # ---------- 输入 ----------
    def on_key(self, key):
        if os.environ.get("SHELL_3D_DBG"):
            print("[KEY] %r" % (key,), flush=True)
        if key in ("scroll up", "scroll down"):
            self._wheel += 1 if key == "scroll up" else -1
            return
        if key.endswith(" mouse down") or key.endswith(" mouse up"):
            btn = BUTTONS.get(key.split()[0])
            if btn:
                self._buttons.append((btn, key.endswith(" down")))
            return
        self._keys.append(key)

    def _map_key(self, key):
        code = KEYMAP.get(key)
        ch = ""
        if code is None:
            if len(key) == 1:
                ch = key
                code = getattr(pygame, "K_" + key, 0) or ord(key)
            else:
                return None, ""
        elif len(key) == 1:
            ch = key
        return code, ch

    def _drain(self):
        pos = self.mpos
        if pos != self._last_mpos:
            rel = (pos[0] - self._last_mpos[0], pos[1] - self._last_mpos[1])
            pygame.event.post(pygame.event.Event(
                pygame.MOUSEMOTION, pos=pos, rel=rel, buttons=(0, 0, 0)))
            self._last_mpos = pos
        for key in self._keys:
            code, ch = self._map_key(key)
            if code:
                pygame.event.post(pygame.event.Event(
                    pygame.KEYDOWN, key=code, unicode=ch, mod=0, scancode=0))
                pygame.event.post(pygame.event.Event(
                    pygame.KEYUP, key=code, unicode=ch, mod=0, scancode=0))
        self._keys = []
        self._inj = []
        for btn, down in self._buttons:
            pygame.event.post(pygame.event.Event(
                pygame.MOUSEBUTTONDOWN if down else pygame.MOUSEBUTTONUP,
                pos=pos, button=btn))
        self._buttons = []
        if self._wheel:
            pygame.event.post(pygame.event.Event(
                pygame.MOUSEWHEEL, x=0, y=self._wheel, which=0,
                flipped=False, precise_x=0.0, precise_y=float(self._wheel)))
            self._wheel = 0

    def _shots(self):
        """SHELL_SHOT 支持 "路径@帧;路径@帧"，一次运行可抓多页"""
        spec = os.environ.get("SHELL_SHOT")
        if not spec:
            return []
        out = []
        for seg in spec.split(";"):
            seg = seg.strip()
            if not seg:
                continue
            path, _, fr = seg.rpartition("@")
            if not path:
                path, fr = seg, os.environ.get("SHELL_SHOT_FRAMES", "40")
            out.append((path, int(fr or 40)))
        return out

    def _save(self, path):
        try:
            shot = application.base.win.getScreenshot()
            shot.write(Filename.fromOsSpecific(path))
            print("[SHELL] shot -> %s" % path, flush=True)
        except Exception as exc:
            print("[SHELL] shot failed: %r" % (exc,), flush=True)

    def _maybe_shot(self):
        shots = self._shots()
        if not shots:
            return
        for path, fr in shots:
            if self._frames == fr:
                self._save(path)
        if self._frames >= max(fr for _, fr in shots):
            application.quit()


BRIDGE = None


def input(key):              # ursina 的全局输入回调
    if BRIDGE is not None:
        BRIDGE.on_key(key)


def _build_3d(flat):
    """按启动参数挑一个 3D 玩法，返回 (game, 标签)"""
    if "--sky3d-ai" in flat or any(str(x).endswith("sky3d_ai_mode.py") for x in flat):
        import sky3d_ai_mode as s3ai
        diff = "normal"
        if "--ai-diff" in flat:
            i = flat.index("--ai-diff")
            if i + 1 < len(flat):
                diff = flat[i + 1]
        s3ai._APP = BRIDGE.app
        return s3ai.Sky3DAIGame(difficulty=diff, test_mode=False), "ai"
    if ("--net-host" in flat or "--net-join" in flat
            or any(str(x).endswith("sky3d_net_mode.py") for x in flat)):
        import sky3d_net_mode as s3n
        is_host, ip, port, nick = s3n._parse_args(flat)
        if is_host:
            conn = s3n.NetHost(s3n.NET_PORT)
            room_code = s3n.make_room_code(s3n.get_local_ip())
        else:
            conn = s3n.NetClient(ip, port, timeout=3.0)
            room_code = ""
        s3n._APP = BRIDGE.app
        game = s3n.Sky3DNetGame(conn, is_host, room_code, nickname=nick,
                                skin=s3n.SETTINGS.get("skin", 0), test_mode=False)
        return game, "net"
    import sky3d_mode as s3
    s3._APP = BRIDGE.app
    return s3.Sky3DGame(test_mode=False), "sky"


def run_3d(args=()):
    """在同一个 Ursina 窗口里直接构建 3D 场景（不再起子进程）"""
    application.asset_folder = Path(__file__).parent
    camera.clip_plane_far = 3000
    BRIDGE._scene_baseline()             # 第一次进来时记下基线
    BRIDGE._clear_3d_leftovers()         # 再进 3D 时先清干净上一局的残影
    BRIDGE.quad.enabled = False          # 3D 时隐藏 2D 画布纹理
    ui_before = list(getattr(camera.ui, "children", []))
    try:
        game, label = _build_3d([str(x) for x in args])
    except Exception as exc:
        print("[SHELL] 3D build failed: %r" % (exc,), flush=True)
        BRIDGE.quad.enabled = True
        return 3
    game._want_exit = False
    game._want_exit = False
    exit_code = {"v": 0}

    def _exit(code=0):
        exit_code["v"] = int(code)       # 记住退出码：42 = 退出整个游戏，0 = 只回菜单
        game._want_exit = True           # 原来这里是 os._exit，会把整个外壳杀掉
    game._exit = _exit
    print("[SHELL] 3D built in same window (%s)" % label, flush=True)

    exit_at = os.environ.get("SHELL_3D_EXIT_AT")
    script = {}
    for seg in (os.environ.get("SHELL_3D_SCRIPT") or "").split(";"):
        seg = seg.strip()
        if ":" in seg:
            fr, _, act = seg.partition(":")
            try:
                script[int(fr)] = act.strip()
            except ValueError:
                pass
    BRIDGE.install_mouse_override()      # 菜单期间用真实光标覆盖 ursina 的鼠标坐标
    if BRIDGE._frames < 10:              # 只在"启动即 3D"的自检路径里居中，正常进入不动窗口
        BRIDGE._center_window(BRIDGE._own_hwnd(require_visible=False))
    BRIDGE._grab_focus()
    focus_until = BRIDGE._frames + 360    # 进入 3D 后头 6 秒持续盯着焦点
    limit = BRIDGE._frames + int(os.environ.get("SHELL_3D_MAX_FRAMES", "100000"))
    while not game._want_exit and BRIDGE._frames < limit:
        application.base.taskMgr.step()
        # 真实按键由 ursina 的 taskMgr.step() 直接派发给游戏实体，这里只回放
        # SHELL_KEY 自检注入的键（手动喂，否则自检钩子在 3D 里会失效）
        for k in BRIDGE._inj:
            try:
                game.input(k)
            except Exception as _exc:
                print("[FWD] %r failed %r" % (k, _exc), flush=True)
        # 菜单打开时每帧校正指针：不可信就接管点击（见 poll_menu_click）
        try:
            BRIDGE.poll_menu_click(game)
        except Exception as _exc:
            print("[CLICK] poll failed %r" % (_exc,), flush=True)
        BRIDGE._buttons = []
        BRIDGE._keys = []
        BRIDGE._inj = []
        try:
            pygame.event.clear()         # 别让 dummy 事件队列无限增长
        except pygame.error:
            pass
        try:
            game.steer()
        except Exception:
            pass
        BRIDGE._frames += 1
        # 窗口看不见 / 被最小化时一直补：这两种状态下列表里的所有按钮都点不到
        if BRIDGE._frames % 24 == 0:
            try:
                import ctypes as _ct2
                _h = getattr(getattr(game, "raw_mouse", None), "hwnd", None)
                if _h:
                    _u = _ct2.windll.user32
                    _hidden = (not _u.IsWindowVisible(_h)) or _u.IsIconic(_h)
                    _lost = _u.GetForegroundWindow() != _h
                    if _hidden or (BRIDGE._frames <= focus_until and _lost):
                        BRIDGE._grab_focus(silent=True)
            except Exception:
                pass
        # 进 3D 时 Panda 可能把窗口最小化甚至 hide；只要它看不见就补显示，
        # 但不再挪动位置 —— 窗口位置交给用户自己拖
        if BRIDGE._frames % 24 == 0:
            try:
                import ctypes as _ct2
                _h2 = getattr(getattr(game, "raw_mouse", None), "hwnd", None)
                if _h2:
                    _u = _ct2.windll.user32
                    if not _u.IsWindowVisible(_h2) or _u.IsIconic(_h2):
                        _u.ShowWindow(_h2, 5)
            except Exception:
                pass
        # 菜单/弹层打开期间：鼠标一进窗口就补焦点并刷新指针，否则 Panda 的
        # pointer 停在 in_window=False，ursina 的 mouse.x/y 恒为 0，所有菜单
        # 按钮 hover 全是 False —— 点哪里都不响应（"点了返回菜单没反应"）。
        try:
            _menu_open = (game.pause_menu or game.over
                          or getattr(game, "dialog_panel", None) is not None
                          and game.dialog_panel.enabled)
            if _menu_open and BRIDGE._frames % 4 == 0:
                _h = getattr(getattr(game, "raw_mouse", None), "hwnd", None)
                if _h:
                    BRIDGE.keep_menu_focus(_h)
        except Exception:
            pass
        if os.environ.get("SHELL_3D_DBG") and BRIDGE._frames % 24 == 0:
            try:
                import ctypes as _ct
                from ursina import held_keys as _hk, mouse as _m
                _hwnd = getattr(getattr(game, "raw_mouse", None), "hwnd", None)
                _fg = _ct.windll.user32.GetForegroundWindow()
                print("[DBG] mouse=%.2f,%.2f left=%s shift=%s lshift=%s "
                      "energy=%.1f dash=%s over=%s pause=%s dlg=%s fgmatch=%s hwnd=%s/%s"
                      % (_m.position[0], _m.position[1], _m.left, _hk["shift"],
                         _hk["left shift"], getattr(game, "energy", -1.0),
                         getattr(game, "dashing", None), game.over,
                         game.pause_menu, game.dialog_panel.enabled,
                         _fg == _hwnd, _hwnd, _fg), flush=True)
                _hov = [(getattr(b, "text", "?"), bool(getattr(b, "hovered", False)))
                        for b, _c in getattr(game, "pause_btns", [])]
                _dhov = [(getattr(b, "text", "?"), bool(getattr(b, "hovered", False)))
                         for b in getattr(getattr(game, "dialog_card", None), "children", [])
                         if hasattr(b, "on_click")]
                print("[HOV] pause=%s over=%s dlg=%s mouse=(%.2f,%.2f)" % (
                    _hov,
                    [(getattr(b, "text", "?"), bool(getattr(b, "hovered", False)))
                     for b, _c in getattr(game, "over_btns", [])],
                    _dhov, _m.position[0], _m.position[1]), flush=True)
            except Exception as _e:
                print("[DBG] err %r" % (_e,), flush=True)
        act = script.get(BRIDGE._frames)
        if act:
            try:
                if act == "pause":
                    game._open_pause_menu()
                elif act == "dialog":
                    game._ask_dialog("返回菜单", "当前进度将不会保存",
                                     lambda: game._exit(0), "返回菜单")
                elif act == "confirm":
                    game._confirm_dialog()
                elif act == "focus":
                    BRIDGE._grab_focus()
                elif act == "resume":
                    game.resume()
                print("[SHELL] 3d script %s" % act, flush=True)
            except Exception as exc:
                print("[SHELL] 3d script %s failed %r" % (act, exc), flush=True)
        BRIDGE._maybe_key()
        BRIDGE._maybe_shot()
        if exit_at and BRIDGE._frames >= int(exit_at):
            game._want_exit = True
    try:
        game.raw_mouse.unlock()
    except Exception:
        pass
    # 把这一轮 3D 新建的实体全部强制销毁——game 自己也在这批里，所以不再单独
    # destroy(game)；关键是 force_destroy=True，否则 eternal 的地形/山/穹顶会被跳过
    BRIDGE._clear_3d_leftovers()
    BRIDGE.quad.enabled = True
    # 3D 的 HUD / 暂停面板挂在 camera.ui 下，不会随 game 销毁；
    # 只清掉本次新建的那些，别动 ursina 自己的 UI 和 2D 画布
    kids = list(getattr(camera.ui, "children", []))
    if os.environ.get("SHELL_DEBUG"):
        print("[SHELL] ui children before=%d" % len(kids), flush=True)
    for child in kids:
        if child is BRIDGE.quad or child in ui_before:
            continue
        try:
            destroy(child, force_destroy=True)
        except Exception as exc:
            print("[SHELL] destroy failed %r" % (exc,), flush=True)
    if os.environ.get("SHELL_DEBUG"):
        p = getattr(game, "pause_panel", None)
        print("[SHELL] ui children after=%d pause_panel=%s" % (
            len(getattr(camera.ui, "children", [])),
            None if p is None else (p.enabled, getattr(p.parent, "name", p.parent))),
            flush=True)
    if os.environ.get("SHELL_DEBUG"):
        p = getattr(game, "pause_panel", None)
        print("[SHELL] ui children after=%d pause_panel=%s" % (
            len(getattr(camera.ui, "children", [])),
            None if p is None else (p.enabled, p.parent)), flush=True)
    BRIDGE.quad.enabled = True
    BRIDGE._keys = []
    BRIDGE._inj = []
    print("[SHELL] 3D closed (code=%s)" % exit_code["v"], flush=True)
    return exit_code["v"]


def _patch_subprocess():
    """让 main.py 里的 subprocess.call 改成本进程内构建 3D"""
    import subprocess

    original = subprocess.call

    def call(args, *a, **k):
        if isinstance(args, (list, tuple)):
            flat = [str(x) for x in args]
            if any(x.endswith(("sky3d_mode.py", "sky3d_ai_mode.py",
                               "sky3d_net_mode.py"))
                   or x in ("--sky3d", "--sky3d-ai", "--net-host", "--net-join")
                   for x in flat):
                return run_3d(flat)
        return original(args, *a, **k)

    subprocess.call = call


def run():
    global BRIDGE
    BRIDGE = Bridge()
    if os.environ.get("SHELL_3D"):
        v = os.environ["SHELL_3D"]
        args = {"ai": ("--sky3d-ai",), "net": ("--net-host",)}.get(v, ())
        try:
            run_3d(args)
        except Exception:
            import traceback
            traceback.print_exc()
        print("[SHELL] 3d loop ended", flush=True)
        application.quit()
        return
    import main
    main.create_display = BRIDGE.create_display
    main.toggle_fullscreen = BRIDGE.toggle_fullscreen
    _patch_subprocess()
    try:
        main.main()
    except SystemExit:
        pass
    print("[SHELL] main loop ended", flush=True)
    application.quit()


if __name__ == "__main__":
    run()
