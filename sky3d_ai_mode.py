"""天空贪吃蛇 3D · 人机对战（AI 对手）。

玩法：与「天空对战」共用 3D 世界与 HP 制规则，但对手由本地 AI 驾驶：
  • 双方各 100 HP：Q 冲击波 / E 闪避 / R 护盾；Shift 冲刺；吃果实与穿光环升级
    （每级 +10 生命上限 / +3 攻击）；任意一方 HP 归零结束。
  • AI 会主动采集果实/光环、追击并预判玩家位置、在玩家发射光波时闪避、低血量时开盾拉扯。

AI 算法（实时 3D 飞行，参考 Steering Behaviors / 人工势场法 / Pursuit）：
  1. 避障（局部规划）：球形探测 + 人工势场（APF）斥力。
     - 陨石：径向斥力 + 切向绕行力（避免正面顶死，缓解势场局部极小）；
     - 地形：前方 4 段高度探测，按高差产生爬升力，并向较低一侧偏航；
     - 边界：靠近世界边缘时向场地中心回归；
     - 自身轨迹：近距离分离力（Separation），避免把自己绕进去。
  2. 目标追踪：Seek + Pursuit（按玩家速度计算提前量），配合角速度限制平滑转向。
  3. 行为决策：有限状态机 + 效用评分（采集 / 追击 / 闪避 / 防守 / 脱困）。
  4. 脱困：一段时间内目标距离没有缩短 → 进入 Wander（随机游走 + 爬升）。
  5. 难度配置：反应间隔、转向速率、预判精度、攻击频率、闪避/防守概率、避障余量。

运行方式：python sky3d_ai_mode.py [--ai-diff easy|normal|hard]
退出码：0=返回菜单  42=退出整个游戏
"""
import os
import sys
import math
import random
from collections import deque

from ursina import Ursina, Entity, Text, camera, window, color, application, held_keys
from ursina import time as ursina_time
from panda3d.core import loadPrcFileData, TransparencyAttrib

import sky3d_mode as s3
from sky3d_mode import (Sky3DGame, WORLD, SC, LAND_BAND, NEAR_HIDE, MAX_ALT,
                        PITCH_LIMIT, HEAD_COL_R, TERRAIN_MAX, RING_RADIUS, RING_XP,
                        RING_ENERGY, BUFF_DUR, DASH_SPEED, ZONE_DPS,
                        _w, _additive, _unlit)
from sky3d_net_mode import (HP_MAX, ATTACK_DAMAGE, ATTACK_RANGE, ATTACK_SPEED,
                            ATTACK_HIT_RADIUS, ATTACK_HIT_HEIGHT, ATTACK_CD,
                            DODGE_CD, DODGE_INVULN, DODGE_SPEED, GUARD_CD,
                            GUARD_DURATION, GUARD_REDUCTION, HP_PER_LEVEL,
                            DAMAGE_PER_LEVEL, HIT_XP, BRAKE_SPEED)
from config import (SKY_SPEED, SKY_TURN_RATE, SKY_HEAD_RADIUS, SKY_TRAIL_SPACING,
                    SKY_INITIAL_LENGTH, SKY_MAX_LENGTH, SKY_LENGTH_PER_XP,
                    SKY_XP_PER_LEVEL, SKINS, SETTINGS)
from utils import load_settings, save_settings

AI_CELL = 420.0                     # 陨石空间哈希单元（像素）
AI_METEOR_RANGE = 520.0             # 参与斥力计算的陨石搜索半径（像素）

# 难度：数值越高 AI 越强（speed/turn 为倍率，think 为决策间隔秒，aim 为瞄准抖动）
AI_DIFFICULTY = {
    "easy": {"name": "简单", "speed": 0.86, "turn": 0.62, "think": 0.24,
             "aim": 0.34, "atk_cd_mul": 1.55, "dodge": 0.28, "aggr": 0.55,
             "margin": 3.0},
    "normal": {"name": "普通", "speed": 0.95, "turn": 0.84, "think": 0.15,
               "aim": 0.18, "atk_cd_mul": 1.15, "dodge": 0.62, "aggr": 0.82,
               "margin": 2.2},
    "hard": {"name": "困难", "speed": 1.03, "turn": 1.00, "think": 0.09,
             "aim": 0.08, "atk_cd_mul": 0.88, "dodge": 0.90, "aggr": 1.00,
             "margin": 1.9},
}
AI_STATE_TEXT = {"collect": "采集", "hunt": "追击", "evade": "闪避",
                 "guard": "防守", "wander": "脱困", "zone": "回圈"}

TEST_MODE = os.environ.get("SKY3D_TEST") == "1"
_AI_FAST = os.environ.get("SKY3D_AI_FAST") == "1"
_AI_SHOT = os.environ.get("SKY3D_AI_SHOT", "")
_AI_SHOT_AT = float(os.environ.get("SKY3D_AI_SHOT_AT", "8") or 8)
_AI_TEST_EXIT = float(os.environ.get("SKY3D_AI_TEST_EXIT", "0") or 0)

_APP = None


def _wrap(a):
    """角度归一到 (-pi, pi]"""
    return (a + math.pi) % (math.pi * 2.0) - math.pi


class Sky3DAIGame(Sky3DGame):
    """3D 天空人机对战：玩家本地操控，AI 由本地算法驾驶"""

    def __init__(self, difficulty="normal", test_mode=False, seed=None):
        self._ai_inited = False
        self._ai_hud_ready = False
        self.diff_key = difficulty if difficulty in AI_DIFFICULTY else "normal"
        self.diff = AI_DIFFICULTY[self.diff_key]
        self.player_nick = (SETTINGS.get("nickname") or "玩家")[:10]
        self.ai_nick = f"AI · {self.diff['name']}"
        self.ai_skin = (SETTINGS.get("skin", 0) + 2) % len(SKINS)
        # 战斗状态先给出默认值，避免基类构造期间的 HUD 刷新访问不到
        self.hp = self.hp_max = HP_MAX
        self.attack_power = ATTACK_DAMAGE
        self.ai_hp = self.ai_hp_max = HP_MAX
        self.ai_attack = ATTACK_DAMAGE
        self.ai_xp = self.ai_level = 0
        self.ai_level = 1
        self.ai_target_len = SKY_INITIAL_LENGTH
        self.match_phase = "countdown"
        self.countdown_left = 3.0
        self.ai_state = "collect"
        self.ai_trail = deque()
        self.ai_x = self.ai_y = self.ai_h = 0.0
        self.ai_px = self.ai_py = self.ai_ph = 0.0
        self.ai_angle = self.ai_target_angle = 0.0
        self.ai_pitch = self.ai_target_pitch = 0.0
        self._ai_vis_n = -1
        super().__init__(test_mode=test_mode, seed=seed)

        self._ai_inited = True
        self._build_ai_snake()
        self._build_meteor_grid()
        self._build_ai_hud()
        self._build_ai_combat_ui()
        self._ai_hud_ready = True
        self.reset()
        if TEST_MODE:
            print(f"[AI] ready diff={self.diff_key} seed={seed}", flush=True)

    # ---------- 实体 / HUD 搭建 ----------

    def _build_ai_snake(self):
        max_seg = max(8, int(SKY_MAX_LENGTH / SKY_TRAIL_SPACING))
        self.ai_seg_ents = [Entity(model="sphere", scale=1.0, visible=False)
                            for _ in range(max_seg)]
        self.ai_head = Entity(model="sphere", scale=SKY_HEAD_RADIUS * SC * 2.2,
                              visible=False)
        for dz in (0.30, -0.30):
            Entity(parent=self.ai_head, model="sphere", scale=0.2,
                   position=(0.26, 0.2, dz), color=color.white)
            Entity(parent=self.ai_head, model="sphere", scale=0.1,
                   position=(0.40, 0.2, dz * 1.07),
                   color=color.rgb(0.08, 0.08, 0.16))
        skin = SKINS[self.ai_skin % len(SKINS)]
        self._ai_head_rgb = (skin["head"][0] / 255.0, skin["head"][1] / 255.0,
                             skin["head"][2] / 255.0)
        self.ai_head.color = color.rgb(*self._ai_head_rgb)
        self._ai_vis_n = -1

    def _recolor_ai_segments(self, n):
        skin = SKINS[self.ai_skin % len(SKINS)]
        c1, c2 = skin["c1"], skin["c2"]
        denom = max(1, n - 1)
        for i in range(n):
            e = self.ai_seg_ents[i]
            k = i / denom
            e.visible = True
            e.scale = (0.92 * (1 - k) + 0.34 * k) * 2
            e.color = color.rgb((c1[0] * (1 - k) + c2[0] * k) / 255,
                                (c1[1] * (1 - k) + c2[1] * k) / 255,
                                (c1[2] * (1 - k) + c2[2] * k) / 255)

    def _build_ai_hud(self):
        kw = {"font": self.font} if self.font else {}
        self.hud_hint.text = ("鼠标转向 · Shift 冲刺 · S 减速拉扯 · Q 冲击波 · "
                              "E 闪避 · R 护盾 · P 暂停 · ESC 菜单")

        # 玩家血量（左下）
        self.hp_root = Entity(parent=camera.ui)
        Entity(parent=self.hp_root, model="quad",
               color=color.rgba(0.06, 0.10, 0.20, 0.85),
               position=(-0.44, -0.378), scale=(0.34, 0.024))
        self.hp_bar = Entity(parent=self.hp_root, model="quad",
                             color=color.rgb(0.34, 0.95, 0.46),
                             position=(-0.60, -0.378), scale=(0.32, 0.017))
        self.hp_text = Text(parent=self.hp_root, text=f"HP {HP_MAX} / {HP_MAX}",
                            position=(-0.44, -0.350), origin=(0, 0), scale=0.88,
                            color=color.rgb(0.86, 0.98, 1.0), **kw)

        # AI 血量（顶部中央）
        self.ai_hp_root = Entity(parent=camera.ui)
        Entity(parent=self.ai_hp_root, model="quad",
               color=color.rgba(0.10, 0.08, 0.16, 0.78),
               position=(0, 0.404), scale=(0.34, 0.017))
        self.ai_hp_bar = Entity(parent=self.ai_hp_root, model="quad",
                                color=color.rgb(1.0, 0.45, 0.36),
                                position=(-0.17, 0.404), scale=(0.34, 0.011))
        self.ai_name_text = Text(parent=self.ai_hp_root, text=self.ai_nick,
                                 position=(0, 0.426), origin=(0, 0),
                                 scale=0.72, color=color.rgba(1.0, 0.82, 0.68, 0.96),
                                 **kw)
        self.ai_state_text = Text(parent=self.ai_hp_root, text="",
                                  position=(0.20, 0.398), origin=(0, 0),
                                  scale=0.66, color=color.rgba(0.80, 0.92, 1.0, 0.9),
                                  **kw)

        # 技能栏（底部居中）
        self.skill_root = Entity(parent=camera.ui)
        self.skill_slots = []
        for i, (key, name, col) in enumerate((("q", "冲击波", (1.0, 0.58, 0.22)),
                                              ("e", "闪避", (0.42, 1.0, 0.72)),
                                              ("r", "护盾", (0.36, 0.82, 1.0)))):
            x = -0.10 + i * 0.10
            root = Entity(parent=self.skill_root, position=(x, -0.412))
            Entity(parent=root, model="quad",
                   color=color.rgba(0.06, 0.10, 0.20, 0.86),
                   scale=(0.086, 0.062))
            Text(parent=root, text=key.upper(), position=(-0.030, 0.015),
                 origin=(0, 0), scale=0.95, color=color.rgb(*col), **kw)
            Text(parent=root, text=name, position=(0.004, -0.021),
                 origin=(0, 0), scale=0.58,
                 color=color.rgba(0.82, 0.90, 1.0, 0.9), **kw)
            cd = Text(parent=root, text="", position=(0.036, 0.013),
                      origin=(1, 0), scale=0.8,
                      color=color.rgba(1.0, 0.72, 0.35, 1.0), **kw)
            self.skill_slots.append({"key": key, "cd": cd, "last": ""})

        # 对战提示 / 倒计时
        self.center_text = Text(parent=camera.ui, text="", position=(0, 0.03),
                                origin=(0, 0), scale=3.0,
                                color=color.rgba(0.55, 0.95, 1.0, 1.0), **kw)
        self._center_hold = 0.0

        # 受击全屏红闪
        self.hit_quad = Entity(parent=camera.ui, model="quad", scale=(2, 2),
                               color=color.rgba(0.95, 0.08, 0.08, 0.0),
                               enabled=False)
        self.hit_quad.setTransparency(TransparencyAttrib.MAlpha)

        # 小地图上的 AI 点位
        self.mm_ai = Entity(parent=camera.ui, model="quad",
                            color=color.rgb(1.0, 0.42, 0.30),
                            scale=(0.014, 0.014), enabled=False)

        self._ai_extra_hud = [self.hp_root, self.ai_hp_root, self.skill_root,
                              self.center_text, self.hit_quad, self.mm_ai]
        self._hp_ui_key = None
        self._ai_hp_ui_key = None

    def _set_hud_visible(self, visible):
        super()._set_hud_visible(visible)
        if not getattr(self, "_ai_hud_ready", False):
            return
        for e in self._ai_extra_hud:
            e.enabled = visible
        self.mm_ai.enabled = visible and self.match_phase in ("countdown", "play")
        if not visible:
            self.hit_quad.enabled = False

    def _build_ai_combat_ui(self):
        kw = {"font": self.font} if self.font else {}
        # 防御 / 闪避状态泡（挂在两个蛇头下）
        self.guard_bubble = Entity(parent=self.head, model="sphere", scale=1.55,
                                   color=color.rgba(0.38, 0.82, 1.0, 0.20),
                                   visible=False)
        _unlit(self.guard_bubble, depth_write=False)
        self.dodge_halo = Entity(parent=self.head, model="sphere", scale=1.35,
                                 color=color.rgba(0.45, 1.0, 0.72, 0.16),
                                 visible=False)
        _unlit(self.dodge_halo, depth_write=False)
        self.ai_guard_bubble = Entity(parent=self.ai_head, model="sphere",
                                      scale=1.55,
                                      color=color.rgba(0.38, 0.82, 1.0, 0.20),
                                      visible=False)
        _unlit(self.ai_guard_bubble, depth_write=False)
        self.ai_dodge_halo = Entity(parent=self.ai_head, model="sphere",
                                    scale=1.35,
                                    color=color.rgba(0.45, 1.0, 0.72, 0.16),
                                    visible=False)
        _unlit(self.ai_dodge_halo, depth_write=False)

        # 冲击波弹体（玩家橙色 / AI 红色）
        self.p_shock_core = Entity(model="sphere", visible=False,
                                   color=color.rgba(1.0, 0.66, 0.22, 0.88))
        _additive(self.p_shock_core)
        self.p_shock_halo = Entity(model="sphere", visible=False,
                                   color=color.rgba(1.0, 0.46, 0.12, 0.32))
        _additive(self.p_shock_halo)
        self.ai_shock_core = Entity(model="sphere", visible=False,
                                    color=color.rgba(1.0, 0.38, 0.22, 0.88))
        _additive(self.ai_shock_core)
        self.ai_shock_halo = Entity(model="sphere", visible=False,
                                    color=color.rgba(1.0, 0.20, 0.10, 0.32))
        _additive(self.ai_shock_halo)

        # 3D 特效池：扩散波 / 命中火花
        self._fx_pool = []
        for _ in range(40):
            e = Entity(model="sphere", color=color.rgba(1, 1, 1, 0.5),
                       visible=False)
            _additive(e)
            self._fx_pool.append({"e": e, "age": 0.0, "ttl": 0.0,
                                  "x": 0.0, "y": 0.0, "h": 0.0,
                                  "vx": 0.0, "vy": 0.0, "vh": 0.0,
                                  "s0": 1.0, "s1": 1.0, "a0": 0.5,
                                  "rgb": (1.0, 1.0, 1.0)})
        # 伤害数字
        self._pops = []
        for _ in range(6):
            t = Text(parent=camera.ui, text="", position=(0, 0.06),
                     origin=(0, 0), scale=2.0,
                     color=color.rgba(1, 0.6, 0.3, 0.0), **kw)
            self._pops.append({"t": t, "age": 0.0, "ttl": 0.0,
                               "x0": 0.0, "y0": 0.0, "rgb": (1, 1, 1)})
        self._pop_i = 0
        self._ai_extra_hud += [p["t"] for p in self._pops]
        self.fx_rng = random.Random(20261117)
        self.hit_flash = 0.0
        self.ai_hit_flash = 0.0

    def _level_stats(self, level):
        lv = max(1, int(level))
        return (HP_MAX + (lv - 1) * HP_PER_LEVEL,
                ATTACK_DAMAGE + (lv - 1) * DAMAGE_PER_LEVEL)

    # ---------- 状态重置 ----------

    def reset(self):
        super().reset()
        if not self._ai_inited:
            return
        self._reset_ai_state()

    def _reset_ai_state(self):
        self.match_phase = "countdown"
        self.countdown_left = 1.0 if (TEST_MODE and _AI_FAST) else 3.0
        self.result = None
        self.paused = True
        self._center_hold = 0.0
        self.center_text.text = ""

        # 玩家战斗状态
        self.hp_max, self.attack_power = self._level_stats(1)
        self.hp = self.hp_max
        self.atk_cd = 0.0
        self.dodge_cd = 0.0
        self.guard_cd = 0.0
        self.dodge_t = 0.0
        self.guard_t = 0.0
        self.speed_scale = 1.0
        self.hit_flash = 0.0
        self.p_wave = self._new_wave()

        # AI 状态
        self.ai_xp = 0
        self.ai_level = 1
        self.ai_hp_max, self.ai_attack = self._level_stats(1)
        self.ai_hp = self.ai_hp_max
        self.ai_target_len = SKY_INITIAL_LENGTH
        self.ai_energy = 100.0
        self.ai_dashing = False
        self.ai_buffs = {"magnet": 0.0, "double": 0.0}
        self.ai_atk_cd = 0.0
        self.ai_dodge_cd = 0.0
        self.ai_guard_cd = 0.0
        self.ai_dodge_t = 0.0
        self.ai_guard_t = 0.0
        self.ai_hit_flash = 0.0
        self.ai_state = "collect"
        self.ai_think_t = 0.0
        self.ai_goal = (WORLD / 2.0, WORLD / 2.0, 4.0)
        self.ai_jitter = (0.0, 0.0, 0.0)
        self.ai_stuck_t = 0.0
        self._ai_goal_t = 0.0
        self._ai_goal_d = 0.0
        self._threat_roll = False
        self._dodge_wanted = False
        self._ai_ring_d = {}
        self.ai_zone_acc = 0.0
        self.a_wave = self._new_wave()
        self.ai_trail = deque()
        self._ai_vis_n = -1
        self._player_vx = 0.0
        self._player_vy = 0.0
        self._player_vh = 0.0

        self._place_fighters()
        self._reset_fx()
        self._hp_ui_key = None
        self._ai_hp_ui_key = None
        self._update_hud_text()
        self._update_ai_hud()
        if hasattr(self, "mm_ai"):
            self.mm_ai.enabled = False
        self._enable_safe_zone()

    # ---------- 安全区伤害 ----------

    def _zone_hurt(self, dmg):
        if self.over or self.match_phase != "play":
            return
        self.hp = max(0, self.hp - dmg)
        self.hit_flash = max(self.hit_flash, 0.12)
        self._spawn_pop(f"-{dmg}", (1.0, 0.34, 0.28), 1.6)
        if self.hp <= 0:
            self._finish_match("ai")

    def _zone_tick_others(self, dt):
        if self.over or self.match_phase != "play":
            return
        dz = math.hypot(self.ai_x - self.zone_cx, self.ai_y - self.zone_cy)
        if dz > self.zone_r:
            dps = ZONE_DPS[min(self.zone_phase, len(ZONE_DPS) - 1)]
            self.ai_zone_acc += dps * dt
            if self.ai_zone_acc >= 1.0:
                dmg = int(self.ai_zone_acc)
                self.ai_zone_acc -= dmg
                self.ai_hp = max(0, self.ai_hp - dmg)
                self.ai_hit_flash = max(self.ai_hit_flash, 0.12)
                self._spawn_pop(f"AI -{dmg}", (1.0, 0.5, 0.3), 1.5)
                if self.ai_hp <= 0:
                    self._finish_match("player")
        else:
            self.ai_zone_acc = 0.0

    @staticmethod
    def _new_wave():
        return {"on": False, "x": 0.0, "y": 0.0, "h": 0.0,
                "dx": 1.0, "dy": 0.0, "dh": 0.0, "trav": 0.0, "age": 0.0}

    def _place_fighters(self):
        """固定出生点：玩家在左侧向东，AI 在右侧向西"""
        c = WORLD / 2.0
        off = 300.0
        self.x = self.px = c - off
        self.y = self.py = c
        self.h = self.ph = 3.2
        self.angle = self.target_angle = 0.0
        self.pitch = self.target_pitch = 0.0
        self.trail.clear()
        for i in range(34):
            self.trail.append((self.x - SKY_TRAIL_SPACING * i, self.y, self.h))
        self._vis_n = -1

        self.ai_x = self.ai_px = c + off
        self.ai_y = self.ai_py = c
        self.ai_h = self.ai_ph = 3.2
        self.ai_angle = self.ai_target_angle = math.pi
        self.ai_pitch = self.ai_target_pitch = 0.0
        self.ai_trail.clear()
        for i in range(34):
            self.ai_trail.append((self.ai_x - math.cos(math.pi) * SKY_TRAIL_SPACING * i,
                                  self.ai_y - math.sin(math.pi) * SKY_TRAIL_SPACING * i,
                                  self.ai_h))
        self._update_camera(1.0)

    def _build_meteor_grid(self):
        """陨石空间哈希：把障碍降到每帧只检查邻近单元"""
        grid = {}
        for m in self.meteors:
            key = (int(m["x"] // AI_CELL), int(m["y"] // AI_CELL))
            grid.setdefault(key, []).append(m)
        self._meteor_grid = grid

    def _meteors_near(self, x, y, r):
        cell = AI_CELL
        gx0 = int((x - r) // cell)
        gx1 = int((x + r) // cell)
        gy0 = int((y - r) // cell)
        gy1 = int((y + r) // cell)
        out = []
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                out.extend(self._meteor_grid.get((gx, gy), ()))
        return out

    # ---------- 成长 ----------

    def _update_hud_text(self):
        super()._update_hud_text()
        if not getattr(self, "_ai_hud_ready", False):
            return
        self.hud_lv.text = (f"Lv {self.level}   经验 {self.xp}   "
                            f"攻击 {self.attack_power}")

    def _gain_xp(self, amount, t):
        if not getattr(self, "_ai_inited", False):
            super()._gain_xp(amount, t)
            return
        old = self.level
        super()._gain_xp(amount, t)
        if self.level > old:
            self._apply_player_growth()

    def _apply_player_growth(self):
        old_max = self.hp_max
        self.hp_max, self.attack_power = self._level_stats(self.level)
        self.hp = min(self.hp_max, self.hp + max(0, self.hp_max - old_max))
        self._spawn_wave_fx(self.x, self.y, self.h, (1.0, 0.86, 0.35), 0.6, 5.2, 0.5)
        self._spawn_burst(self.x, self.y, self.h, (1.0, 0.86, 0.35), 14)
        self._spawn_pop(f"升级 Lv {self.level} · 攻击 {self.attack_power}", (1.0, 0.86, 0.4), 1.8)
        self._play(self.snd_eat)
        self._update_hud_text()

    def _ai_gain_xp(self, amount):
        if amount <= 0 or self.match_phase != "play" or self.over:
            return
        if self.ai_buffs.get("double", 0.0) > 0.0:
            amount *= 2
        old = self.ai_level
        self.ai_xp += amount
        self.ai_level = self.ai_xp // SKY_XP_PER_LEVEL + 1
        self.ai_target_len = min(SKY_MAX_LENGTH,
                                 SKY_INITIAL_LENGTH + self.ai_xp * SKY_LENGTH_PER_XP)
        if self.ai_level > old:
            old_max = self.ai_hp_max
            self.ai_hp_max, self.ai_attack = self._level_stats(self.ai_level)
            self.ai_hp = min(self.ai_hp_max, self.ai_hp + max(0, self.ai_hp_max - old_max))
            self._spawn_pop(f"AI 升级 Lv {self.ai_level}", (1.0, 0.62, 0.42), 1.6)

    # ---------- 距离 / 威胁 ----------

    def _dist_to_player(self):
        return self._dist3(self.ai_x, self.ai_y, self.ai_h,
                           self.x, self.y, self.h)

    @staticmethod
    def _dist3(ax, ay, ah, bx, by, bh):
        dx = ax - bx
        dy = ay - by
        dz = (ah - bh) / SC
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _incoming_threat(self):
        """玩家光波是否将在短时间内从 AI 附近掠过"""
        w = self.p_wave
        if not w["on"]:
            return False
        rx = self.ai_x - w["x"]
        ry = self.ai_y - w["y"]
        rz = (self.ai_h - w["h"]) / SC
        along = rx * w["dx"] + ry * w["dy"] + rz * w["dh"]
        if along < -40.0 or along > ATTACK_RANGE:
            return False
        lat2 = (rx * rx + ry * ry + rz * rz) - along * along
        return lat2 < (ATTACK_HIT_RADIUS * 1.9) ** 2

    # ---------- AI 决策（有限状态机 + 效用） ----------

    def _ai_think(self):
        ax, ay, ah = self.ai_x, self.ai_y, self.ai_h
        d = self._dist_to_player()
        hp_frac = self.ai_hp / max(1, self.ai_hp_max)

        # 安全区优先：靠近或越过边界时先回圈
        if getattr(self, "safe_zone_enabled", False):
            dz = math.hypot(ax - self.zone_cx, ay - self.zone_cy)
            if dz > self.zone_r - 110.0:
                self.ai_state = "zone"
                self.ai_goal = self._clamp_goal(self.zone_cx, self.zone_cy, 4.0)
                self.ai_jitter = (0.0, 0.0, 0.0)
                self._ai_goal_d = self._dist3(ax, ay, ah, *self.ai_goal)
                self._ai_goal_t = 0.0
                return

        # 每次来袭光波只掷一次闪避判定（难度 dodge 决定反应概率）
        threat = self._incoming_threat()
        if threat and not self._threat_roll:
            self._threat_roll = True
            self._dodge_wanted = self.fx_rng.random() < self.diff["dodge"]
        elif not threat:
            self._threat_roll = False
            self._dodge_wanted = False

        hunt_range = 650.0 + 750.0 * self.diff["aggr"]
        if self._dodge_wanted and self.ai_dodge_cd <= 0.0:
            state = "evade"
        elif self.ai_stuck_t > 1.0:
            state = "wander"
        elif hp_frac < 0.38 and d < 900.0:
            state = "guard"
        elif d < hunt_range and self.ai_atk_cd <= 0.0:
            state = "hunt"
        elif self._pick_collect_target() is not None:
            state = "collect"
        else:
            state = "hunt"
        self.ai_state = state

        if state == "hunt":
            # Pursuit：按玩家速度做提前量预测，并叠加随难度变化的瞄准抖动
            lead_t = min(1.1, d / (SKY_SPEED * 1.15))
            jx, jy, jh = self.ai_jitter
            self.ai_goal = (self._clamp_goal(self.x + self._player_vx * lead_t * 0.55 + jx,
                                             self.y + self._player_vy * lead_t * 0.55 + jy,
                                             self.h + self._player_vh * lead_t * 0.35 + jh))
        elif state == "collect":
            tgt = self._pick_collect_target()
            if tgt is None:
                self.ai_goal = (self.x, self.y, self.h)
            else:
                self.ai_goal = self._clamp_goal(*tgt)
        elif state == "guard":
            # 远离玩家，同时保持高度优势
            vx, vy = ax - self.x, ay - self.y
            dl = math.hypot(vx, vy) + 1e-6
            self.ai_goal = self._clamp_goal(ax + vx / dl * 720.0,
                                            ay + vy / dl * 720.0,
                                            min(MAX_ALT - 2.0, ah + 3.0))
            if self.ai_guard_cd <= 0.0:
                self._ai_guard()
        elif state == "evade":
            # 垂直于来袭光波方向闪避（选与当前航向夹角更小的一侧）
            w = self.p_wave
            px, py = w["dx"], w["dy"]
            n1x, n1y = -py, px
            curx, cury = math.cos(self.ai_angle), math.sin(self.ai_angle)
            if n1x * curx + n1y * cury < 0.0:
                n1x, n1y = -n1x, -n1y
            self.ai_goal = self._clamp_goal(ax + n1x * 650.0,
                                            ay + n1y * 650.0,
                                            min(MAX_ALT - 2.0, ah + 4.0))
            if self.ai_dodge_cd <= 0.0:
                self._ai_dodge()
        else:  # wander
            ang = self.ai_angle + self.fx_rng.uniform(-1.1, 1.1)
            self.ai_goal = self._clamp_goal(ax + math.cos(ang) * 560.0,
                                            ay + math.sin(ang) * 560.0,
                                            min(MAX_ALT - 2.0, ah + 4.5))
            self.ai_stuck_t = 0.0

        # 重新选择目标后刷新抖动与落点
        jit = self.diff["aim"] * min(900.0, d) * 0.30
        self.ai_jitter = (self.fx_rng.uniform(-jit, jit),
                          self.fx_rng.uniform(-jit, jit),
                          self.fx_rng.uniform(-1.2, 1.2))
        self._ai_goal_d = self._dist3(ax, ay, ah, *self.ai_goal)
        self._ai_goal_t = 0.0

    @staticmethod
    def _clamp_goal(x, y, h):
        x = min(max(x, LAND_BAND + 80.0), WORLD - LAND_BAND - 80.0)
        y = min(max(y, LAND_BAND + 80.0), WORLD - LAND_BAND - 80.0)
        h = min(max(h, 2.5), MAX_ALT - 2.0)
        return (x, y, h)

    def _pick_collect_target(self):
        """效用评分：经验价值 / 距离。光环价值高，金色果实次之。"""
        ax, ay, ah = self.ai_x, self.ai_y, self.ai_h
        best, best_score = None, 0.0
        for f in self.fruit_pool:
            if not f["active"]:
                continue
            d = self._dist3(ax, ay, ah, f["x"], f["y"], f["h"])
            val = 3.0 if f["gold"] else 1.0
            s = val * 900.0 / (d + 260.0)
            if s > best_score:
                best_score = s
                best = (f["x"], f["y"], f["h"])
        for ring in self.rings:
            if not ring["active"]:
                continue
            d = self._dist3(ax, ay, ah, ring["x"], ring["y"], ring["h"])
            s = 5.5 * 900.0 / (d + 360.0)
            if s > best_score:
                best_score = s
                best = (ring["x"], ring["y"], ring["h"])
        weights = {"shield": 4.0, "double": 4.5, "energy": 2.6, "magnet": 2.2}
        for p in self.powerup_pool:
            if not p["active"]:
                continue
            d = self._dist3(ax, ay, ah, p["x"], p["y"], p["h"])
            s = weights.get(p["kind"], 2.0) * 900.0 / (d + 320.0)
            if s > best_score:
                best_score = s
                best = (p["x"], p["y"], p["h"])
        return best

    # ---------- 转向力（APF + 分离 + 地形探测） ----------

    def _ai_force(self):
        ax, ay, ah = self.ai_x, self.ai_y, self.ai_h
        cp = math.cos(self.ai_pitch)
        cur = (math.cos(self.ai_angle) * cp, math.sin(self.ai_angle) * cp,
               math.sin(self.ai_pitch))

        gx, gy, gh = self.ai_goal
        dx, dy, dz = gx - ax, gy - ay, (gh - ah) / SC
        gd = math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6
        seek = (dx / gd, dy / gd, dz / gd)
        fx, fy, fz = seek[0] * 1.35, seek[1] * 1.35, seek[2] * 1.35

        margin = self.diff["margin"]
        # ---- 陨石：径向斥力 + 切向绕行（势场法） ----
        for m in self._meteors_near(ax, ay, AI_METEOR_RANGE):
            rx = ax - m["x"]
            ry = ay - m["y"]
            rz = (ah - m["h"]) / SC
            dist = math.sqrt(rx * rx + ry * ry + rz * rz) + 1e-6
            need = (m.get("cr", m["r"]) + HEAD_COL_R + margin) / SC
            reach = need * 2.2
            if dist >= reach:
                continue
            k = max(0.0, 1.0 - dist / reach)
            k *= k
            ux, uy, uz = -rx / dist, -ry / dist, -rz / dist
            tx, ty = -uy, ux
            tl = math.hypot(tx, ty) + 1e-6
            tx, ty = tx / tl, ty / tl
            if tx * seek[0] + ty * seek[1] < 0.0:
                tx, ty = -tx, -ty
            fx += (ux * 2.8 + tx * 1.35) * k
            fy += (uy * 2.8 + ty * 1.35) * k
            fz += uz * 1.7 * k + 0.18 * k

        # ---- 地形：前方高度探测 + 低侧偏航 ----
        min_clear = HEAD_COL_R + margin * 0.55
        for look in (70.0, 150.0, 260.0, 390.0):
            scale = 1.0 - look / 500.0
            px = ax + cur[0] * look
            py = ay + cur[1] * look
            top = self._column_top(px, py)
            if top is None:
                continue
            gap = (ah - min_clear) - top
            if gap >= 1.6:
                continue
            strength = min(1.9, (1.6 - gap) * 0.55) * scale
            fz += strength
            side = 95.0
            tl = self._column_top(ax - cur[1] * side, ay + cur[0] * side)
            tr = self._column_top(ax + cur[1] * side, ay - cur[0] * side)
            if tl is not None and tr is not None:
                if tl > tr + 0.30:
                    fx += cur[1] * 0.95 * strength
                    fy -= cur[0] * 0.95 * strength
                elif tr > tl + 0.30:
                    fx -= cur[1] * 0.95 * strength
                    fy += cur[0] * 0.95 * strength
        top_now = self._column_top(ax, ay)
        if top_now is not None and ah < top_now + min_clear + 0.8:
            fz += min(2.2, (top_now + min_clear + 0.8 - ah) * 0.9)

        # ---- 世界边界回归 ----
        edge = 380.0
        if ax < edge:
            fx += (edge - ax) / edge * 2.4
        elif ax > WORLD - edge:
            fx -= (ax - (WORLD - edge)) / edge * 2.4
        if ay < edge:
            fy += (edge - ay) / edge * 2.4
        elif ay > WORLD - edge:
            fy -= (ay - (WORLD - edge)) / edge * 2.4

        # ---- 高度软限制 ----
        if ah > MAX_ALT - 3.0:
            fz -= (ah - (MAX_ALT - 3.0)) * 0.55
        if ah < 2.6:
            fz += (2.6 - ah) * 0.9

        # ---- 自身体分离（Separation）----
        if len(self.ai_trail) > 12:
            n = min(len(self.ai_trail), 96)
            for i in range(8, n, 4):
                tx, ty, th = self.ai_trail[i]
                rx, ry = ax - tx, ay - ty
                rz = (ah - th) / SC
                d2 = rx * rx + ry * ry + rz * rz
                if 1e-6 < d2 < 70.0 * 70.0:
                    d = math.sqrt(d2)
                    k = 1.0 - d / 70.0
                    fx += rx / d * 1.1 * k
                    fy += ry / d * 1.1 * k
                    fz += rz / d * 0.5 * k

        # ---- 脱困：Wander 随机游走 ----
        if self.ai_state == "wander":
            wob = math.sin(self._anim_t * 2.3) * 1.3
            fx += -cur[1] * wob
            fy += cur[0] * wob
            fz += 0.55 + 0.3 * math.sin(self._anim_t * 1.7)

        mag = math.sqrt(fx * fx + fy * fy + fz * fz)
        if mag < 1e-6:
            return cur
        return (fx / mag, fy / mag, fz / mag)

    # ---------- AI 运动 ----------

    def _ai_tick(self, dt):
        self._update_fx(dt)
        self._update_pops(dt)
        if self.hit_flash > 0.0:
            self.hit_flash = max(0.0, self.hit_flash - dt)
        if self.ai_hit_flash > 0.0:
            self.ai_hit_flash = max(0.0, self.ai_hit_flash - dt)

        if self._center_hold > 0.0:
            self._center_hold -= dt
            if self._center_hold <= 0.0:
                self.center_text.text = ""

        if self.match_phase == "countdown" and not self.pause_menu:
            self.countdown_left -= dt
            if self.countdown_left > 0.0:
                self.center_text.text = str(int(math.ceil(self.countdown_left)))
            else:
                self.match_phase = "play"
                self.paused = False
                self.center_text.text = "GO!"
                self._center_hold = 0.75
                self._player_vx = self._player_vy = self._player_vh = 0.0
                if TEST_MODE:
                    print("[AI] phase=play", flush=True)
            return

        if self.match_phase != "play" or self.over:
            return
        if self.paused or self.pause_menu:
            return

        # 冷却 / 技能计时
        self.atk_cd = max(0.0, self.atk_cd - dt)
        self.dodge_cd = max(0.0, self.dodge_cd - dt)
        self.guard_cd = max(0.0, self.guard_cd - dt)
        self.dodge_t = max(0.0, self.dodge_t - dt)
        self.guard_t = max(0.0, self.guard_t - dt)
        self.ai_atk_cd = max(0.0, self.ai_atk_cd - dt)
        self.ai_dodge_cd = max(0.0, self.ai_dodge_cd - dt)
        self.ai_guard_cd = max(0.0, self.ai_guard_cd - dt)
        self.ai_dodge_t = max(0.0, self.ai_dodge_t - dt)
        self.ai_guard_t = max(0.0, self.ai_guard_t - dt)
        for k in self.ai_buffs:
            if self.ai_buffs[k] > 0.0:
                self.ai_buffs[k] = max(0.0, self.ai_buffs[k] - dt)

        # 玩家姿态：闪避加速 / 按住 S 减速拉扯
        if self.dodge_t > 0.0:
            self.speed_scale = DODGE_SPEED
        elif held_keys["s"] or held_keys["down arrow"]:
            self.speed_scale = BRAKE_SPEED
        else:
            self.speed_scale = 1.0

        # 玩家速度（供 Pursuit 预判）
        if dt > 1e-5:
            self._player_vx = (self.x - self.px) / dt
            self._player_vy = (self.y - self.py) / dt
            self._player_vh = (self.h - self.ph) / dt

        # 冲刺决策
        if self.ai_state in ("hunt", "evade") and self.ai_energy > 45.0 \
                and self._dist_to_player() > 200.0:
            self.ai_dashing = True
        elif self.ai_state not in ("hunt", "evade") or self.ai_energy <= 20.0:
            self.ai_dashing = False

        self.ai_think_t -= dt
        if self.ai_think_t <= 0.0:
            self.ai_think_t = self.diff["think"]
            self._ai_think()
            d = self._ai_force()
            self.ai_target_angle = math.atan2(d[1], d[0]) % (math.pi * 2.0)
            self.ai_target_pitch = max(-PITCH_LIMIT * 0.95,
                                       min(PITCH_LIMIT * 0.95,
                                           math.atan2(d[2], math.hypot(d[0], d[1]))))

        self._ai_move(dt)
        self._ai_collide()
        self._ai_pickups(dt)
        self._update_player_wave(dt)
        self._update_ai_wave(dt)
        self._ai_attack_check()
        self._ai_progress_check(dt)
        if TEST_MODE:
            self._ai_log_t = getattr(self, "_ai_log_t", 0.0) + dt
            if self._ai_log_t >= 8.0:
                self._ai_log_t = 0.0
                print(f"[AI] t={self._anim_t:.1f} state={self.ai_state} "
                      f"hp={self.hp}/{self.ai_hp} xp={self.xp}/{self.ai_xp} "
                      f"lv={self.level}/{self.ai_level} "
                      f"dist={self._dist_to_player():.0f} "
                      f"pos=({self.ai_x:.0f},{self.ai_y:.0f},{self.ai_h:.1f}) "
                      f"goal=({self.ai_goal[0]:.0f},{self.ai_goal[1]:.0f},"
                      f"{self.ai_goal[2]:.1f})", flush=True)

    def _ai_move(self, dt):
        cfg = self.diff
        diff = _wrap(self.ai_target_angle - self.ai_angle)
        mt = SKY_TURN_RATE * cfg["turn"] * dt
        if abs(diff) <= mt:
            self.ai_angle = self.ai_target_angle
        else:
            self.ai_angle += mt if diff > 0.0 else -mt
        self.ai_angle %= math.pi * 2.0
        pd = max(-mt, min(mt, self.ai_target_pitch - self.ai_pitch))
        self.ai_pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.ai_pitch + pd))

        if self.ai_dashing:
            self.ai_energy = max(0.0, self.ai_energy - 32.0 * dt)
        else:
            self.ai_energy = min(100.0, self.ai_energy + 16.0 * dt)

        sp = SKY_SPEED * cfg["speed"] * (DASH_SPEED if self.ai_dashing else 1.0)
        if self.ai_dodge_t > 0.0:
            sp *= DODGE_SPEED
        sp *= dt

        self.ai_px, self.ai_py, self.ai_ph = self.ai_x, self.ai_y, self.ai_h
        cp = math.cos(self.ai_pitch)
        self.ai_x += math.cos(self.ai_angle) * cp * sp
        self.ai_y += math.sin(self.ai_angle) * cp * sp
        self.ai_h += math.sin(self.ai_pitch) * sp * SC
        if self.ai_h > MAX_ALT:
            self.ai_h = MAX_ALT
            self.ai_pitch = min(self.ai_pitch, 0.0)
            self.ai_target_pitch = min(self.ai_target_pitch, 0.0)

        # 轨迹（长度随经验成长）
        if self.ai_trail:
            tx, ty, th = self.ai_trail[0]
            dpx, dpy = self.ai_x - tx, self.ai_y - ty
            dh = (self.ai_h - th) / SC
            moved = math.sqrt(dpx * dpx + dpy * dpy + dh * dh)
            if moved > 1e-9:
                steps = int(moved // SKY_TRAIL_SPACING)
                for k in range(1, steps + 1):
                    f = (k * SKY_TRAIL_SPACING) / moved
                    self.ai_trail.appendleft((tx + dpx * f, ty + dpy * f,
                                              th + (self.ai_h - th) * f))
        else:
            self.ai_trail.appendleft((self.ai_x, self.ai_y, self.ai_h))
        max_pts = max(8, int(self.ai_target_len / SKY_TRAIL_SPACING))
        while len(self.ai_trail) > max_pts:
            self.ai_trail.pop()

    def _ai_collide(self):
        """AI 不做坠毁判定：撞地形抬起，撞陨石沿径向弹出，越界掉头"""
        hr = HEAD_COL_R
        if self.ai_x < hr or self.ai_y < hr or \
                self.ai_x > WORLD - hr or self.ai_y > WORLD - hr:
            self.ai_x = min(max(self.ai_x, hr + 1.0), WORLD - hr - 1.0)
            self.ai_y = min(max(self.ai_y, hr + 1.0), WORLD - hr - 1.0)
            self.ai_target_angle = math.atan2(WORLD / 2.0 - self.ai_y,
                                              WORLD / 2.0 - self.ai_x)
            self.ai_angle = self.ai_target_angle
            self.ai_pitch = max(self.ai_pitch, 0.25)

        hit_f, safe_f, lift = self._terrain_sweep(
            self.ai_px, self.ai_py, self.ai_ph,
            self.ai_x, self.ai_y, self.ai_h)
        if hit_f is not None:
            self.ai_x = self.ai_px + (self.ai_x - self.ai_px) * safe_f
            self.ai_y = self.ai_py + (self.ai_y - self.ai_py) * safe_f
            self.ai_h = max(self.ai_h, lift)
            self.ai_pitch = max(self.ai_pitch, 0.45)
            self.ai_target_pitch = max(self.ai_target_pitch, 0.45)
            self.ai_stuck_t = max(self.ai_stuck_t, 0.35)

        for m in self._meteors_near(self.ai_x, self.ai_y, 220.0):
            rx = self.ai_x - m["x"]
            ry = self.ai_y - m["y"]
            rz = (self.ai_h - m["h"]) / SC
            d = math.sqrt(rx * rx + ry * ry + rz * rz) + 1e-6
            need = (m.get("cr", m["r"]) + HEAD_COL_R + 0.15) / SC
            if d >= need:
                continue
            ux, uy, uz = rx / d, ry / d, rz / d
            if abs(ux) + abs(uy) + abs(uz) < 1e-3:
                ux, uy, uz = 1.0, 0.0, 0.4
            self.ai_x = m["x"] + ux * need
            self.ai_y = m["y"] + uy * need
            self.ai_h = m["h"] + uz * need * SC
            self.ai_pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT,
                                                  self.ai_pitch + uz * 0.5))
            self.ai_target_pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT,
                                                         self.ai_target_pitch + uz * 0.5))
            break

    def _ai_progress_check(self, dt):
        """脱困检测：目标距离长时间不缩短就 Wander 一段时间"""
        self._ai_goal_t += dt
        if self._ai_goal_t < 1.2:
            return
        self._ai_goal_t = 0.0
        d = self._dist3(self.ai_x, self.ai_y, self.ai_h, *self.ai_goal)
        if d > self._ai_goal_d - 20.0:
            self.ai_stuck_t += 1.2
        else:
            self.ai_stuck_t = max(0.0, self.ai_stuck_t - 0.7)
        self._ai_goal_d = d

    # ---------- AI 采集 ----------

    def _ai_pickups(self, dt):
        ax, ay, ah = self.ai_x, self.ai_y, self.ai_h
        # 果实
        for f in self.fruit_pool:
            if not f["active"]:
                continue
            if self._dist3(ax, ay, ah, f["x"], f["y"], f["h"]) <= 18.0:
                f["active"] = False
                f["core"].visible = False
                self._ai_gain_xp(f["value"])
        # 光环（带符号距离过零且交点落在环口内）
        rr2 = (RING_RADIUS - 0.14) ** 2
        for i, ring in enumerate(self.rings):
            if not ring["active"]:
                continue
            d1 = self._ring_sd(ring, ax, ay, ah)
            d0 = self._ai_ring_d.get(i)
            self._ai_ring_d[i] = d1
            if d0 is None or d0 * d1 >= 0.0:
                continue
            f = d0 / (d0 - d1)
            cx = ((self.ai_px - ring["x"]) * SC) + \
                ((self.ai_x - ring["x"]) * SC - (self.ai_px - ring["x"]) * SC) * f
            cz = ((self.ai_py - ring["y"]) * SC) + \
                ((self.ai_y - ring["y"]) * SC - (self.ai_py - ring["y"]) * SC) * f
            ch = (self.ai_ph - ring["h"]) + (self.ai_h - self.ai_ph) * f
            tang = cx * (-ring["nz"]) + cz * ring["nx"]
            if ch * ch + tang * tang > rr2:
                continue
            ring["active"] = False
            ring["body"].visible = False
            ring["respawn"] = self._anim_t + s3.RING_RESPAWN
            ring["last_d"] = None
            self.ai_energy = min(100.0, self.ai_energy + RING_ENERGY)
            self._ai_gain_xp(RING_XP)
        # 强化道具
        for p in self.powerup_pool:
            if not p["active"]:
                continue
            if self._dist3(ax, ay, ah, p["x"], p["y"], p["h"]) > 24.0:
                continue
            p["active"] = False
            p["core"].visible = False
            p["glow"].visible = False
            kind = p["kind"]
            if kind == "energy":
                self.ai_energy = 100.0
            elif kind == "shield":
                self.ai_guard_t = max(self.ai_guard_t, 6.0)
            else:
                self.ai_buffs[kind] = BUFF_DUR[kind]
            self._spawn_burst(p["x"], p["y"], p["h"],
                              s3.POWERUP_COLORS[kind], 8)
        # 磁铁：把果实吸向 AI
        if self.ai_buffs.get("magnet", 0.0) > 0.0:
            step = 420.0 * dt
            for f in self.fruit_pool:
                if not f["active"]:
                    continue
                dx, dy = f["x"] - ax, f["y"] - ay
                d2 = dx * dx + dy * dy
                if d2 > 280.0 * 280.0:
                    continue
                dist = math.sqrt(d2) + 1e-6
                k = min(1.0, step / dist)
                f["x"] -= dx * k
                f["y"] -= dy * k
                dh = ah - f["h"]
                f["h"] += max(-14.0 * dt, min(14.0 * dt, dh))

    # ---------- 战斗 ----------

    def _use_player_skill(self, key):
        if self.match_phase != "play" or self.paused or self.over:
            return
        if key == "q":
            if self.atk_cd > 0.0:
                self._spawn_pop(f"冲击波冷却 {self.atk_cd:.1f}s", (1.0, 0.7, 0.4), 1.3)
                return
            self.atk_cd = ATTACK_CD
            self._cast_player_wave()
        elif key == "e":
            if self.dodge_cd > 0.0:
                self._spawn_pop(f"闪避冷却 {self.dodge_cd:.1f}s", (0.6, 1.0, 0.8), 1.3)
                return
            self.dodge_cd = DODGE_CD
            self.dodge_t = DODGE_INVULN
            self._spawn_wave_fx(self.x, self.y, self.h, (0.42, 1.0, 0.72), 0.8, 4.2, 0.4)
            self._spawn_pop("闪避 · 无敌", (0.5, 1.0, 0.75), 1.6)
            self._play(self.snd_eat)
        elif key == "r":
            if self.guard_cd > 0.0:
                self._spawn_pop(f"护盾冷却 {self.guard_cd:.1f}s", (0.55, 0.85, 1.0), 1.3)
                return
            self.guard_cd = GUARD_CD
            self.guard_t = GUARD_DURATION
            self._spawn_wave_fx(self.x, self.y, self.h, (0.38, 0.82, 1.0), 0.8, 4.6, 0.45)
            self._spawn_pop(f"护盾 · 减伤 {int(GUARD_REDUCTION * 100)}%",
                            (0.55, 0.85, 1.0), 1.6)
            self._play(self.snd_eat)

    def _cast_player_wave(self):
        w = self.p_wave
        w.update(on=True, x=self.x, y=self.y, h=self.h, trav=0.0, age=0.0)
        cp = math.cos(self.pitch)
        w["dx"] = math.cos(self.angle) * cp
        w["dy"] = math.sin(self.angle) * cp
        w["dh"] = math.sin(self.pitch)
        pos = (_w(self.x), self.h, _w(self.y))
        self.p_shock_core.position = pos
        self.p_shock_halo.position = pos
        self.p_shock_core.scale = (2.4, 1.1, 2.4)
        self.p_shock_halo.scale = (3.8, 1.5, 3.8)
        self.p_shock_core.visible = True
        self.p_shock_halo.visible = True
        self._spawn_wave_fx(self.x, self.y, self.h, (1.0, 0.55, 0.18), 0.9, 3.4, 0.3)
        self._play(self.snd_eat)

    def _update_player_wave(self, dt):
        w = self.p_wave
        if not w["on"]:
            return
        x0, y0, h0 = w["x"], w["y"], w["h"]
        step = ATTACK_SPEED * dt
        x1 = x0 + w["dx"] * step
        y1 = y0 + w["dy"] * step
        h1 = h0 + w["dh"] * step * SC
        w.update(x=x1, y=y1, h=h1)
        w["trav"] += step
        w["age"] += dt

        pts = [(self.ai_x, self.ai_y, self.ai_h)] + list(self.ai_trail)[1:]
        hit = self._segment_body_hit(x0, y0, h0, x1, y1, h1, pts)
        if hit is not None:
            self._damage_ai(self.attack_power, hit)
            self._spawn_wave_fx(hit[0], hit[1], hit[2], (1.0, 0.55, 0.18),
                                1.0, 6.4, 0.42)
            self._end_wave(w, self.p_shock_core, self.p_shock_halo)
            return
        pulse = 0.55 + 0.45 * math.sin(w["age"] * 26.0)
        self.p_shock_core.position = (_w(x1), h1, _w(y1))
        self.p_shock_halo.position = (_w(x1), h1, _w(y1))
        self.p_shock_core.scale = (2.1 + 0.5 * pulse, 1.0 + 0.22 * pulse,
                                   2.1 + 0.5 * pulse)
        self.p_shock_halo.scale = (3.4 + 0.8 * pulse, 1.4 + 0.3 * pulse,
                                   3.4 + 0.8 * pulse)
        if w["trav"] >= ATTACK_RANGE:
            self._spawn_wave_fx(x1, y1, h1, (1.0, 0.55, 0.18), 0.8, 3.8, 0.28)
            self._end_wave(w, self.p_shock_core, self.p_shock_halo)

    def _damage_ai(self, dmg, hit):
        if self.match_phase != "play" or self.over:
            return
        if self.ai_dodge_t > 0.0:
            self._spawn_pop("AI 闪避成功", (0.5, 1.0, 0.75), 1.6)
            return
        if self.ai_guard_t > 0.0:
            dmg = int(round(dmg * (1.0 - GUARD_REDUCTION)))
            self._spawn_pop(f"AI 格挡 -{dmg}", (0.55, 0.85, 1.0), 1.8)
        else:
            self._spawn_pop(f"命中 AI -{dmg}", (1.0, 0.86, 0.36), 2.0)
        self.ai_hp = max(0, self.ai_hp - dmg)
        self.ai_hit_flash = 0.24
        self._spawn_burst(hit[0], hit[1], hit[2], (1.0, 0.55, 0.30), 10)
        self._gain_xp(HIT_XP, self._anim_t)      # 玩家命中获得战斗经验
        if self.ai_hp <= 0:
            self._finish_match("player")

    def _ai_attack_check(self):
        if self.ai_atk_cd > 0.0 or self.match_phase != "play":
            return
        dx = self.x - self.ai_x
        dy = self.y - self.ai_y
        d = math.hypot(dx, dy)
        if d > ATTACK_RANGE * 0.92 or d < 40.0:
            return
        err = abs(_wrap(math.atan2(dy, dx) - self.ai_angle))
        to_pitch = math.atan2((self.h - self.ai_h) / SC, d)
        perr = abs(self.ai_pitch - to_pitch)
        tol = 0.18 + self.diff["aim"] * 0.55
        if err > tol or perr > tol * 0.95:
            return
        self._ai_cast_wave()

    def _ai_cast_wave(self):
        self.ai_atk_cd = ATTACK_CD * self.diff["atk_cd_mul"]
        w = self.a_wave
        w.update(on=True, x=self.ai_x, y=self.ai_y, h=self.ai_h, trav=0.0, age=0.0)
        cp = math.cos(self.ai_pitch)
        w["dx"] = math.cos(self.ai_angle) * cp
        w["dy"] = math.sin(self.ai_angle) * cp
        w["dh"] = math.sin(self.ai_pitch)
        pos = (_w(self.ai_x), self.ai_h, _w(self.ai_y))
        self.ai_shock_core.position = pos
        self.ai_shock_halo.position = pos
        self.ai_shock_core.scale = (2.3, 1.1, 2.3)
        self.ai_shock_halo.scale = (3.6, 1.4, 3.6)
        self.ai_shock_core.visible = True
        self.ai_shock_halo.visible = True
        self._spawn_wave_fx(self.ai_x, self.ai_y, self.ai_h, (1.0, 0.35, 0.20),
                            0.9, 3.2, 0.30)

    def _update_ai_wave(self, dt):
        w = self.a_wave
        if not w["on"]:
            return
        x0, y0, h0 = w["x"], w["y"], w["h"]
        step = ATTACK_SPEED * dt
        x1 = x0 + w["dx"] * step
        y1 = y0 + w["dy"] * step
        h1 = h0 + w["dh"] * step * SC
        w.update(x=x1, y=y1, h=h1)
        w["trav"] += step
        w["age"] += dt

        pts = [(self.x, self.y, self.h)] + list(self.trail)[1:]
        hit = self._segment_body_hit(x0, y0, h0, x1, y1, h1, pts)
        if hit is not None:
            self._damage_player(self.ai_attack, hit)
            self._spawn_wave_fx(hit[0], hit[1], hit[2], (1.0, 0.35, 0.20),
                                1.0, 6.2, 0.42)
            self._end_wave(w, self.ai_shock_core, self.ai_shock_halo)
            return
        pulse = 0.55 + 0.45 * math.sin(w["age"] * 26.0)
        self.ai_shock_core.position = (_w(x1), h1, _w(y1))
        self.ai_shock_halo.position = (_w(x1), h1, _w(y1))
        self.ai_shock_core.scale = (2.0 + 0.5 * pulse, 1.0 + 0.2 * pulse,
                                    2.0 + 0.5 * pulse)
        self.ai_shock_halo.scale = (3.2 + 0.7 * pulse, 1.3 + 0.3 * pulse,
                                    3.2 + 0.7 * pulse)
        if w["trav"] >= ATTACK_RANGE:
            self._spawn_wave_fx(x1, y1, h1, (1.0, 0.35, 0.20), 0.8, 3.6, 0.28)
            self._end_wave(w, self.ai_shock_core, self.ai_shock_halo)

    @staticmethod
    def _segment_body_hit(x0, y0, h0, x1, y1, h1, pts):
        sx, sy = x1 - x0, y1 - y0
        seg2 = sx * sx + sy * sy
        for tx, ty, th in pts:
            k = 0.0 if seg2 < 1e-6 else ((tx - x0) * sx + (ty - y0) * sy) / seg2
            k = min(1.0, max(0.0, k))
            dx = tx - (x0 + sx * k)
            dy = ty - (y0 + sy * k)
            if dx * dx + dy * dy > ATTACK_HIT_RADIUS * ATTACK_HIT_RADIUS:
                continue
            if abs((th - (h0 + (h1 - h0) * k)) / SC) > ATTACK_HIT_HEIGHT:
                continue
            return (tx, ty, th)
        return None

    @staticmethod
    def _end_wave(w, core, halo):
        w["on"] = False
        core.visible = False
        halo.visible = False

    def _damage_player(self, dmg, hit):
        if self.match_phase != "play" or self.over:
            return
        if self.dodge_t > 0.0:
            self._spawn_pop("闪避成功", (0.5, 1.0, 0.75), 1.8)
            return
        if self.guard_t > 0.0:
            dmg = int(round(dmg * (1.0 - GUARD_REDUCTION)))
            self._spawn_pop(f"格挡 -{dmg}", (0.55, 0.85, 1.0), 2.0)
        else:
            self._spawn_pop(f"-{dmg}", (1.0, 0.38, 0.30), 2.2)
        self.hp = max(0, self.hp - dmg)
        self.hit_flash = 0.30
        self._spawn_burst(hit[0], hit[1], hit[2], (1.0, 0.30, 0.24), 12)
        self._ai_gain_xp(HIT_XP)                 # AI 命中获得战斗经验
        if self.hp <= 0:
            self._finish_match("ai")

    def _ai_dodge(self):
        if self.ai_dodge_cd > 0.0:
            return
        self.ai_dodge_cd = DODGE_CD
        self.ai_dodge_t = DODGE_INVULN
        self._spawn_wave_fx(self.ai_x, self.ai_y, self.ai_h,
                            (0.42, 1.0, 0.72), 0.8, 4.0, 0.4)
        self._spawn_pop("AI 闪避", (0.5, 1.0, 0.75), 1.5)

    def _ai_guard(self):
        if self.ai_guard_cd > 0.0:
            return
        self.ai_guard_cd = GUARD_CD
        self.ai_guard_t = GUARD_DURATION
        self._spawn_wave_fx(self.ai_x, self.ai_y, self.ai_h,
                            (0.38, 0.82, 1.0), 0.8, 4.4, 0.45)
        self._spawn_pop("AI 开启护盾", (0.55, 0.85, 1.0), 1.5)

    # ---------- 结算 ----------

    def die(self, reason):
        if self.over:
            return
        if not getattr(self, "_ai_inited", False) or \
                self.match_phase not in ("countdown", "play"):
            super().die(reason)
            return
        if reason == "hp":
            self.hp = 0
            self._finish_match("ai")
            return
        # 非 HP 的碰撞不判负：弹出 / 掉头后继续
        if reason == "bounds":
            hr = SKY_HEAD_RADIUS
            self.target_angle = math.atan2(WORLD / 2 - self.y, WORLD / 2 - self.x)
            self.angle = self.target_angle
            self.x = min(max(self.x, hr + 1), WORLD - hr - 1)
            self.y = min(max(self.y, hr + 1), WORLD - hr - 1)
        else:
            self.hit_flash = 0.20
            self.pitch = max(self.pitch, 0.35)
            self.target_pitch = max(self.target_pitch, 0.35)

    def _finish_match(self, winner):
        if self.match_phase == "ending":
            return
        self.match_phase = "ending"
        self.result = winner
        self.over = True
        self.paused = True
        self.dashing = False
        self.speed_scale = 1.0
        if self.bgm:
            try:
                self.bgm.stop()
            except Exception:
                pass
        win = winner == "player"
        title = "胜利！" if win else "惜败"
        col = color.rgb(1.0, 0.85, 0.35) if win else color.rgb(1.0, 0.38, 0.32)
        self.over_title.text = title
        self.over_title.color = col
        self.over_accent.color = color.rgba(col[0], col[1], col[2], 0.80)
        self.over_border.color = color.rgba(col[0], col[1], col[2], 0.20)
        self.over_info.text = (
            f"{'击败了 ' + self.ai_nick if win else self.ai_nick + ' 耗尽了你的生命'}\n"
            f"你的 HP {self.hp}/{self.hp_max}   AI HP {self.ai_hp}/{self.ai_hp_max}\n"
            f"经验 {self.xp} : {self.ai_xp}   光环 {self.rings_passed}")
        self.pause_badge.enabled = False
        self._set_hud_visible(False)
        self.center_text.text = ""
        self._set_cursor_locked(False)
        self.over_panel.enabled = True
        self._pop_menu(self.over_card, self.over_dim)
        self._play(self.snd_eat if win else self.snd_death)
        if TEST_MODE:
            print(f"[AI] end winner={winner} hp={self.hp} ai_hp={self.ai_hp} "
                  f"xp={self.xp}/{self.ai_xp}", flush=True)

    def _show_gameover(self):
        if getattr(self, "_ai_inited", False) and \
                self.match_phase in ("countdown", "play", "ending"):
            return
        super()._show_gameover()

    # ---------- 特效 ----------

    def _reset_fx(self):
        for p in self._fx_pool:
            p["ttl"] = 0.0
            p["e"].visible = False
        for p in self._pops:
            p["ttl"] = 0.0
            p["t"].color = color.rgba(1, 0.5, 0.3, 0.0)
        for e in (self.p_shock_core, self.p_shock_halo,
                  self.ai_shock_core, self.ai_shock_halo,
                  self.guard_bubble, self.dodge_halo,
                  self.ai_guard_bubble, self.ai_dodge_halo):
            e.visible = False
        self.hit_quad.enabled = False
        self.hit_quad.color = color.rgba(0.95, 0.08, 0.08, 0.0)
        self.hit_flash = 0.0
        self.ai_hit_flash = 0.0

    def _spawn_wave_fx(self, x, y, h, rgb, s0, s1, ttl):
        self._spawn_fx(x, y, h, s0, s1, ttl, 0.5, rgb)

    def _spawn_burst(self, x, y, h, rgb, count):
        rng = self.fx_rng
        for _ in range(count):
            ang = rng.uniform(0.0, math.pi * 2.0)
            spd = rng.uniform(30.0, 110.0)
            self._spawn_fx(x, y, h, rng.uniform(0.12, 0.26), 0.0,
                           rng.uniform(0.30, 0.55), rng.uniform(0.45, 0.8),
                           rgb, vx=math.cos(ang) * spd, vy=math.sin(ang) * spd,
                           vh=rng.uniform(-2.0, 5.0))

    def _spawn_fx(self, x, y, h, s0, s1, ttl, a0, rgb,
                  vx=0.0, vy=0.0, vh=0.0):
        slot = next((p for p in self._fx_pool if p["ttl"] <= 0.0), None)
        if slot is None:
            slot = self._fx_pool[0]
        slot.update(age=0.0, ttl=ttl, x=x, y=y, h=h, vx=vx, vy=vy, vh=vh,
                    s0=s0, s1=s1, a0=a0, rgb=rgb)
        e = slot["e"]
        e.position = (_w(x), h, _w(y))
        e.scale = s0
        e.color = color.rgba(rgb[0], rgb[1], rgb[2], a0)
        e.visible = True

    def _update_fx(self, dt):
        for p in self._fx_pool:
            if p["ttl"] <= 0.0:
                continue
            p["age"] += dt
            if p["age"] >= p["ttl"]:
                p["ttl"] = 0.0
                p["e"].visible = False
                continue
            k = p["age"] / p["ttl"]
            e = p["e"]
            if p["s1"] > p["s0"]:           # 扩散波
                e.scale = p["s0"] + (p["s1"] - p["s0"]) * k
                a = p["a0"] * (1.0 - k) * (1.0 - k)
            else:                            # 火花
                p["x"] += p["vx"] * dt
                p["y"] += p["vy"] * dt
                p["h"] += p["vh"] * dt
                p["vh"] -= 4.0 * dt
                e.position = (_w(p["x"]), p["h"], _w(p["y"]))
                e.scale = p["s0"] * (1.0 - k) + 0.04
                a = p["a0"] * (1.0 - k)
            e.color = color.rgba(p["rgb"][0], p["rgb"][1], p["rgb"][2], a)

    def _spawn_pop(self, text, rgb, scale):
        p = self._pops[self._pop_i]
        self._pop_i = (self._pop_i + 1) % len(self._pops)
        p.update(age=0.0, ttl=0.95, rgb=rgb,
                 x0=self.fx_rng.uniform(-0.07, 0.07),
                 y0=self.fx_rng.uniform(0.02, 0.07))
        p["t"].text = text
        p["t"].scale = scale
        p["t"].position = (p["x0"], p["y0"])
        p["t"].color = color.rgba(rgb[0], rgb[1], rgb[2], 1.0)

    def _update_pops(self, dt):
        for p in self._pops:
            if p["ttl"] <= 0.0:
                continue
            p["age"] += dt
            if p["age"] >= p["ttl"]:
                p["ttl"] = 0.0
                p["t"].color = color.rgba(1.0, 0.5, 0.3, 0.0)
                continue
            k = p["age"] / p["ttl"]
            p["t"].position = (p["x0"], p["y0"] + 0.09 * k)
            rgb = p["rgb"]
            p["t"].color = color.rgba(rgb[0], rgb[1], rgb[2], 1.0 - k)

    # ---------- 帧更新 ----------

    def update(self):
        if not self._ai_inited:
            super().update()
            return
        dt = min(0.033, ursina_time.dt)
        rdt = min(0.25, ursina_time.dt)
        self._ai_tick(rdt)
        super().update()
        self._update_ai_visual(dt)
        self._update_ai_hud()

        if TEST_MODE and _AI_SHOT and not getattr(self, "_shot_done", False) \
                and self.match_phase in ("play", "ending") \
                and self._anim_t >= _AI_SHOT_AT:
            self._shot_done = True
            img = s3._APP.win.getScreenshot() if s3._APP else None
            if img:
                from panda3d.core import Filename
                img.write(Filename.fromOsSpecific(_AI_SHOT))
            print(f"[AI] shot {_AI_SHOT} phase={self.match_phase} "
                  f"hp={self.hp}/{self.hp_max} ai_hp={self.ai_hp}/{self.ai_hp_max} "
                  f"state={self.ai_state} xp={self.xp}/{self.ai_xp}", flush=True)
        if _AI_TEST_EXIT > 0 and self._anim_t > _AI_TEST_EXIT:
            self._exit(0)

    def _update_ai_visual(self, dt):
        n = len(self.ai_trail)
        if n < 2:
            self.ai_head.visible = False
            for e in self.ai_seg_ents:
                e.visible = False
            self._ai_vis_n = 0
            return
        bob = math.sin(self._anim_t * 3.0) * 0.1
        self.ai_head.setPos(_w(self.ai_x), self.ai_h + bob, _w(self.ai_y))
        self.ai_head.rotation_y = -math.degrees(self.ai_angle)
        self.ai_head.rotation_z = math.degrees(self.ai_pitch)
        if self.ai_hit_flash > 0.0:
            k = min(1.0, self.ai_hit_flash / 0.24)
            self.ai_head.color = color.rgb(1.0, 0.30 + 0.55 * (1.0 - k),
                                           0.28 + 0.55 * (1.0 - k))
        else:
            self.ai_head.color = color.rgb(*self._ai_head_rgb)
        self.ai_head.visible = True

        prev_n = self._ai_vis_n
        if n != prev_n:
            self._recolor_ai_segments(n)
        camx, camy, camz = camera.position
        hide2 = NEAR_HIDE * NEAR_HIDE
        for i in range(n):
            tx, ty, th = self.ai_trail[i]
            sx, sz = _w(tx), _w(ty)
            sy = th + math.sin(self._anim_t * 3.0 - i * 0.3) * 0.1
            e = self.ai_seg_ents[i]
            # AI 身体段同样贴地，避免插入山体
            rad = e.scale_x * 0.5
            if sy - rad < TERRAIN_MAX:
                top = self._column_top(tx, ty)
                if top is not None:
                    floor = top + rad - 0.02
                    if sy < floor:
                        sy = floor
            e.setPos(sx, sy, sz)
            dx, dy, dz = sx - camx, sy - camy, sz - camz
            e.visible = (dx * dx + dy * dy + dz * dz) > hide2
        for i in range(n, max(n, prev_n)):
            self.ai_seg_ents[i].visible = False
        self._ai_vis_n = n

        self.guard_bubble.visible = self.guard_t > 0.0
        self.dodge_halo.visible = self.dodge_t > 0.0
        self.ai_guard_bubble.visible = self.ai_guard_t > 0.0
        self.ai_dodge_halo.visible = self.ai_dodge_t > 0.0

    def _update_ai_hud(self):
        if not self._ai_hud_ready:
            return
        # 玩家血量
        key = (self.hp, self.hp_max, self.attack_power)
        if key != self._hp_ui_key:
            self._hp_ui_key = key
            frac = self.hp / max(1, self.hp_max)
            self.hp_bar.scale = (max(0.001, 0.32 * frac), 0.017)
            self.hp_bar.position = (-0.60 + 0.16 * frac, -0.378)
            self.hp_bar.color = (color.rgb(0.34, 0.95, 0.46) if frac > 0.6 else
                                 color.rgb(1.0, 0.78, 0.25) if frac > 0.3 else
                                 color.rgb(1.0, 0.32, 0.28))
            self.hp_text.text = f"HP {self.hp} / {self.hp_max}   攻击 {self.attack_power}"

        # AI 血量
        akey = (self.ai_hp, self.ai_hp_max, self.ai_level)
        if akey != self._ai_hp_ui_key:
            self._ai_hp_ui_key = akey
            frac = self.ai_hp / max(1, self.ai_hp_max)
            self.ai_hp_bar.scale = (max(0.001, 0.34 * frac), 0.011)
            self.ai_hp_bar.position = (-0.17 + 0.17 * frac, 0.404)
            self.ai_name_text.text = f"{self.ai_nick}   Lv {self.ai_level}"

        # 技能冷却
        for slot, cd in zip(self.skill_slots,
                            (self.atk_cd, self.dodge_cd, self.guard_cd)):
            txt = f"{cd:.1f}" if cd > 0.05 else ""
            if txt != slot["last"]:
                slot["last"] = txt
                slot["cd"].text = txt

        self.ai_state_text.text = AI_STATE_TEXT.get(self.ai_state, "")
        hud_on = not (self.over or self.pause_menu or self.dialog_panel.enabled)
        self.center_text.enabled = hud_on

        # 小地图 AI 点位
        if hud_on and self.match_phase in ("countdown", "play"):
            self.mm_ai.enabled = True
            self.mm_ai.position = (0.5 + (self.ai_x / WORLD - 0.5) * 0.24,
                                   0.33 + (0.5 - self.ai_y / WORLD) * (0.24 * 9 / 7))
        else:
            self.mm_ai.enabled = False

        # 受击红闪
        if self.hit_flash > 0.0 and hud_on:
            self.hit_quad.enabled = True
            self.hit_quad.color = color.rgba(0.95, 0.08, 0.08,
                                             min(0.36, self.hit_flash * 1.1))
        else:
            self.hit_quad.enabled = False

    # ---------- 输入 ----------

    def resume(self):
        super().resume()
        if self.match_phase == "countdown":
            self.paused = True

    def input(self, key):
        if key in ("q", "e", "r"):
            self._use_player_skill(key)
            return
        if self.match_phase == "countdown" and key == "p":
            return
        super().input(key)

    def _exit(self, code):
        # 人机对战经验不计入 3D 单机最高分
        save_settings()
        rm = getattr(self, "raw_mouse", None)
        if rm is not None:
            rm.unlock()      # 恢复系统光标 + 还原指针加速（覆写父类 _exit 时别漏掉）
        os._exit(code)


# ---------------- 入口 ----------------

def _parse_diff(argv):
    if "--ai-diff" in argv:
        i = argv.index("--ai-diff")
        if i + 1 < len(argv):
            return argv[i + 1]
    for a in argv[1:]:
        if a in AI_DIFFICULTY:
            return a
    return "normal"


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    diff = _parse_diff(argv)
    load_settings()
    loadPrcFileData("", "window-title 天空贪吃蛇 3D · 人机对战")
    if TEST_MODE:
        loadPrcFileData("", "window-type offscreen")
    if os.environ.get("SKY3D_SMALL") == "1":
        size, full = (320, 240), False
    elif TEST_MODE:
        size, full = (900, 700), False
    else:
        size, full = None, True

    app = Ursina(size=size, title="天空贪吃蛇 3D 人机对战", vsync=True,
                 fullscreen=full)
    global _APP
    _APP = app
    s3._APP = app
    if full:
        s3._set_fullscreen(True)
    from pathlib import Path
    if getattr(sys, "frozen", False):
        application.asset_folder = Path(sys._MEIPASS)
    else:
        application.asset_folder = Path(os.path.abspath(__file__)).parent
    window.exit_button.visible = False
    window.fps_counter.enabled = False
    window.entity_counter.enabled = False
    window.collider_counter.enabled = False
    camera.clip_plane_far = 3000
    if TEST_MODE:
        window.borderless = False

    game = Sky3DAIGame(difficulty=diff, test_mode=TEST_MODE)
    s3.make_fps_display()

    def frame():
        game.steer()

    Entity(update=lambda: frame(), eternal=True)
    app.run()


if __name__ == "__main__":
    main()
