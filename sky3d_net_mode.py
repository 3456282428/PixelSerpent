"""天空贪吃蛇 3D 联机对战（真三维天空世界）。

主机（--net-host）：创建房间后立即进入 3D 世界自由练习，HUD 显示房间码；
好友通过房间码加入后，双方自动重置并开始正式对决。
客户端（--net-join IP 端口）：连接主机，等待主机开局。

同步策略（局域网实时对战）：
  • 双方使用相同的世界随机种子，地形/陨石/果实布局一致；
  • 各自在本地模拟自己的蛇（操控零延迟），以 15Hz 交换位姿、轨迹与经验；
  • 开局 100 HP：Q 冲击波（向前发射光波，命中扣血）、E 闪避（短暂无敌+冲刺）、
    R 护盾（减伤 60%）；命中/受击由受击方权威结算并即时回传同步；
  • 自由追逐对战：无时间限制、无碰撞坠毁，按住 S/↓ 减速拉扯，Shift 冲刺追击；
    唯一结束条件为任意一方 HP 归零；
  • 战斗与拾取经验都会升级，升级按成长系数提升攻击力与最大生命值；
  • 主机负责判定胜负，客户端只做镜像展示。

退出码：0=返回菜单  2=端口被占用  3=无法连接  42=退出整个游戏
"""
import os
import sys
import math
import random
from collections import deque

from ursina import Ursina, Entity, Text, camera, window, color, application, \
    held_keys
from ursina import time as ursina_time
from panda3d.core import loadPrcFileData, TransparencyAttrib

import sky3d_mode as s3
from sky3d_mode import (Sky3DGame, WORLD, SC, NEAR_HIDE, TERRAIN_MAX, ZONE_DPS,
                        _w, _additive, _unlit)
from config import SKY_HEAD_RADIUS, SKY_TRAIL_SPACING, SKY_MAX_LENGTH, SKINS, \
    SETTINGS, NET_PORT, SKY_XP_PER_LEVEL
from network import NetHost, NetClient, get_local_ip, make_room_code
from utils import load_settings

NET_SEED = 20261105             # 双方共用世界种子，保证地图完全一致
NET_COUNTDOWN = 3.0             # 开局倒计时
NET_SEND_INTERVAL = 0.066       # 位姿广播间隔（约 15Hz）
NET_TRAIL_MAX = 260             # 单次同步的最大轨迹点数

# ----- 联机战斗数值（血量 / 技能） -----
HP_MAX = 100                    # 血量上限
ATTACK_DAMAGE = 26              # 冲击波基础伤害（默认 4 次命中击毁）
ATTACK_RANGE = 320.0            # 光波最大飞行距离（像素）
ATTACK_SPEED = 900.0            # 光波飞行速度（像素/秒）
ATTACK_HIT_RADIUS = 34.0        # 光波水平命中半径（像素）
ATTACK_HIT_HEIGHT = 45.0        # 光波垂直命中容差（像素）
ATTACK_CD = 6.0                 # 冲击波冷却（秒）
ATTACK_ANIM = 0.42              # 冲击波命中特效时长
DODGE_CD = 8.0                  # 闪避冷却（秒）
DODGE_INVULN = 1.0              # 闪避无敌时长（秒）
DODGE_SPEED = 2.3               # 闪避期间的速度倍率
GUARD_CD = 12.0                 # 护盾冷却（秒）
GUARD_DURATION = 4.0            # 护盾持续（秒）
GUARD_REDUCTION = 0.6           # 护盾减伤比例（60%）
BRAKE_SPEED = 0.55              # 按住 S/↓ 时的减速倍率（拉扯/迂回）
# ----- 等级成长：升级同步提升基础伤害与最大生命 -----
HP_PER_LEVEL = 10               # 每级最大生命提升
DAMAGE_PER_LEVEL = 3            # 每级基础伤害提升
HIT_XP = 4                      # 每次命中获得的战斗经验
ATTACK_ANIM_COLOR = (1.0, 0.55, 0.18)
DODGE_ANIM_COLOR = (0.42, 1.0, 0.72)
GUARD_ANIM_COLOR = (0.38, 0.82, 1.0)

TEST_MODE = os.environ.get("SKY3D_TEST") == "1"
_NET_TEST_EXIT = float(os.environ.get("SKY3D_NET_TEST_EXIT", "0") or 0)
_NET_SHOT = os.environ.get("SKY3D_NET_SHOT", "")
_NET_SHOT_AT = float(os.environ.get("SKY3D_NET_SHOT_AT", "6") or 6)
_NET_TEST_LIVE = os.environ.get("SKY3D_NET_TEST_LIVE") == "1"
_NET_TEST_COMBAT = os.environ.get("SKY3D_NET_TEST_COMBAT") == "1"
_NET_TEST_HP0 = int(os.environ.get("SKY3D_NET_TEST_HP_START", "0") or 0)

_REASON_TEXT = {
    "hp": "血量被耗尽",
    "leave": "对手离开了对战",
}


def _log(msg):
    """窗口化 exe 中没有控制台时也能安全输出"""
    try:
        print(msg, flush=True)
    except Exception:
        pass


class Sky3DNetGame(Sky3DGame):
    """3D 天空联机对战（主机/客户端共用，双方本地模拟自己的蛇）"""

    def __init__(self, conn, is_host, room_code="", nickname=None, skin=None,
                 test_mode=False):
        self._net_inited = False
        self.conn = conn
        self.is_host = is_host
        self.room_code = room_code or ""
        self.my_nick = (nickname or SETTINGS.get("nickname") or "玩家")[:10]
        self.my_skin = (skin if isinstance(skin, int) else SETTINGS.get("skin", 0)) \
            % len(SKINS)
        self.remote_nick = "对手"
        self.remote_skin = 0
        # 成长属性先按 1 级初始化，reset 后由 _reset_net_state 重建
        self.hp_max, self.attack_power = self._level_stats(1)
        super().__init__(test_mode=test_mode, seed=NET_SEED)
        if _NET_TEST_LIVE:
            self.invincible = False

        self._build_remote_snake()
        self._build_net_hud()
        self._build_combat_ui()
        self._net_inited = True
        self.reset()
        if TEST_MODE:
            print(f"[NET] {('host' if is_host else 'client')} ready "
                  f"room={self.room_code!r}", flush=True)

        # 客户端连上后立即自报昵称/皮肤
        if not self.is_host:
            self.conn.send({"t": "hello", "nick": self.my_nick, "skin": self.my_skin})

    # ---------- 初始化 ----------

    def _build_remote_snake(self):
        """对手蛇的渲染实体（复用与玩家蛇一致的球体链外观）"""
        max_seg = max(8, int(SKY_MAX_LENGTH / SKY_TRAIL_SPACING))
        self.r_seg_ents = [Entity(model="sphere", scale=1.0, visible=False)
                           for _ in range(max_seg)]
        self.r_head = Entity(model="sphere", scale=SKY_HEAD_RADIUS * SC * 2.2,
                             color=color.rgb(0.35, 0.95, 0.45), visible=False)
        for dz in (0.30, -0.30):
            Entity(parent=self.r_head, model="sphere", scale=0.2,
                   position=(0.26, 0.2, dz), color=color.white)
            Entity(parent=self.r_head, model="sphere", scale=0.1,
                   position=(0.40, 0.2, dz * 1.07),
                   color=color.rgb(0.08, 0.08, 0.16))
        self.r_skin = SKINS[0]
        self._r_vis_n = -1
        self._apply_remote_skin()

    def _apply_remote_skin(self):
        idx = self.remote_skin % len(SKINS)
        # 双方选了同一皮肤时，把对手换成对比色，避免分不清敌我
        if idx == self.my_skin % len(SKINS) and len(SKINS) > 1:
            idx = (idx + 2) % len(SKINS)
        skin = SKINS[idx]
        self.r_skin = skin
        head = skin["head"]
        self.r_head.color = color.rgb(head[0] / 255, head[1] / 255, head[2] / 255)
        self._r_vis_n = -1

    def _build_net_hud(self):
        kw = {"font": self.font} if self.font else {}
        self.hud_hint.text = ("鼠标转向 · Shift 冲刺追击 · S/↓ 减速拉扯 · "
                              "Q 冲击波 · E 闪避 · R 护盾 · ESC 菜单")
        self.net_top = Text(text="", position=(0, 0.445), origin=(0, 0),
                            scale=1.25, color=color.rgba(0.85, 0.97, 1.0, 1.0), **kw)
        self.net_center = Text(text="", position=(0, 0.03), origin=(0, 0),
                               scale=3.2, color=color.rgba(0.4, 0.95, 1.0, 1.0), **kw)
        self.mm_remote = Entity(parent=camera.ui, model="quad",
                                color=color.rgb(1.0, 0.55, 0.32),
                                scale=(0.014, 0.014), enabled=False)
        self._hud_key = None

    def _set_hud_visible(self, visible):
        super()._set_hud_visible(visible)
        if not hasattr(self, "net_top"):
            return
        if not visible:
            extra = [self.net_top, self.net_center, self.mm_remote,
                     self.combat_root, self.remote_hp_root, self.hit_quad]
            extra += [p["t"] for p in self._pops]
            for e in extra:
                e.enabled = False
            return
        self.net_top.enabled = True
        self.net_center.enabled = True
        remote_alive = getattr(self, "remote_alive", False)
        self.mm_remote.enabled = remote_alive
        self.combat_root.enabled = self._net_inited
        self.remote_hp_root.enabled = remote_alive
        self.hit_quad.enabled = False
        for p in self._pops:
            p["t"].enabled = True

    def _build_combat_ui(self):
        """HP 条 / 技能栏 / 状态标识 / 受击闪屏 / 伤害数字 / 3D 技能特效"""
        kw = {"font": self.font} if self.font else {}
        self.combat_root = Entity(parent=camera.ui, enabled=False)
        # 自己：血量条（左下）
        self.hp_bar_bg = Entity(parent=self.combat_root, model="quad",
                                color=color.rgba(0.06, 0.10, 0.20, 0.85),
                                position=(-0.44, -0.382), scale=(0.32, 0.024))
        self.hp_bar = Entity(parent=self.combat_root, model="quad",
                             color=color.rgb(0.34, 0.95, 0.46),
                             position=(-0.60, -0.382), scale=(0.32, 0.017))
        self.hp_text = Text(parent=self.combat_root, text=f"HP {HP_MAX} / {HP_MAX}",
                            position=(-0.44, -0.354), origin=(0, 0), scale=0.9,
                            color=color.rgb(0.86, 0.98, 1.0), **kw)

        # 技能栏（底部居中）：Q 冲击波 / E 闪避 / R 护盾
        self.skill_slots = []
        for i, (key, name, col) in enumerate((
                ("q", "冲击波", (1.0, 0.58, 0.22)),
                ("e", "闪避", (0.42, 1.0, 0.72)),
                ("r", "护盾", (0.36, 0.82, 1.0)))):
            x = -0.10 + i * 0.10
            root = Entity(parent=self.combat_root, position=(x, -0.408))
            bg = Entity(parent=root, model="quad",
                        color=color.rgba(0.06, 0.10, 0.20, 0.86),
                        scale=(0.086, 0.064))
            overlay = Entity(parent=root, model="quad",
                             color=color.rgba(0.02, 0.03, 0.08, 0.62),
                             scale=(0.086, 0.064), enabled=False)
            keytxt = Text(parent=root, text=key.upper(), position=(-0.028, 0.017),
                          origin=(0, 0), scale=1.0, color=color.rgb(*col), **kw)
            nametxt = Text(parent=root, text=name, position=(0.004, -0.020),
                           origin=(0, 0), scale=0.62,
                           color=color.rgba(0.82, 0.90, 1.0, 0.9), **kw)
            cdtxt = Text(parent=root, text="", position=(0.034, 0.015),
                         origin=(1, 0), scale=0.85,
                         color=color.rgba(1.0, 0.72, 0.35, 1.0), **kw)
            self.skill_slots.append({"key": key, "bg": bg, "overlay": overlay,
                                     "cd": cdtxt, "name": nametxt, "col": col})

        # 状态 buff 标识（闪避 / 护盾剩余时间）
        self.net_buff_text = Text(parent=self.combat_root, text="",
                                  position=(0, -0.352), origin=(0, 0), scale=0.92,
                                  color=color.rgba(0.55, 1.0, 0.8, 1.0), **kw)

        # 对手血量条（顶部中央，位于比分下方）
        self.remote_hp_root = Entity(parent=camera.ui, enabled=False)
        Entity(parent=self.remote_hp_root, model="quad",
               color=color.rgba(0.08, 0.08, 0.16, 0.7),
               position=(0, 0.404), scale=(0.28, 0.014))
        self.remote_hp_bar = Entity(parent=self.remote_hp_root, model="quad",
                                    color=color.rgb(1.0, 0.45, 0.36),
                                    position=(-0.14, 0.404), scale=(0.28, 0.010))
        self.remote_hp_text = Text(parent=self.remote_hp_root, text="",
                                   position=(0.195, 0.404), origin=(0, 0), scale=0.66,
                                   color=color.rgba(1.0, 0.78, 0.62, 0.95), **kw)

        # 受击全屏红闪
        self.hit_quad = Entity(parent=camera.ui, model="quad", scale=(2, 2),
                               color=color.rgba(0.95, 0.08, 0.08, 0.0),
                               enabled=False)
        self.hit_quad.setTransparency(TransparencyAttrib.MAlpha)

        # 防御护盾泡 / 闪避状态光环（挂在本机与对手蛇头下）
        self.guard_bubble = Entity(parent=self.head, model="sphere", scale=1.55,
                                   color=color.rgba(0.38, 0.82, 1.0, 0.20),
                                   visible=False)
        _unlit(self.guard_bubble, depth_write=False)
        self.dodge_halo = Entity(parent=self.head, model="sphere", scale=1.35,
                                 color=color.rgba(0.45, 1.0, 0.72, 0.16),
                                 visible=False)
        _unlit(self.dodge_halo, depth_write=False)
        self.r_guard_bubble = Entity(parent=self.r_head, model="sphere", scale=1.55,
                                     color=color.rgba(0.38, 0.82, 1.0, 0.20),
                                     visible=False)
        _unlit(self.r_guard_bubble, depth_write=False)
        self.r_dodge_halo = Entity(parent=self.r_head, model="sphere", scale=1.35,
                                   color=color.rgba(0.45, 1.0, 0.72, 0.16),
                                   visible=False)
        _unlit(self.r_dodge_halo, depth_write=False)

        # 3D 特效池：冲击波 / 命中火花 / 闪避残影
        self._fx_pool = []
        for _ in range(40):
            e = Entity(model="sphere", color=color.rgba(1, 1, 1, 0.5), visible=False)
            _additive(e)
            self._fx_pool.append({"e": e, "kind": "wave", "age": 0.0, "ttl": 0.0,
                                  "x": 0.0, "y": 0.0, "h": 0.0,
                                  "vx": 0.0, "vy": 0.0, "vh": 0.0,
                                  "s0": 1.0, "s1": 1.0, "a0": 0.5,
                                  "rgb": (1.0, 1.0, 1.0)})
        # 冲击波弹体：向前飞行的光波（发光核心 + 外层光晕）
        self.shock_core = Entity(model="sphere",
                                 color=color.rgba(1.0, 0.66, 0.22, 0.88),
                                 visible=False)
        _additive(self.shock_core)
        _unlit(self.shock_core, depth_write=False)
        self.shock_halo = Entity(model="sphere",
                                 color=color.rgba(1.0, 0.46, 0.12, 0.32),
                                 visible=False)
        _additive(self.shock_halo)
        _unlit(self.shock_halo, depth_write=False)
        # 伤害数字池
        self._pops = []
        for _ in range(6):
            t = Text(parent=camera.ui, text="", position=(0, 0.06),
                     origin=(0, 0), scale=2.0, color=color.rgba(1, 0.6, 0.3, 0.0), **kw)
            self._pops.append({"t": t, "age": 0.0, "ttl": 0.0,
                               "x0": 0.0, "y0": 0.0})
        self._pop_i = 0
        self.fx_rng = random.Random(424242)     # 特效专用随机源（不影响世界种子同步）
        self._hit_flash_cap = 0.55
        self._skill_ui_key = None
        self._hp_ui_key = None
        self._remote_hp_key = None
        self._buff_key = None
        self._ghost_step = 0.0
        self.guard_t = 0.0
        self.dodge_t = 0.0
        self.remote_guard = 0.0
        self.remote_dodge = 0.0

    # ---------- 状态 ----------

    def reset(self):
        super().reset()
        if not self._net_inited:
            return
        self._reset_net_state()

    def _reset_net_state(self):
        self.net_phase = "wait" if self.is_host else "connect"
        self.net_countdown_left = 0.0
        self.net_clock = 0.0
        self.net_result = None
        self.remote_alive = False
        self.remote_seen = False
        self.remote_xp = 0
        self.remote_angle = 0.0
        self.remote_pitch = 0.0
        self.remote_trail = deque()
        self.remote_target = (0.0, 0.0, 0.0)
        self.remote_head_disp = [0.0, 0.0, 0.0]
        self._last_send_t = -1.0
        self._center_msg = ""
        self._center_hold = 0.0
        self._hud_key = None
        self._r_vis_n = -1
        self._pose_in = 0
        self._shot_done = False
        self.net_result = None
        # 战斗状态：血量 / 技能冷却与生效计时 / 同步来的对 Buff
        self.hp_max, self.attack_power = self._level_stats(1)
        hp0 = _NET_TEST_HP0 if _NET_TEST_HP0 > 0 else self.hp_max
        self.hp = hp0
        self.remote_level = 1
        self.remote_hp_max = self.hp_max
        self.remote_attack = self.attack_power
        self.remote_hp = hp0
        self.atk_cd = 0.0
        # 光波弹体状态（位置为像素坐标，h 为三维高度）
        self._shot_active = False
        self._shot_traveled = 0.0
        self._shot_age = 0.0
        self._shot_x = self._shot_y = 0.0
        self._shot_h = 0.0
        self._shot_dx = 1.0
        self._shot_dy = 0.0
        self._shot_dh = 0.0
        self.dodge_cd = 0.0
        self.guard_cd = 0.0
        self.dodge_t = 0.0
        self.guard_t = 0.0
        self.remote_dodge = 0.0
        self.remote_guard = 0.0
        self.speed_scale = 1.0
        self.hit_flash = 0.0
        self._ghost_step = 0.0
        self._reset_combat_fx()
        self._skill_ui_key = None
        self._hp_ui_key = None
        self._remote_hp_key = None
        self._buff_key = None
        self._test_plan = self._combat_test_plan()
        self._place_snake("p1" if self.is_host else "p2")
        self._update_hud_text()
        self.paused = not self.is_host
        if hasattr(self, "mm_remote"):
            self.mm_remote.enabled = False
        self._enable_safe_zone()

    # ---------- 安全区伤害 ----------

    def _zone_hurt(self, dmg):
        if (not self._net_inited or self.net_phase != "play"
                or self.over or self.pause_menu):
            return
        self.hp = max(0, self.hp - dmg)
        self.hit_flash = max(self.hit_flash, 0.10)
        self._spawn_pop(f"-{dmg}", (1.0, 0.34, 0.28), 1.6)
        if self.hp <= 0:
            self.die("hp")

    def _place_snake(self, side):
        """固定出生点：主机在左侧向东，客户端在右侧向西"""
        c = WORLD / 2.0
        off = 150.0 if _NET_TEST_COMBAT else 300.0
        x = c - off if side == "p1" else c + off
        y = c
        ang = 0.0 if side == "p1" else math.pi
        self.x = self.px = x
        self.y = self.py = y
        self.h = self.ph = 3.2
        self.angle = self.target_angle = ang
        self.pitch = self.target_pitch = 0.0
        self.trail.clear()
        for i in range(34):
            self.trail.append((x - math.cos(ang) * SKY_TRAIL_SPACING * i,
                               y - math.sin(ang) * SKY_TRAIL_SPACING * i, self.h))
        self._vis_n = -1
        self._update_camera(1.0)

    # ---------- 等级成长 ----------

    @staticmethod
    def _level_stats(level):
        """等级 → (最大生命, 基础伤害)，成长系数固定且可预期"""
        lv = max(1, int(level))
        return (HP_MAX + (lv - 1) * HP_PER_LEVEL,
                ATTACK_DAMAGE + (lv - 1) * DAMAGE_PER_LEVEL)

    def _gain_xp(self, amount, t):
        """经验入口：升级时同步提升最大生命与基础伤害"""
        if amount <= 0 or self.over:
            return
        old = self.level
        super()._gain_xp(amount, t)
        if self.level > old:
            self._apply_level_growth()

    def _apply_level_growth(self):
        old_max = self.hp_max
        self.hp_max, self.attack_power = self._level_stats(self.level)
        gain = max(0, self.hp_max - old_max)
        self.hp = min(self.hp_max, self.hp + gain)   # 升级即刻回复增量生命
        c = (1.0, 0.86, 0.35)
        self._spawn_wave(self.x, self.y, self.h, c, 0.6, 5.2, 0.5, 0.5)
        self._spawn_burst(self.x, self.y, self.h, c, 16)
        self._spawn_pop(f"升级 Lv {self.level} · 攻击 {self.attack_power} · "
                        f"生命上限 {self.hp_max}", c, 2.0)
        self._update_hud_text()
        self._play(self.snd_eat)
        if TEST_MODE:
            print(f"[NET] {'host' if self.is_host else 'client'} level up "
                  f"lv={self.level} hp={self.hp}/{self.hp_max} "
                  f"atk={self.attack_power}", flush=True)

    def _update_hud_text(self):
        super()._update_hud_text()
        if getattr(self, "_net_inited", False):
            self.hud_lv.text = (f"Lv {self.level}   经验 {self.xp}   "
                                f"攻击 {self.attack_power}")

    # ---------- 网络 ----------

    def _poll_net(self):
        if self.conn is None:
            return
        if self.is_host and self.net_phase == "wait" and not self.conn.alive:
            try:
                self.conn.try_accept()
            except Exception:
                pass
        if self.conn.alive:
            for msg in self.conn.poll():
                self._handle_msg(msg)
            return

        # 已建立连接后断开
        if self.is_host and self.net_phase in ("countdown", "play"):
            self._finish_match("p1", "leave")
        elif not self.is_host:
            if self.net_phase == "connect":
                self._client_end(None, "leave")
            elif self.net_phase in ("countdown", "play"):
                self._client_end(None, "leave")

    def _handle_msg(self, msg):
        kind = msg.get("t")
        if kind == "hello":
            if not self.is_host or self.net_phase != "wait":
                return
            self.remote_nick = str(msg.get("nick") or "对手")[:10]
            try:
                self.remote_skin = int(msg.get("skin", 0))
            except Exception:
                self.remote_skin = 0
            self._apply_remote_skin()
            self._begin_match()
        elif kind == "start":
            if not self.is_host:
                self._begin_client(msg)
        elif kind == "pose":
            self._apply_pose(msg)
        elif kind == "hit":
            self._receive_hit(msg)
        elif kind == "hp":
            self._apply_remote_hp(msg)
        elif kind == "die":
            if self.is_host:
                self._finish_match("p1", str(msg.get("reason") or "hp"))
        elif kind == "end":
            if not self.is_host:
                self._client_end(msg.get("winner"), str(msg.get("reason") or "hp"))
        elif kind == "bye":
            if self.is_host:
                self._finish_match("p1", "leave")
            else:
                self._client_end(None, "leave")

    def _begin_match(self):
        """主机：好友已加入 → 重置并开始倒计时"""
        nick, skin = self.remote_nick, self.remote_skin
        self.reset()
        self.remote_nick, self.remote_skin = nick, skin
        self._apply_remote_skin()
        self.remote_alive = True
        self.net_phase = "countdown"
        self.net_countdown_left = NET_COUNTDOWN
        self.paused = True
        self._set_center("对手已加入", 1.2)
        self.conn.send({"t": "start", "nick": self.my_nick, "skin": self.my_skin})
        if TEST_MODE:
            print("[NET] host begin match", flush=True)

    def _begin_client(self, msg):
        """客户端：收到开局指令 → 重置并同步倒计时"""
        nick = str(msg.get("nick") or "对手")[:10]
        try:
            skin = int(msg.get("skin", 0))
        except Exception:
            skin = 0
        self.reset()
        self.remote_nick, self.remote_skin = nick, skin
        self._apply_remote_skin()
        self.remote_alive = True
        self.net_phase = "countdown"
        self.net_countdown_left = NET_COUNTDOWN
        self.paused = True
        self._set_center("对手已就位", 1.2)
        if TEST_MODE:
            print("[NET] client begin match", flush=True)

    def _apply_pose(self, msg):
        if self.is_host and self.net_phase == "wait":
            return
        try:
            x = float(msg.get("x", 0.0))
            y = float(msg.get("y", 0.0))
            h = float(msg.get("h", 2.0))
        except Exception:
            return
        self.remote_xp = int(msg.get("xp", 0))
        try:
            self.remote_level = max(1, int(msg.get("lv", self.remote_level)))
            self.remote_hp_max = max(1, int(msg.get("mhp", self.remote_hp_max)))
            self.remote_attack = max(1, int(msg.get("atk", self.remote_attack)))
            self.remote_hp = max(0, min(self.remote_hp_max,
                                       int(msg.get("hp", self.remote_hp))))
            self.remote_dodge = max(0.0, float(msg.get("dgr", 0.0)))
            self.remote_guard = max(0.0, float(msg.get("grd", 0.0)))
        except Exception:
            pass
        self.remote_angle = float(msg.get("a", 0.0))
        self.remote_pitch = float(msg.get("p", 0.0))
        self.remote_alive = bool(msg.get("alive", True))
        pts = deque()
        for p in msg.get("tr") or []:
            try:
                pts.append((float(p[0]), float(p[1]), float(p[2])))
            except Exception:
                continue
            if len(pts) >= NET_TRAIL_MAX:
                break
        self.remote_trail = pts
        self.remote_target = (x, y, h)
        if not self.remote_seen:
            self.remote_head_disp = [x, y, h]
        else:
            d = self.remote_head_disp
            if math.hypot(x - d[0], y - d[1]) > 400:      # 出生/重置瞬移
                self.remote_head_disp = [x, y, h]
        self.remote_seen = True
        self._pose_in += 1

    def _send_pose(self):
        t = self._anim_t
        if t - self._last_send_t < NET_SEND_INTERVAL:
            return
        self._last_send_t = t
        tr = [[int(px), int(py), round(ph, 1)]
              for px, py, ph in list(self.trail)[:NET_TRAIL_MAX]]
        self.conn.send({"t": "pose",
                        "x": round(self.x, 1), "y": round(self.y, 1),
                        "h": round(self.h, 2), "a": round(self.angle, 3),
                        "p": round(self.pitch, 3), "xp": int(self.xp),
                        "lv": int(self.level), "mhp": int(self.hp_max),
                        "atk": int(self.attack_power),
                        "hp": int(self.hp), "dgr": round(self.dodge_t, 2),
                        "grd": round(self.guard_t, 2),
                        "alive": not self.over, "tr": tr})

    # ---------- 战斗：血量 / 技能 / 特效 ----------

    def _combat_test_plan(self):
        """无头联调：按时间轴自动放技能（仅 SKY3D_NET_TEST_COMBAT=1）"""
        if not _NET_TEST_COMBAT:
            return []
        if self.is_host:
            return [(0.5, "q"), (2.0, "x"), (6.6, "q"), (12.7, "q"),
                    (18.8, "q")]
        return [(6.5, "e"), (12.0, "r")]

    def _reset_combat_fx(self):
        for p in self._fx_pool:
            p["ttl"] = 0.0
            p["e"].visible = False
        for p in self._pops:
            p["ttl"] = 0.0
            p["age"] = 0.0
            p["t"].color = color.rgba(1, 0.5, 0.3, 0.0)
        self.hit_flash = 0.0
        self.hit_quad.enabled = False
        self.hit_quad.color = color.rgba(0.95, 0.08, 0.08, 0.0)
        for e in (self.guard_bubble, self.dodge_halo,
                  self.r_guard_bubble, self.r_dodge_halo):
            e.visible = False
        self._shot_active = False
        self.shock_core.visible = False
        self.shock_halo.visible = False

    def _tick_combat(self, dt):
        """技能计时 / 特效推进 / 闪避状态（dt 为墙钟时间，暂停时只推进表现层）"""
        self._update_fx(dt)
        self._update_pops(dt)
        if self.hit_flash > 0.0:
            self.hit_flash = max(0.0, self.hit_flash - dt)

        if (self.net_phase == "play" and not self.paused
                and not self.over and not self.pause_menu):
            self.atk_cd = max(0.0, self.atk_cd - dt)
            self.dodge_cd = max(0.0, self.dodge_cd - dt)
            self.guard_cd = max(0.0, self.guard_cd - dt)
            self._update_shockwave(dt)
            self.dodge_t = max(0.0, self.dodge_t - dt)
            self.guard_t = max(0.0, self.guard_t - dt)
            if self.dodge_t > 0.0:
                self._ghost_step -= dt
                if self._ghost_step <= 0.0:
                    self._ghost_step = 0.07
                    self._spawn_ghost(self.x, self.y, self.h)
            while self._test_plan and self.net_clock >= self._test_plan[0][0]:
                _t, key = self._test_plan.pop(0)
                self._use_skill(key)

        if self.dodge_t > 0.0:
            self.speed_scale = DODGE_SPEED
        elif held_keys["s"] or held_keys["down arrow"]:
            self.speed_scale = BRAKE_SPEED     # 减速拉扯：便于迂回 / 拉开距离
        else:
            self.speed_scale = 1.0
        if _NET_TEST_COMBAT:
            self.speed_scale = 0.0     # 联调：保持固定距离，便于稳定命中
        if self.over and self._shot_active:
            self._end_shockwave()

    def _use_skill(self, key):
        if self.net_phase != "play" or self.paused or self.over or self.pause_menu:
            return
        if key == "q":
            if self.atk_cd > 0.0:
                self._spawn_pop(f"冲击波冷却 {self.atk_cd:.1f}s",
                                (1.0, 0.7, 0.4), 1.4)
                return
            self.atk_cd = ATTACK_CD
            self._cast_attack()
        elif key == "e":
            if self.dodge_cd > 0.0:
                self._spawn_pop(f"闪避冷却 {self.dodge_cd:.1f}s",
                                (0.6, 1.0, 0.8), 1.4)
                return
            self.dodge_cd = DODGE_CD
            self.dodge_t = DODGE_INVULN
            self._ghost_step = 0.0
            self._spawn_wave(self.x, self.y, self.h, DODGE_ANIM_COLOR, 0.8, 4.2,
                             0.4, 0.45)
            self._spawn_burst(self.x, self.y, self.h, DODGE_ANIM_COLOR, 8)
            self._spawn_pop("闪避 · 无敌", (0.5, 1.0, 0.75), 1.7)
            self._play(self.snd_eat)
            if TEST_MODE:
                print(f"[NET] {'host' if self.is_host else 'client'} cast dodge",
                      flush=True)
        elif key == "r":
            if self.guard_cd > 0.0:
                self._spawn_pop(f"护盾冷却 {self.guard_cd:.1f}s",
                                (0.55, 0.85, 1.0), 1.4)
                return
            self.guard_cd = GUARD_CD
            self.guard_t = GUARD_DURATION
            self._spawn_wave(self.x, self.y, self.h, GUARD_ANIM_COLOR, 0.8, 4.6,
                             0.45, 0.42)
            self._spawn_pop(f"护盾开启 · 减伤 {int(GUARD_REDUCTION * 100)}%",
                            (0.55, 0.85, 1.0), 1.6)
            self._play(self.snd_eat)
            if TEST_MODE:
                print(f"[NET] {'host' if self.is_host else 'client'} cast guard",
                      flush=True)
        elif key == "x":
            # 联调专用：模拟战斗获得经验，验证升级成长
            self._gain_xp(SKY_XP_PER_LEVEL, self._anim_t)

    def _cast_attack(self):
        """冲击波：从蛇头向前方发射光波，飞行途中命中对手造成伤害"""
        self._shot_active = True
        self._shot_traveled = 0.0
        self._shot_age = 0.0
        self._shot_x, self._shot_y, self._shot_h = self.x, self.y, self.h
        cp = math.cos(self.pitch)
        self._shot_dx = math.cos(self.angle) * cp
        self._shot_dy = math.sin(self.angle) * cp
        self._shot_dh = math.sin(self.pitch)
        pos = (_w(self.x), self.h, _w(self.y))
        self.shock_core.position = pos
        self.shock_halo.position = pos
        self.shock_core.scale = (2.4, 1.1, 2.4)
        self.shock_halo.scale = (3.8, 1.5, 3.8)
        self.shock_core.visible = True
        self.shock_halo.visible = True
        self._spawn_wave(self.x, self.y, self.h, ATTACK_ANIM_COLOR, 0.9, 3.4,
                         0.30, 0.5)
        self._play(self.snd_eat)
        if TEST_MODE:
            print(f"[NET] {'host' if self.is_host else 'client'} cast attack",
                  flush=True)

    def _resolve_attack_hit(self, hit):
        hx, hy, hh = hit
        self._spawn_burst(hx, hy, hh, ATTACK_ANIM_COLOR, 12)
        self._spawn_pop(f"命中 -{self.attack_power}", (1.0, 0.86, 0.36), 2.2)
        self._gain_xp(HIT_XP, self._anim_t)      # 战斗经验：命中即成长
        if TEST_MODE:
            print(f"[NET] {'host' if self.is_host else 'client'} attack hit=yes "
                  f"dmg={self.attack_power} xp={self.xp} lv={self.level}",
                  flush=True)
        if self.conn is not None:
            self.conn.send({"t": "hit", "dmg": int(self.attack_power)})

    def _update_shockwave(self, dt):
        """光波飞行：沿施放方向推进，扫掠检测命中，超出射程后消散"""
        if not self._shot_active:
            return
        self._shot_age += dt
        step = ATTACK_SPEED * dt
        x0, y0, h0 = self._shot_x, self._shot_y, self._shot_h
        x1 = x0 + self._shot_dx * step
        y1 = y0 + self._shot_dy * step
        h1 = h0 + self._shot_dh * step * SC
        self._shot_x, self._shot_y, self._shot_h = x1, y1, h1
        self._shot_traveled += step

        hit = self._find_shot_hit(x0, y0, h0, x1, y1, h1)
        if hit is not None:
            hx, hy, hh = hit
            self._spawn_wave(hx, hy, hh, ATTACK_ANIM_COLOR, 1.0, 6.4,
                             ATTACK_ANIM, 0.65)
            self._resolve_attack_hit(hit)
            self._end_shockwave()
            return

        px, py, ph = _w(x1), h1, _w(y1)
        pulse = 0.55 + 0.45 * math.sin(self._shot_age * 26.0)
        self.shock_core.position = (px, ph, py)
        self.shock_halo.position = (px, ph, py)
        self.shock_core.scale = (2.1 + 0.5 * pulse, 1.0 + 0.22 * pulse,
                                 2.1 + 0.5 * pulse)
        self.shock_halo.scale = (3.4 + 0.8 * pulse, 1.4 + 0.3 * pulse,
                                 3.4 + 0.8 * pulse)

        if self._shot_traveled >= ATTACK_RANGE:
            self._spawn_wave(x1, y1, h1, ATTACK_ANIM_COLOR, 0.8, 3.8,
                             0.28, 0.35)
            self._end_shockwave()

    def _end_shockwave(self):
        self._shot_active = False
        self.shock_core.visible = False
        self.shock_halo.visible = False

    def _find_shot_hit(self, x0, y0, h0, x1, y1, h1):
        """扫掠命中：对手蛇头/轨迹点到本帧飞行线段的最小距离"""
        if not self.remote_seen or not self.remote_alive:
            return None
        sx, sy = x1 - x0, y1 - y0
        seg2 = sx * sx + sy * sy
        pts = [self.remote_head_disp]
        pts.extend(self.remote_trail)
        for tx, ty, th in pts:
            k = 0.0 if seg2 < 1e-6 \
                else ((tx - x0) * sx + (ty - y0) * sy) / seg2
            k = min(1.0, max(0.0, k))
            dx = tx - (x0 + sx * k)
            dy = ty - (y0 + sy * k)
            if dx * dx + dy * dy > ATTACK_HIT_RADIUS * ATTACK_HIT_RADIUS:
                continue
            if abs((th - (h0 + (h1 - h0) * k)) / SC) > ATTACK_HIT_HEIGHT:
                continue
            return (tx, ty, th)
        return None

    def _receive_hit(self, msg):
        """被命中：本地权威结算闪避/护盾，再把真实 HP 回传攻击方"""
        if (not self._net_inited or self.net_phase != "play"
                or self.over or self.pause_menu):
            return
        try:
            dmg = float(msg.get("dmg", ATTACK_DAMAGE))
        except Exception:
            dmg = float(ATTACK_DAMAGE)
        if self.dodge_t > 0.0:
            kind, dmg = "dodge", 0
        elif self.guard_t > 0.0:
            kind = "guard"
            dmg = int(round(dmg * (1.0 - GUARD_REDUCTION)))
        else:
            kind = ""
            dmg = int(round(dmg))
        dmg = max(0, dmg)
        self.hp = max(0, self.hp - dmg)
        if dmg > 0:
            self.hit_flash = self._hit_flash_cap
            self._spawn_burst(self.x, self.y, self.h, (1.0, 0.3, 0.24), 14)
            text, col = f"-{dmg}", (1.0, 0.38, 0.30)
            if kind == "guard":
                text, col = f"格挡 -{dmg}", (0.55, 0.85, 1.0)
            self._spawn_pop(text, col, 2.4)
        else:
            self._spawn_pop("闪避成功", (0.5, 1.0, 0.75), 2.0)
        if self.conn is not None:
            self.conn.send({"t": "hp", "v": int(self.hp), "d": int(dmg),
                            "k": kind})
        if TEST_MODE:
            print(f"[NET] {'host' if self.is_host else 'client'} got hit "
                  f"kind={kind or 'none'} dmg={dmg} hp={self.hp} "
                  f"dodge={self.dodge_t:.1f} guard={self.guard_t:.1f}",
                  flush=True)
        if self.hp <= 0:
            self.die("hp")

    def _apply_remote_hp(self, msg):
        if self.net_phase == "ending":
            return
        try:
            v = int(msg.get("v", self.remote_hp))
            dmg = int(msg.get("d", 0))
        except Exception:
            return
        self.remote_hp = max(0, min(self.remote_hp_max, v))
        kind = str(msg.get("k") or "")
        if kind == "dodge":
            self._spawn_pop(f"{self.remote_nick} 闪避", (0.5, 1.0, 0.75), 1.6)
        elif kind == "guard":
            self._spawn_pop(f"{self.remote_nick} 格挡 -{dmg}",
                            (0.55, 0.85, 1.0), 1.7)
        elif dmg > 0:
            self._spawn_pop(f"{self.remote_nick} -{dmg}", (1.0, 0.55, 0.35), 1.8)
        if TEST_MODE:
            print(f"[NET] {'host' if self.is_host else 'client'} sync remote "
                  f"hp={self.remote_hp} kind={kind or 'none'} dmg={dmg}",
                  flush=True)

    def _spawn_wave(self, x, y, h, rgb, s0, s1, ttl, a0):
        self._spawn_fx("wave", x, y, h, s0=s0, s1=s1, ttl=ttl, a0=a0, rgb=rgb)

    def _spawn_burst(self, x, y, h, rgb, count):
        rng = self.fx_rng
        for _ in range(count):
            ang = rng.uniform(0.0, math.pi * 2)
            spd = rng.uniform(30.0, 110.0)
            self._spawn_fx("spark", x, y, h,
                           s0=rng.uniform(0.12, 0.26), s1=0.0,
                           ttl=rng.uniform(0.30, 0.55),
                           a0=rng.uniform(0.45, 0.8), rgb=rgb,
                           vx=math.cos(ang) * spd,
                           vy=math.sin(ang) * spd,
                           vh=rng.uniform(-2.0, 5.0))

    def _spawn_ghost(self, x, y, h):
        self._spawn_fx("ghost", x, y, h,
                       s0=SKY_HEAD_RADIUS * SC * 2.0, s1=0.0,
                       ttl=0.42, a0=0.30, rgb=(0.42, 1.0, 0.72))

    def _spawn_fx(self, kind, x, y, h, s0, s1, ttl, a0, rgb,
                  vx=0.0, vy=0.0, vh=0.0):
        slot = next((p for p in self._fx_pool if p["ttl"] <= 0.0), None)
        if slot is None:
            slot = self._fx_pool[0]
        slot.update(kind=kind, age=0.0, ttl=ttl, x=x, y=y, h=h,
                    vx=vx, vy=vy, vh=vh, s0=s0, s1=s1, a0=a0, rgb=rgb)
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
            if p["kind"] == "wave":
                e.scale = p["s0"] + (p["s1"] - p["s0"]) * k
                e.position = (_w(p["x"]), p["h"], _w(p["y"]))
                a = p["a0"] * (1.0 - k) * (1.0 - k)
            elif p["kind"] == "ghost":
                e.scale = p["s0"] * (1.0 + 0.35 * k)
                a = p["a0"] * (1.0 - k)
            else:
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
            a = 1.0 - k
            p["t"].color = color.rgba(rgb[0], rgb[1], rgb[2], a)

    # ---------- 帧更新 ----------

    def update(self):
        self._poll_net()
        dt = min(0.033, ursina_time.dt)
        rdt = min(0.25, ursina_time.dt)     # 墙钟时间：计时/冷却按真实时间推进
        self._net_tick(dt, rdt)
        super().update()
        self._update_remote_visual(ursina_time.dt)
        self._update_net_hud()
        if TEST_MODE and _NET_SHOT and not self._shot_done \
                and self.net_phase in ("play", "ending") \
                and self.net_clock >= _NET_SHOT_AT:
            self._shot_done = True
            from panda3d.core import Filename
            img = _APP.win.getScreenshot() if _APP else None
            if img:
                img.write(Filename.fromOsSpecific(_NET_SHOT))
            print(f"[NET] shot {_NET_SHOT} poses={self._pose_in} "
                  f"remote={self.remote_seen}/{self.remote_alive} "
                  f"phase={self.net_phase} head_vis={self.r_head.visible} "
                  f"seg_vis={sum(1 for e in self.r_seg_ents if e.visible)} "
                  f"dist={math.hypot(self.x - self.remote_head_disp[0], self.y - self.remote_head_disp[1]):.0f}",
                  flush=True)
        if _NET_TEST_EXIT > 0 and self._anim_t > _NET_TEST_EXIT:
            self._exit(0)

    def _net_tick(self, dt, rdt):
        if self._net_inited:
            self._tick_combat(rdt)
        if self._center_hold > 0:
            self._center_hold -= dt
            if self._center_hold < 0.0:
                self._center_hold = 0.0

        if self.net_phase == "countdown" and not self.pause_menu:
            self.net_countdown_left -= rdt
            if self.net_countdown_left <= 0:
                self.net_phase = "play"
                self.paused = False
                self.net_clock = 0.0
                self._set_center("GO!", 0.8)
                self._last_send_t = -1.0
                if TEST_MODE:
                    print("[NET] phase=play", flush=True)
            return

        if self.net_phase != "play" or self.over:
            return
        if self.paused or self.pause_menu:
            return

        self.net_clock += rdt
        self._send_pose()
        # 无时间限制：对局只在 HP 归零时结束（见 die/_receive_hit）

    def die(self, reason):
        if self.over or (self.invincible and reason != "hp"):
            return
        if not self._net_inited or self.net_phase not in ("countdown", "play"):
            super().die(reason)
            return
        if reason != "hp":
            # 联机对战（含等待期外）唯一的结束条件是 HP 归零：
            # 地形/陨石/边界碰撞转化为弹开或掉头，不再判定坠毁
            if reason == "bounds":
                hr = SKY_HEAD_RADIUS
                self.target_angle = math.atan2(WORLD / 2 - self.y,
                                               WORLD / 2 - self.x)
                self.angle = self.target_angle
                self.x = min(max(self.x, hr + 1), WORLD - hr - 1)
                self.y = min(max(self.y, hr + 1), WORLD - hr - 1)
            else:
                self.hit_flash = 0.22
                self.pitch = max(self.pitch, 0.35)
                self.target_pitch = max(self.target_pitch, 0.35)
            return
        self.over = True
        self.death_reason = reason
        self.dashing = False
        self.speed_scale = 1.0
        self.dodge_t = 0.0
        self.guard_t = 0.0
        if self.bgm:
            try:
                self.bgm.stop()
            except Exception:
                pass
        self._play(self.snd_death)
        for k in self.buffs:
            self.buffs[k] = 0.0
        self.shield_bubble.visible = False
        self.guard_bubble.visible = False
        self.dodge_halo.visible = False
        self._update_buff_hud()
        self.paused = True
        if self.is_host:
            self._finish_match("p2", reason)
        else:
            self.conn.send({"t": "die", "reason": reason})
            self._set_center("你已坠毁，等待主机结算…", -1.0)

    def _finish_match(self, winner, reason):
        """主机判定结果并广播"""
        if self.net_phase == "ending":
            return
        self.net_phase = "ending"
        self.over = True
        self.paused = True
        self.net_result = (winner, reason)
        self.conn.send({"t": "end", "winner": winner, "reason": reason})
        self._show_net_result(winner == "p1", winner is None, reason)
        if TEST_MODE:
            print(f"[NET] host end winner={winner} reason={reason} "
                  f"xp={self.xp}/{self.remote_xp}", flush=True)

    def _client_end(self, winner, reason):
        if not self.is_host and self.net_phase == "ending":
            return
        self.net_phase = "ending"
        self.over = True
        self.paused = True
        self.net_result = (winner, reason)
        self._show_net_result(winner == "p2", winner is None, reason)
        if TEST_MODE:
            print(f"[NET] client end winner={winner} reason={reason} "
                  f"xp={self.xp}/{self.remote_xp}", flush=True)

    def _show_net_result(self, win, draw, reason):
        if not self._net_inited:
            return
        reason_text = _REASON_TEXT.get(reason, reason)
        if reason == "leave" and draw:
            title, col = "连接中断", color.rgb(0.75, 0.82, 0.9)
        elif draw:
            title, col = "平局", color.rgb(0.85, 0.9, 1.0)
        elif win:
            title, col = "胜利！", color.rgb(1.0, 0.85, 0.35)
        else:
            title, col = "惜败", color.rgb(1.0, 0.38, 0.32)
        self.over_title.text = title
        self.over_title.color = col
        self.over_accent.color = color.rgba(col[0], col[1], col[2], 0.80)
        self.over_border.color = color.rgba(col[0], col[1], col[2], 0.20)
        self.over_info.text = (f"{reason_text}\n你的经验 {self.xp}   "
                               f"{self.remote_nick} 经验 {self.remote_xp}")
        self.pause_badge.enabled = False
        self._set_hud_visible(False)
        self._set_center("", 0.0)
        self._set_cursor_locked(False)
        self.over_panel.enabled = True
        self._pop_menu(self.over_card, self.over_dim)

    def _show_gameover(self):
        # 主机等待期自由练习死亡时沿用单机结算面板
        if self._net_inited and self.net_phase not in ("wait", "connect"):
            return
        super()._show_gameover()

    # ---------- 对手渲染 / HUD ----------

    def _update_remote_visual(self, dt):
        if not self._net_inited or not self.remote_alive or len(self.remote_trail) < 2:
            self.r_head.visible = False
            for e in self.r_seg_ents:
                e.visible = False
            self._r_vis_n = 0
            return
        k = min(1.0, max(0.05, dt * 12.0))
        d = self.remote_head_disp
        tx, ty, th = self.remote_target
        d[0] += (tx - d[0]) * k
        d[1] += (ty - d[1]) * k
        d[2] += (th - d[2]) * k
        bob = math.sin(self._anim_t * 3.0) * 0.1
        self.r_head.setPos(_w(d[0]), d[2] + bob, _w(d[1]))
        self.r_head.rotation_y = -math.degrees(self.remote_angle)
        self.r_head.rotation_z = math.degrees(self.remote_pitch)
        self.r_head.visible = True

        pts = [(d[0], d[1], d[2])] + list(self.remote_trail)[1:]
        n = min(len(pts), len(self.r_seg_ents))
        prev_n = self._r_vis_n
        if n != prev_n:
            self._recolor_remote_segments(n)
        camx, camy, camz = camera.position
        hide2 = NEAR_HIDE * NEAR_HIDE
        for i in range(n):
            px, py, ph = pts[i]
            sx, sz = _w(px), _w(py)
            sy = ph + math.sin(self._anim_t * 3.0 - i * 0.3) * 0.1
            e = self.r_seg_ents[i]
            # 远端蛇身体段同样贴地，避免插入山体
            rad = e.scale_x * 0.5
            if sy - rad < TERRAIN_MAX:
                top = self._column_top(px, py)
                if top is not None:
                    floor = top + rad - 0.02
                    if sy < floor:
                        sy = floor
            e.setPos(sx, sy, sz)
            dx, dy, dz = sx - camx, sy - camy, sz - camz
            e.visible = (dx * dx + dy * dy + dz * dz) > hide2
        for i in range(n, max(n, prev_n)):
            self.r_seg_ents[i].visible = False
        self._r_vis_n = n

    def _recolor_remote_segments(self, n):
        c1, c2 = self.r_skin["c1"], self.r_skin["c2"]
        denom = max(1, n - 1)
        for i in range(n):
            e = self.r_seg_ents[i]
            k = i / denom
            e.scale = (0.92 * (1 - k) + 0.34 * k) * 2
            e.color = color.rgb((c1[0] * (1 - k) + c2[0] * k) / 255,
                                (c1[1] * (1 - k) + c2[1] * k) / 255,
                                (c1[2] * (1 - k) + c2[2] * k) / 255)

    def _set_center(self, msg, hold):
        self._center_msg = msg
        self._center_hold = hold

    def _update_combat_hud(self):
        """HP 条 / 技能冷却 / 状态标识 / 护盾特效的每帧刷新"""
        active = (self.net_phase in ("countdown", "play")
                  and not self.over)
        if self.combat_root.enabled != active:
            self.combat_root.enabled = active

        t = self._anim_t
        # 自己的血量条（上限随等级成长）
        hk = (self.hp, self.hp_max, self.level, self.attack_power)
        if self._hp_ui_key != hk:
            self._hp_ui_key = hk
            frac = self.hp / max(1, self.hp_max)
            self.hp_bar.scale = (max(0.001, 0.32 * frac), 0.017)
            self.hp_bar.position = (-0.60 + 0.16 * frac, -0.382)
            if frac > 0.6:
                self.hp_bar.color = color.rgb(0.34, 0.95, 0.46)
            elif frac > 0.3:
                self.hp_bar.color = color.rgb(1.0, 0.78, 0.25)
            else:
                self.hp_bar.color = color.rgb(1.0, 0.30, 0.26)
            self.hp_text.text = (f"HP {self.hp}/{self.hp_max}   "
                                 f"Lv {self.level} · 攻击 {self.attack_power}")

        # 技能冷却（0.1s 粒度刷新文本，避免每帧重建字形纹理）
        key = (round(self.atk_cd, 1), round(self.dodge_cd, 1),
               round(self.guard_cd, 1), round(self.dodge_t, 1),
               round(self.guard_t, 1))
        if self._skill_ui_key != key:
            self._skill_ui_key = key
            for slot in self.skill_slots:
                cd = {"q": self.atk_cd, "e": self.dodge_cd,
                      "r": self.guard_cd}[slot["key"]]
                ready = cd <= 0.0
                slot["overlay"].enabled = not ready
                slot["cd"].enabled = not ready
                if ready:
                    slot["cd"].text = ""
                else:
                    slot["cd"].text = f"{cd:.1f}"
                col = slot["col"]
                if ready:
                    slot["bg"].color = color.rgba(0.10, 0.18, 0.34, 0.9)
                else:
                    slot["bg"].color = color.rgba(0.05, 0.06, 0.12, 0.9)
                slot["name"].color = color.rgba(col[0], col[1], col[2],
                                                1.0 if ready else 0.45)

        # 状态 buff 文本
        parts = []
        if self.dodge_t > 0.0:
            parts.append(f"闪避·无敌 {self.dodge_t:.1f}s")
        if self.guard_t > 0.0:
            parts.append(f"护盾 {self.guard_t:.1f}s 减伤"
                         f"{int(GUARD_REDUCTION * 100)}%")
        bkey = tuple(parts)
        if self._buff_key != bkey:
            self._buff_key = bkey
            self.net_buff_text.text = "    ".join(parts)
            self.net_buff_text.color = (color.rgba(0.55, 1.0, 0.8, 1.0)
                                        if parts else
                                        color.rgba(0.55, 1.0, 0.8, 0.0))

        # 自己的护盾泡 / 闪避光环
        on = self.guard_t > 0.0 and not self.over
        self.guard_bubble.visible = on
        if on:
            self.guard_bubble.scale = 1.52 + 0.07 * math.sin(t * 8.0)
            self.guard_bubble.color = color.rgba(
                0.38, 0.82, 1.0, 0.18 + 0.06 * math.sin(t * 8.0))
        dn = self.dodge_t > 0.0 and not self.over
        self.dodge_halo.visible = dn
        if dn:
            self.dodge_halo.scale = 1.30 + 0.10 * math.sin(t * 14.0)
            self.dodge_halo.color = color.rgba(
                0.45, 1.0, 0.72, 0.13 + 0.06 * math.sin(t * 14.0))

        # 对手血量条与状态
        show = self.remote_seen and active
        if self.remote_hp_root.enabled != show:
            self.remote_hp_root.enabled = show
        if show:
            frac = self.remote_hp / max(1, self.remote_hp_max)
            self.remote_hp_bar.scale = (max(0.001, 0.28 * frac), 0.010)
            self.remote_hp_bar.position = (-0.14 + 0.14 * frac, 0.404)
            rk = (self.remote_nick, self.remote_hp, self.remote_hp_max,
                  self.remote_level)
            if self._remote_hp_key != rk:
                self._remote_hp_key = rk
                self.remote_hp_text.text = (f"HP {self.remote_hp}/"
                                            f"{self.remote_hp_max} · "
                                            f"Lv{self.remote_level}")
            self.r_guard_bubble.visible = self.remote_guard > 0.0
            if self.r_guard_bubble.visible:
                self.r_guard_bubble.scale = 1.52 + 0.07 * math.sin(t * 8.0)
            self.r_dodge_halo.visible = self.remote_dodge > 0.0
            if self.r_dodge_halo.visible:
                self.r_dodge_halo.scale = 1.30 + 0.10 * math.sin(t * 14.0)
        else:
            self.r_guard_bubble.visible = False
            self.r_dodge_halo.visible = False

        # 受击闪屏
        if self.hit_flash > 0.0:
            a = 0.55 * (self.hit_flash / self._hit_flash_cap)
            self.hit_quad.enabled = True
            self.hit_quad.color = color.rgba(0.95, 0.08, 0.08, a)
        elif self.hit_quad.enabled:
            self.hit_quad.enabled = False

    def _update_net_hud(self):
        phase = self.net_phase
        if phase == "wait":
            top = (f"房间码 {self.room_code}  ·  等待好友加入\n"
                   "（自由练习中，好友加入后自动开局）")
        elif phase == "connect":
            top = "已连接主机，等待开局…"
        else:
            sec = max(0, int(self.net_clock))
            top = (f"你 Lv{self.level} {self.xp}   :   "
                   f"{self.remote_xp} Lv{self.remote_level} {self.remote_nick}"
                   f"      用时 {sec // 60}:{sec % 60:02d}")

        if phase == "countdown":
            center = str(max(1, int(math.ceil(self.net_countdown_left)))
                         ) if self.net_countdown_left > 0 else "GO!"
            ccol = color.rgba(0.4, 0.95, 1.0, 1.0)
        elif self._center_hold != 0.0:
            center = self._center_msg
            ccol = color.rgba(1.0, 0.82, 0.35, 1.0) if self._center_hold < 0 else \
                color.rgba(0.4, 0.95, 1.0, 0.9)
        else:
            center = ""
            ccol = color.rgba(0.4, 0.95, 1.0, 0.0)

        key = (top, center)
        if key != self._hud_key:
            self._hud_key = key
            if self.net_top.text != top:
                self.net_top.text = top
            if self.net_center.text != center:
                self.net_center.text = center
                self.net_center.color = ccol

        show_remote = (self.remote_seen and self.remote_alive
                       and phase in ("countdown", "play"))
        self.mm_remote.enabled = show_remote
        if show_remote:
            rx, ry = self.remote_head_disp[0], self.remote_head_disp[1]
            self.mm_remote.position = (
                0.5 + (rx / WORLD - 0.5) * 0.24,
                0.33 + (0.5 - ry / WORLD) * (0.24 * 9 / 7))
            self.mm_remote.color = color.rgb(
                self.r_skin["head"][0] / 255,
                self.r_skin["head"][1] / 255,
                self.r_skin["head"][2] / 255)

        self._update_combat_hud()

    # ---------- 输入 / 退出 ----------

    def resume(self):
        super().resume()
        if self.net_phase in ("connect", "countdown"):
            self.paused = True

    def input(self, key):
        if key in ("q", "e", "r"):
            self._use_skill(key)
            return
        if self.net_phase in ("connect", "countdown") and key == "p":
            return
        super().input(key)

    def _exit(self, code):
        if TEST_MODE:
            print(f"[NET] exit code={code} phase={self.net_phase} "
                  f"clock={self.net_clock:.1f}s poses={self._pose_in} "
                  f"seen={self.remote_seen}", flush=True)
        try:
            if self.conn is not None:
                self.conn.send({"t": "bye"})
                self.conn.close()
        except Exception:
            pass
        super()._exit(code)


# ---------------- 入口 ----------------

def _parse_args(argv):
    is_host = "--net-host" in argv
    ip, port, nick = None, NET_PORT, None
    if "--net-join" in argv:
        i = argv.index("--net-join")
        if i + 2 < len(argv):
            ip = argv[i + 1]
            try:
                port = int(argv[i + 2])
            except Exception:
                port = NET_PORT
    if "--nick" in argv:
        i = argv.index("--nick")
        if i + 1 < len(argv):
            nick = argv[i + 1]
    return is_host, ip, port, nick


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    is_host, ip, port, nick = _parse_args(argv)
    load_settings()

    if is_host:
        try:
            conn = NetHost(NET_PORT)
        except Exception as exc:
            _log(f"[NET] 端口 {NET_PORT} 被占用: {exc}")
            sys.exit(2)
        room_code = make_room_code(get_local_ip())
    else:
        if not ip:
            _log("[NET] 缺少加入地址")
            sys.exit(3)
        try:
            conn = NetClient(ip, port, timeout=3.0)
        except Exception as exc:
            _log(f"[NET] 连接 {ip}:{port} 失败: {exc}")
            sys.exit(3)
        room_code = ""

    loadPrcFileData("", "window-title 天空贪吃蛇 3D · 联机对战")
    if TEST_MODE:
        loadPrcFileData("", "window-type offscreen")
    if os.environ.get("SKY3D_SMALL") == "1":
        size, full = (320, 240), False
    elif TEST_MODE:
        size, full = (900, 700), False
    else:
        size, full = None, True

    app = Ursina(size=size, title="天空贪吃蛇 3D 联机对战", vsync=True, fullscreen=full)
    global _APP
    s3._APP = app
    _APP = app
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

    game = Sky3DNetGame(conn, is_host, room_code, nickname=nick,
                        skin=SETTINGS.get("skin", 0), test_mode=TEST_MODE)
    s3.make_fps_display()

    def frame():
        game.steer()

    Entity(update=lambda: frame(), eternal=True)
    app.run()


_APP = None

if __name__ == "__main__":
    main()
