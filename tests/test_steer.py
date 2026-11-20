"""临时单测：用假窗口喂指针序列，验证 steer 的方向、死区、回中伪影丢弃"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sky3d_mode as m

W, H = 1920, 1080


class FakePtr:
    def __init__(self, x, y, inwin=True):
        self._x, self._y, self.in_window = x, y, inwin

    def get_x(self):
        return float(self._x)

    def get_y(self):
        return float(self._y)


class FakeWin:
    def __init__(self):
        self.ptr = FakePtr(W // 2, H // 2)
        self.warps = []

    def get_x_size(self):
        return W

    def get_y_size(self):
        return H

    def get_pointer(self, i):
        return self.ptr

    def move_pointer(self, i, x, y):
        self.warps.append((x, y))
        self.ptr._x, self.ptr._y = x, y


win = FakeWin()
m.application = types.SimpleNamespace(base=types.SimpleNamespace(win=win))
g = m.Sky3DGame.__new__(m.Sky3DGame)
g.paused = g.pause_menu = g.over = False
g.mouse_sens = 1.6
g.target_angle = 0.0
g.target_pitch = 0.0
g._mp = None
g._mskip = 0

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else "  -> " + detail))


def frame(x, y, inwin=True):
    win.ptr._x, win.ptr._y, win.ptr.in_window = x, y, inwin
    g.steer()


# 1) 首帧只建立基准，不转向
frame(W // 2, H // 2)
a0, p0 = g.target_angle, g.target_pitch
check("首帧建立基准不转向", abs(a0) < 1e-9 and abs(p0) < 1e-9, f"{a0},{p0}")

# 2) 静止不动 → 不转
for _ in range(5):
    frame(W // 2, H // 2)
check("静止不转", abs(g.target_angle) < 1e-9 and abs(g.target_pitch) < 1e-9,
      f"{g.target_angle},{g.target_pitch}")

# 3) 右移 100px → 角度减小（右转），俯仰不变
frame(W // 2 + 100, H // 2)
check("右移右转(angle 减小)", g.target_angle < -0.05 and abs(g.target_pitch) < 1e-9,
      f"angle={g.target_angle} pitch={g.target_pitch}")

# 4) 回中后再左移 100px → 角度增大（左转）
for _ in range(2):
    frame(W // 2, H // 2)             # 连按两次：一次 warp 一次起效
a_before = g.target_angle
frame(W // 2 - 100, H // 2)
check("左移左转(angle 增大)", g.target_angle > a_before,
      f"{a_before} -> {g.target_angle}")

# 5) 上移 80px → 抬头（pitch 增大）
for _ in range(2):
    frame(W // 2, H // 2)
p_before = g.target_pitch
frame(W // 2, H // 2 - 80)
check("上移抬头(pitch 增大)", g.target_pitch > p_before, f"{p_before} -> {g.target_pitch}")

# 6) 下移 80px → 低头（pitch 减小）
for _ in range(2):
    frame(W // 2, H // 2)
p_before = g.target_pitch
frame(W // 2, H // 2 + 80)
check("下移低头(pitch 减小)", g.target_pitch < p_before, f"{p_before} -> {g.target_pitch}")

# 7) 回中伪影：帧内 warp 到中心，下一帧报"中间位置"，应被整帧丢弃
for _ in range(2):
    frame(W // 2, H // 2)
g.target_angle = 0.0
g._mp = (0.5, 0.5)
frame(int(W * 0.90), H // 2)         # 触发边缘 warp（本帧 dx=0.40 > 0.25，丢弃）
check("触发回中", len(win.warps) > 0, f"warps={len(win.warps)}")
a_before = g.target_angle
frame(int(W * 0.70), H // 2)         # 引擎报中间位置：必须被跳过帧吃掉
check("回中后中间位置不产生转向", abs(g.target_angle - a_before) < 1e-9,
      f"{a_before} -> {g.target_angle}")

# 8) 掉帧尖峰：单帧跳 0.3 屏宽 → 丢弃，不甩
a_before = g.target_angle
g._mskip = 0
frame(int(W * 0.40), H // 2)
check("超大位移(>0.25屏)丢弃", abs(g.target_angle - a_before) < 1e-9)

# 9) 指针离开窗口 → 不转
a_before = g.target_angle
frame(W + 50, H // 2, inwin=False)
check("指针出窗不转", abs(g.target_angle - a_before) < 1e-9)

# 10) 俯仰钳制
g.target_pitch = 0.0
for _ in range(200):
    frame(W // 2, max(0, H // 2 - 300))
    g._mp = (0.5, g._mp[1] - 0.10)   # 反复制造大上移
check("俯仰不越 PITCH_LIMIT", abs(g.target_pitch) <= m.PITCH_LIMIT + 1e-9,
      f"pitch={g.target_pitch}")

print("RESULT:", "PASS" if ok else "FAIL")
