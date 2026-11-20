"""联机对战模式：主机权威模拟 + 客户端镜像渲染。

页面布局与人机对战（VersusGame）保持一致：宽屏窗口、星空背景、
网格、食物、双蛇对战、顶部比分、结束遮罩。

协议（JSON 行）：
  客户端 -> 主机：{"t":"hello","nick":str,"skin":int}
                   {"t":"dir","dir":[dx,dy]}  {"t":"boost","on":bool,"dir":[dx,dy]}  {"t":"bye"}
  主机 -> 客户端：{"t":"state", ...}  完整状态快照（含房间参数、昵称/皮肤、道具、限时）
主机为 p1（左侧），客户端为 p2（右侧）；双方规则与单人模式一致
（撞墙/撞自身/撞对方死亡，长按同向键加速，加速 10 格瘦身 1 格且 -1 分）。
"""
import pygame
import sys
import math
import random
from datetime import datetime
from config import *
from ui import Button, Star, ModalOverlay, QuitConfirm
from utils import UI_SCALE, create_bg, get_ui_font, resource_path, \
    generate_obstacles, load_net_record, update_net_record, sound_volume, key_is, render_text, get_grid_overlay

FOOD_COUNT = 3
COUNTDOWN_MS = 3000

# 竞技规则：三局两胜、连击倍率、危险区收缩
ROUNDS_TO_WIN = 2        # 先赢 2 局获胜
MAX_ROUNDS = 3           # 最多 3 局（平局按局分判定）
ROUND_BREAK_MS = 2600    # 局间休整时长
COMBO_WINDOW_MS = 5000   # 连续吃食的连击窗口
COMBO_MAX = 9            # 连击计数上限
COMBO_SCORE_CAP = 5      # 普通食物最高 5 倍分
SUDDEN_DEATH_START_MS = 35000   # 无时限模式：35 秒后开始收缩
SHRINK_INTERVAL_MS = 3000       # 每 3 秒四周各收缩一格
SAFE_MIN_W = 16
SAFE_MIN_H = 10


class NetBattleGame:
    def __init__(self, screen, conn, is_host, room_cfg=None, nickname=None, skin=None):
        self.screen = screen
        self.conn = conn
        self.is_host = is_host
        self.clock = pygame.time.Clock()
        self.over = False
        self.winner = None            # 'p1' / 'p2' / None（平局）
        self.disconnected = False
        self.confirm_leave = False

        self.room_cfg = dict(room_cfg) if room_cfg else {
            "interval": PLAYER_FIXED_INTERVAL,
            "obstacles": NET_OBSTACLE_COUNT,
            "foods": FOOD_COUNT,
            "duration": 0,
        }

        self.my_nick = (nickname or SETTINGS["nickname"])[:10]
        self.my_skin = skin if isinstance(skin, int) else SETTINGS["skin"]
        self.my_skin = self.my_skin % len(SKINS)
        self.nick = {"p1": self.my_nick if is_host else "对手",
                     "p2": "对手" if is_host else self.my_nick}
        self.skin_id = {"p1": self.my_skin if is_host else 0,
                        "p2": 0 if is_host else self.my_skin}

        pygame.mixer.init()
        self.eat_sound = pygame.mixer.Sound(resource_path("assets/audio/eat.wav"))
        self.eat_sound.set_volume(sound_volume("eat"))
        self.death_sound = pygame.mixer.Sound(resource_path("assets/audio/death.wav"))
        self.death_sound.set_volume(sound_volume("death"))
        self.bgm = pygame.mixer.Sound(resource_path("assets/audio/game.wav"))
        self.bgm.set_volume(sound_volume("bgm"))
        self.bgm_playing = False

        self.grid_w = VERSUS_GRID_WIDTH
        self.grid_h = VERSUS_GRID_HEIGHT
        self.window_w = VERSUS_WINDOW_WIDTH
        self.window_h = VERSUS_WINDOW_HEIGHT

        self.normal_interval = int(self.room_cfg.get("interval", PLAYER_FIXED_INTERVAL))
        self.boost_interval = int(self.normal_interval * BOOST_SPEED_FACTOR)

        self.font20 = get_ui_font(20)
        self.font28 = get_ui_font(28)
        self.font34 = get_ui_font(34)
        self.font48 = get_ui_font(48)
        self.font64 = get_ui_font(64)

        bg_path = create_bg(self.window_w, self.window_h)
        self.bg = pygame.image.load(bg_path).convert() if bg_path else None
        self.stars = [Star(self.window_w, self.window_h) for _ in range(80)]

        window = (self.window_w, self.window_h)
        if self.is_host:
            over_items = [("再来一场", "primary"), ("返回菜单", "card"),
                          ("退出游戏", "ghost")]
        else:
            over_items = [("返回菜单", "primary"), ("退出游戏", "ghost")]
        self.over_ui = ModalOverlay(
            window, "对局结束", self.font48, self.font28, self.font28,
            over_items, accent=(255, 205, 110),
            title_colors=((255, 250, 220), (255, 190, 60)))
        self.confirm_ui = ModalOverlay(
            window, "离开对战？", self.font48, self.font28, self.font28,
            [("继续对战", "primary"), ("离开对战", "ghost")],
            accent=(255, 175, 110),
            title_colors=((255, 240, 225), (255, 150, 80)))
        self.confirm_ui.set_subtitle("离开后本局将判定为弃权")
        self.disconnect_ui = ModalOverlay(
            window, "对方已断开连接", self.font48, self.font28, self.font28, [],
            accent=(255, 118, 118),
            title_colors=((255, 232, 228), (255, 90, 90)))
        self.disconnect_ui.set_subtitle("点击任意处返回菜单")
        self.quit_ui = QuitConfirm(window, self.font48, self.font28, self.font28,
                                   self._quit_game)

        self.reset()

        # 客户端把自己的昵称/皮肤告知主机
        if not self.is_host:
            self.conn.send({"t": "hello", "nick": self.my_nick, "skin": self.my_skin})

    # ---------- 状态初始化 ----------

    def reset(self):
        """开始一整场比赛（三局两胜）"""
        self.round_wins = {'p1': 0, 'p2': 0}
        self.round_no = 1
        self.match_score = {'p1': 0, 'p2': 0}
        self.max_combo = {'p1': 1, 'p2': 1}
        self.record = load_net_record()
        self._record_saved = False
        self._reset_round()

    def _reset_round(self):
        """重置单局：蛇、食物、道具、危险区与倒计时"""
        now = pygame.time.get_ticks()
        # 对角出生（借鉴论文：左下角 vs 右上角）
        self.logic = {'p1': [(4, self.grid_h - 4)], 'p2': [(self.grid_w - 5, 3)]}
        self.dir = {'p1': RIGHT, 'p2': LEFT}
        self.next_dir = dict(self.dir)
        self.score = {'p1': 0, 'p2': 0}
        self.move_cnt = {'p1': 0, 'p2': 0}
        self.held = {'p1': None, 'p2': None}
        self.boost = {'p1': False, 'p2': False}
        self.boost_cnt = {'p1': 0, 'p2': 0}
        self.last_move = {'p1': now, 'p2': now}
        self.foods = []
        self.smooth = {'p1': [], 'p2': []}
        self.target = {'p1': [], 'p2': []}

        # 金色特殊食物 / 强化道具 / 增益效果
        self.gold = None
        self.gold_timer = 0
        self.powerups = []                 # [{"pos": (x,y), "kind": ...}]
        self.next_powerup_time = now + POWERUP_INTERVAL
        self.eff = {'p1': {'double': 0, 'shield': 0}, 'p2': {'double': 0, 'shield': 0}}

        self.over = False
        self.winner = None
        self.confirm_leave = False
        self._last_bc = 0

        # 单局状态：局分、连击、危险区、局间休整
        self.round_winner = None
        self.round_end_at = 0
        self.round_break_left = 0
        self.safe = [0, 0, self.grid_w - 1, self.grid_h - 1]
        self._danger_key = None
        self._shrink_at = now + SUDDEN_DEATH_START_MS
        self._round_start = now
        self._go_until = 0
        self._go_round = -1
        self.combo = {'p1': 0, 'p2': 0}
        self.last_eat = {'p1': -COMBO_WINDOW_MS, 'p2': -COMBO_WINDOW_MS}
        self.combo_until = {'p1': 0, 'p2': 0}

        self.normal_interval = int(self.room_cfg.get("interval", PLAYER_FIXED_INTERVAL))
        self.boost_interval = int(self.normal_interval * BOOST_SPEED_FACTOR)

        duration = int(self.room_cfg.get("duration", 0))
        if self.is_host:
            # 随机障碍（带连通性校验），客户端由快照同步
            obs_count = int(self.room_cfg.get("obstacles", NET_OBSTACLE_COUNT))
            self.obstacles = generate_obstacles(
                self.grid_w, self.grid_h, obs_count,
                self.logic['p1'][0], self.logic['p2'][0])
            self._respawn_all_foods()
            self.countdown_end = now + COUNTDOWN_MS
            self.countdown_left = COUNTDOWN_MS
            self.end_time = (self.countdown_end + duration * 1000) if duration else None
            self.time_left = duration * 1000
        else:
            self.obstacles = set()   # 由主机快照同步
            self.countdown_end = None
            self.countdown_left = COUNTDOWN_MS
            self.end_time = None
            self.time_left = duration * 1000
        self._sync_targets(snap=True)
        if self.is_host:
            self._broadcast_state()
        if hasattr(self, "over_ui"):
            self.over_ui.close()
            self.confirm_ui.close()
            self.disconnect_ui.close()
            self.quit_ui.close()

    # ---------- 食物与道具 ----------

    def _random_empty_pos(self):
        occupied = set(self.logic['p1']) | set(self.logic['p2']) | set(self.foods) | self.obstacles
        if self.gold:
            occupied.add(self.gold)
        for pu in self.powerups:
            occupied.add(pu["pos"])
        if len(occupied) >= self.grid_w * self.grid_h:
            return None
        for _ in range(MAX_ATTEMPTS):
            x = random.randint(0, self.grid_w - 1)
            y = random.randint(0, self.grid_h - 1)
            if (x, y) not in occupied:
                return (x, y)
        for x in range(self.grid_w):
            for y in range(self.grid_h):
                if (x, y) not in occupied:
                    return (x, y)
        return None

    def _respawn_all_foods(self):
        self.foods.clear()
        count = max(1, int(self.room_cfg.get("foods", FOOD_COUNT)))
        for _ in range(count):
            pos = self._random_empty_pos()
            if pos:
                self.foods.append(pos)

    def _respawn_one_food(self):
        pos = self._random_empty_pos()
        if pos:
            self.foods.append(pos)

    def _spawn_gold(self, now):
        if self.gold is not None or random.random() >= GOLD_FOOD_CHANCE:
            return
        pos = self._random_empty_pos()
        if pos:
            self.gold = pos
            self.gold_timer = now + GOLD_FOOD_DURATION

    def _spawn_powerup(self, now):
        if len(self.powerups) >= POWERUP_MAX:
            return
        pos = self._random_empty_pos()
        if pos is None:
            return
        self.powerups.append({"pos": pos, "kind": random.choice(POWERUP_TYPES)})

    def _apply_powerup(self, side, kind, now):
        if kind == "double":
            self.eff[side]["double"] = now + POWERUP_DOUBLE_MS
        else:
            self.eff[side]["shield"] = now + POWERUP_SHIELD_MS

    def _gain(self, side, base, now):
        if self.eff[side]["double"] > now:
            base *= 2
        self.score[side] += base

    def _shield_active(self, side, now):
        return self.eff[side]["shield"] > now

    def _combo_eat(self, side, now):
        """连续吃食累积连击，返回本次得分倍率"""
        if now - self.last_eat[side] <= COMBO_WINDOW_MS:
            self.combo[side] = min(self.combo[side] + 1, COMBO_MAX)
        else:
            self.combo[side] = 1
        self.last_eat[side] = now
        self.combo_until[side] = now + COMBO_WINDOW_MS
        self.max_combo[side] = max(self.max_combo[side], self.combo[side])
        return min(self.combo[side], COMBO_SCORE_CAP)

    # ---------- 主机端游戏逻辑 ----------

    def _set_direction(self, side, new_dir):
        cur = self.dir[side]
        if len(self.logic[side]) >= 3:
            if (cur == UP and new_dir == DOWN) or (cur == DOWN and new_dir == UP):
                return
            if (cur == LEFT and new_dir == RIGHT) or (cur == RIGHT and new_dir == LEFT):
                return
        self.next_dir[side] = new_dir

    def _move(self, side):
        self.dir[side] = self.next_dir[side]
        hx, hy = self.logic[side][0]
        nx, ny = hx + self.dir[side][0], hy + self.dir[side][1]
        now = pygame.time.get_ticks()

        ate = (nx, ny) in self.foods
        ate_gold = (self.gold is not None and (nx, ny) == self.gold)
        pu_kind = None
        for pu in self.powerups:
            if pu["pos"] == (nx, ny):
                pu_kind = pu["kind"]
                self.powerups.remove(pu)
                break

        if ate:
            self.eat_sound.play()
            mult = self._combo_eat(side, now)
            self._gain(side, mult, now)
            self.foods.remove((nx, ny))
            self._respawn_one_food()
            self._spawn_gold(now)
        elif ate_gold:
            self.eat_sound.play()
            mult = min(self._combo_eat(side, now), 2)
            self._gain(side, GOLD_FOOD_SCORE * mult, now)
            self.gold = None
        if pu_kind:
            self.eat_sound.play()
            self._apply_powerup(side, pu_kind, now)

        self.move_cnt[side] += 1
        # 自动成长（借鉴论文：初期每步长1格，后期每3步长1格）
        grow = ate or ate_gold or self.move_cnt[side] <= GROW_EVERY_STEP_UNTIL \
            or self.move_cnt[side] % GROW_INTERVAL_AFTER == 0
        self.logic[side].insert(0, (nx, ny))
        if not grow:
            self.logic[side].pop()

        # 加速：与当前方向同向长按，每 10 格瘦身 1 格且 -1 分
        if self.held[side] is not None and self.held[side] == self.dir[side]:
            self.boost[side] = True
            self.boost_cnt[side] += 1
            if self.boost_cnt[side] >= 10:
                if len(self.logic[side]) > 1:
                    self.logic[side].pop()
                    self.score[side] = max(0, self.score[side] - 1)
                self.boost_cnt[side] = 0
        else:
            self.boost[side] = False
            self.boost_cnt[side] = 0

    def _check_death(self):
        now = pygame.time.get_ticks()
        dead = {}
        for side, other in (('p1', 'p2'), ('p2', 'p1')):
            hx, hy = self.logic[side][0]
            d = False
            if hx < 0 or hx >= self.grid_w or hy < 0 or hy >= self.grid_h:
                d = True
            elif (hx, hy) in self.logic[side][1:]:
                d = True
            elif (hx, hy) in self.logic[other] and not self._shield_active(side, now):
                d = True
            elif (hx, hy) in self.obstacles:
                d = True
            elif hx < self.safe[0] or hx > self.safe[2] or hy < self.safe[1] or hy > self.safe[3]:
                d = True
            dead[side] = d
        if dead['p1'] and dead['p2']:
            self._end_round(None)
        elif dead['p1']:
            self._end_round('p2')
        elif dead['p2']:
            self._end_round('p1')

    def _check_timeout(self, now):
        """限时模式：时间到按分数判定本局胜负"""
        if self.over or self.round_end_at or self.end_time is None or now < self.end_time:
            return
        if self.score['p1'] > self.score['p2']:
            self._end_round('p1', with_sound=False)
        elif self.score['p2'] > self.score['p1']:
            self._end_round('p2', with_sound=False)
        else:
            self._end_round(None, with_sound=False)

    def _end_round(self, winner, with_sound=True):
        """结束当前局，进入局间休整"""
        if self.over or self.round_end_at:
            return
        now = pygame.time.get_ticks()
        self.round_winner = winner
        if winner:
            self.round_wins[winner] += 1
        self.match_score['p1'] += self.score['p1']
        self.match_score['p2'] += self.score['p2']
        self.round_end_at = now + ROUND_BREAK_MS
        self.round_break_left = ROUND_BREAK_MS
        self.countdown_left = 0
        self._stop_bgm()
        if with_sound:
            self.death_sound.play()
        self._broadcast_state()

    def _advance_round(self):
        """局间休整结束：判定是否结束整场，或开下一局"""
        w1, w2 = self.round_wins['p1'], self.round_wins['p2']
        if max(w1, w2) >= ROUNDS_TO_WIN or self.round_no >= MAX_ROUNDS:
            self._finish_match()
            return
        self.round_no += 1
        self._reset_round()

    def _finish_match(self):
        """整场结束：按局分判定胜负并写入战绩"""
        self.over = True
        self.round_end_at = 0
        self.round_break_left = 0
        w1, w2 = self.round_wins['p1'], self.round_wins['p2']
        if w1 > w2:
            self.winner = 'p1'
        elif w2 > w1:
            self.winner = 'p2'
        else:
            self.winner = None
        self._stop_bgm()
        self._save_record()
        self._broadcast_state()

    def _save_record(self):
        """对局结束写入联机战绩与对局明细（只记一次）"""
        if self._record_saved or not self.over:
            return
        self._record_saved = True
        self_side = 'p1' if self.is_host else 'p2'
        other_side = 'p2' if self.is_host else 'p1'
        if self.winner is None:
            result = 'draw'
        elif self.winner == self_side:
            result = 'win'
        else:
            result = 'lose'
        detail = {
            "time": datetime.now().strftime("%m-%d %H:%M"),
            "my": self.match_score.get(self_side, self.score[self_side]),
            "opp": self.match_score.get(other_side, self.score[other_side]),
            "result": result,
            "mode": "限时" if int(self.room_cfg.get("duration", 0)) else "标准",
            "rounds": f"{self.round_wins[self_side]}-{self.round_wins[other_side]}",
        }
        self.record = update_net_record(result, detail)

    def _shrink_safe(self):
        """危险区收缩：四周各削去一格（保持最小竞技空间）"""
        l, t, r, b = self.safe
        if r - l + 1 > SAFE_MIN_W:
            l += 1
            r -= 1
        if b - t + 1 > SAFE_MIN_H:
            t += 1
            b -= 1
        self.safe = [l, t, r, b]

    # ---------- 同步与渲染辅助 ----------

    def _sync_targets(self, snap=False):
        """把逻辑坐标转成像素目标；长度对齐 smooth 列表；snap 时直接跳变"""
        for side in ('p1', 'p2'):
            self.target[side] = [(x * GRID_SIZE, y * GRID_SIZE) for (x, y) in self.logic[side]]
            sm = self.smooth[side]
            tg = self.target[side]
            if len(sm) < len(tg):
                sm.extend(tg[len(sm):])
            elif len(sm) > len(tg):
                del sm[len(tg):]
            if snap:
                self.smooth[side] = list(tg)

    def _smooth_update(self, dt):
        for side in ('p1', 'p2'):
            interval = self.boost_interval if self.boost[side] else self.normal_interval
            speed = GRID_SIZE / (interval / 1000.0)
            max_move = speed * dt
            sm = self.smooth[side]
            tg = self.target[side]
            for i in range(min(len(sm), len(tg))):
                sx, sy = sm[i]
                tx, ty = tg[i]
                dx = tx - sx
                dy = ty - sy
                dist = math.hypot(dx, dy)
                if dist < 0.1:
                    sm[i] = (tx, ty)
                    continue
                move = min(max_move, dist)
                ratio = move / dist
                sm[i] = (sx + dx * ratio, sy + dy * ratio)

    # ---------- 网络 ----------

    def _times_ms(self, now):
        if self.end_time is None:
            return 0
        return max(0, self.end_time - now)

    def _broadcast_state(self):
        now = pygame.time.get_ticks()
        cd = 0
        if self.countdown_end is not None:
            cd = max(0, self.countdown_end - now)
        self.time_left = self._times_ms(now)
        self.conn.send({
            't': 'state',
            'p1': self.logic['p1'], 'p2': self.logic['p2'],
            'd1': list(self.dir['p1']), 'd2': list(self.dir['p2']),
            'foods': self.foods,
            's1': self.score['p1'], 's2': self.score['p2'],
            'b1': self.boost['p1'], 'b2': self.boost['p2'],
            'over': self.over, 'winner': self.winner, 'cd': cd,
            'obs': [list(o) for o in self.obstacles],
            'gold': list(self.gold) if self.gold else None,
            'gold_left': max(0, self.gold_timer - now) if self.gold else 0,
            'pu': [[p["pos"][0], p["pos"][1], p["kind"]] for p in self.powerups],
            'ef1': [max(0, self.eff['p1']['double'] - now), max(0, self.eff['p1']['shield'] - now)],
            'ef2': [max(0, self.eff['p2']['double'] - now), max(0, self.eff['p2']['shield'] - now)],
            'tl': self.time_left, 'dur': int(self.room_cfg.get('duration', 0)),
            'n1': self.nick['p1'], 'n2': self.nick['p2'],
            'k1': self.skin_id['p1'], 'k2': self.skin_id['p2'],
            'cfg': self.room_cfg,
            # 竞技赛场：局分、连击、危险区
            'rw1': self.round_wins['p1'], 'rw2': self.round_wins['p2'],
            'rn': self.round_no, 'ro': self.round_winner or '',
            'rb': max(0, self.round_end_at - now) if self.round_end_at else 0,
            'ts1': self.match_score['p1'], 'ts2': self.match_score['p2'],
            'c1': self.combo['p1'], 'c2': self.combo['p2'],
            'cb1': max(0, self.combo_until['p1'] - now) if self.combo['p1'] else 0,
            'cb2': max(0, self.combo_until['p2'] - now) if self.combo['p2'] else 0,
            'sd': list(self.safe),
            'mc1': self.max_combo['p1'], 'mc2': self.max_combo['p2'],
        })
        self._last_bc = now

    def _apply_state(self, st):
        """客户端：应用主机快照"""
        old_heads = {s: (self.logic[s][0] if self.logic[s] else None) for s in ('p1', 'p2')}
        prev_total = self.score['p1'] + self.score['p2']
        was_over = self.over

        self.logic = {'p1': [tuple(p) for p in st['p1']],
                      'p2': [tuple(p) for p in st['p2']]}
        self.dir = {'p1': tuple(st['d1']), 'p2': tuple(st['d2'])}
        self.score = {'p1': st['s1'], 'p2': st['s2']}
        self.foods = [tuple(f) for f in st['foods']]
        self.boost = {'p1': st['b1'], 'p2': st['b2']}
        self.countdown_left = st.get('cd', 0)
        self.over = st['over']
        self.winner = st['winner']
        if 'obs' in st:
            self.obstacles = {tuple(o) for o in st['obs']}

        # 竞技赛场状态
        now = pygame.time.get_ticks()
        prev_rb = self.round_end_at
        self.round_wins = {'p1': int(st.get('rw1', 0)),
                           'p2': int(st.get('rw2', 0))}
        self.round_no = int(st.get('rn', 1))
        self.round_winner = (st.get('ro') or None)
        self.round_break_left = int(st.get('rb', 0))
        self.round_end_at = now + self.round_break_left if self.round_break_left else 0
        self.match_score = {'p1': int(st.get('ts1', 0)),
                            'p2': int(st.get('ts2', 0))}
        self.max_combo = {'p1': int(st.get('mc1', 1)),
                          'p2': int(st.get('mc2', 1))}
        self.combo = {'p1': int(st.get('c1', 0)), 'p2': int(st.get('c2', 0))}
        self.combo_until = {'p1': now + int(st.get('cb1', 0)),
                            'p2': now + int(st.get('cb2', 0))}
        if 'sd' in st:
            sd = st['sd']
            if isinstance(sd, (list, tuple)) and len(sd) == 4:
                self.safe = [int(v) for v in sd]
        if self.round_end_at and not prev_rb:
            self.death_sound.play()

        cfg = st.get('cfg')
        if isinstance(cfg, dict):
            self.room_cfg = cfg
            self.normal_interval = int(cfg.get('interval', PLAYER_FIXED_INTERVAL))
            self.boost_interval = int(self.normal_interval * BOOST_SPEED_FACTOR)

        # 昵称与皮肤
        if st.get('n1'):
            self.nick['p1'] = str(st['n1'])[:10]
        if st.get('n2'):
            self.nick['p2'] = str(st['n2'])[:10]
        for key, side in (('k1', 'p1'), ('k2', 'p2')):
            skin = st.get(key)
            if isinstance(skin, int) and 0 <= skin < len(SKINS):
                self.skin_id[side] = skin

        # 道具状态
        now = pygame.time.get_ticks()
        gold = st.get('gold')
        self.gold = tuple(gold) if gold else None
        self.gold_timer = now + int(st.get('gold_left', 0))
        self.powerups = [{"pos": (p[0], p[1]), "kind": p[2]} for p in st.get('pu', [])]
        ef1, ef2 = st.get('ef1', [0, 0]), st.get('ef2', [0, 0])
        self.eff = {
            'p1': {'double': now + int(ef1[0]), 'shield': now + int(ef1[1])},
            'p2': {'double': now + int(ef2[0]), 'shield': now + int(ef2[1])},
        }
        self.time_left = int(st.get('tl', 0))

        if self.score['p1'] + self.score['p2'] > prev_total:
            self.eat_sound.play()
        if self.over and not was_over:
            self._stop_bgm()
            self.death_sound.play()
            self._save_record()
        if not self.over and was_over:
            self._record_saved = False    # 主机已重开新一局

        # 头部突变（重开/严重丢包）时直接跳变，不做平滑
        snap = False
        for s in ('p1', 'p2'):
            oh = old_heads[s]
            nh = self.logic[s][0] if self.logic[s] else None
            if oh is None or nh is None or abs(oh[0] - nh[0]) + abs(oh[1] - nh[1]) > 3:
                snap = True
        self._sync_targets(snap=snap)

    def _poll_network(self):
        for msg in self.conn.poll():
            t = msg.get('t')
            if t == 'bye':
                self.disconnected = True
            elif self.is_host:
                if t == 'hello':
                    nick = str(msg.get('nick', '对手'))[:10]
                    self.nick['p2'] = nick or '对手'
                    skin = msg.get('skin')
                    if isinstance(skin, int) and 0 <= skin < len(SKINS):
                        self.skin_id['p2'] = skin
                elif t == 'dir':
                    try:
                        d = (int(msg['dir'][0]), int(msg['dir'][1]))
                        if d in (UP, DOWN, LEFT, RIGHT):
                            self._set_direction('p2', d)
                    except Exception:
                        pass
                elif t == 'boost':
                    if msg.get('on'):
                        try:
                            d = (int(msg['dir'][0]), int(msg['dir'][1]))
                            if d in (UP, DOWN, LEFT, RIGHT):
                                self.held['p2'] = d
                        except Exception:
                            pass
                    else:
                        self.held['p2'] = None
            else:
                if t == 'state':
                    self._apply_state(msg)

    # ---------- 声音 ----------

    def _stop_bgm(self):
        self.bgm.stop()
        self.bgm_playing = False

    def _start_bgm(self):
        if not self.bgm_playing and not self.over and not self.round_end_at:
            self.bgm.play(-1)
            self.bgm_playing = True

    def _tick_go(self, now):
        """倒计时结束瞬间触发 GO 提示"""
        if self._go_round != self.round_no and self.countdown_left <= 0:
            self._go_round = self.round_no
            self._go_until = now + 600

    # ---------- 主循环 ----------

    def update(self, now):
        self._poll_network()
        if not self.conn.alive and not self.disconnected:
            self.disconnected = True
            self._stop_bgm()

        if self.disconnected or self.over or self.confirm_leave:
            if self.bgm_playing:
                self._stop_bgm()
            return
        self._start_bgm()

        dt = min(0.05, (now - getattr(self, '_last_dt', now)) / 1000.0)
        self._last_dt = now

        if self.is_host:
            # 局间休整：冻结操作并展示本局结果
            if self.round_end_at:
                if now < self.round_end_at:
                    self.round_break_left = self.round_end_at - now
                    if now - self._last_bc >= 200:
                        self._broadcast_state()
                    self._smooth_update(dt)
                    return
                self.round_end_at = 0
                self.round_break_left = 0
                self._advance_round()
                if self.over:
                    return

            if now < self.countdown_end:
                self.countdown_left = self.countdown_end - now
                if now - self._last_bc >= 200:
                    self._broadcast_state()
                self._smooth_update(dt)
                return
            self.countdown_left = 0
            self._tick_go(now)
            for side in ('p1', 'p2'):
                interval = self.boost_interval if self.boost[side] else self.normal_interval
                if not self.smooth[side] or not self.target[side]:
                    continue
                ready = (abs(self.smooth[side][0][0] - self.target[side][0][0]) < 0.5 and
                         abs(self.smooth[side][0][1] - self.target[side][0][1]) < 0.5)
                if ready and now - self.last_move[side] >= interval:
                    self.last_move[side] = now
                    self._move(side)
                    self._sync_targets()
                    self._check_death()
                    self._broadcast_state()
                    if self.over or self.round_end_at:
                        break

            # 标准模式：危险区周期收缩，逼迫双方交战
            if (not self.round_end_at and self.end_time is None and
                    now - self._round_start >= SUDDEN_DEATH_START_MS and
                    now >= self._shrink_at):
                self._shrink_at = now + SHRINK_INTERVAL_MS
                self._shrink_safe()
                self._check_death()

            # 金色食物到期 / 强化道具生成 / 限时判定
            if self.gold and now > self.gold_timer:
                self.gold = None
            if now >= self.next_powerup_time:
                self.next_powerup_time = now + POWERUP_INTERVAL
                self._spawn_powerup(now)
            self._check_timeout(now)

            if self.over or self.round_end_at:
                self._broadcast_state()
            elif now - self._last_bc >= 500:
                self._broadcast_state()
            self._smooth_update(dt)
        else:
            if self.countdown_left > 0:
                self.countdown_left = max(0, self.countdown_left - dt * 1000)
            self._tick_go(now)
            if self.round_end_at:
                self.round_break_left = max(0, self.round_end_at - now)
            self._smooth_update(dt)

    def _draw_snake(self, smooth, head_color, c1, c2):
        n = len(smooth)
        for idx, (px, py) in enumerate(smooth):
            rect = pygame.Rect(px, py, GRID_SIZE, GRID_SIZE)
            if idx == 0:
                pygame.draw.rect(self.screen, head_color, rect, 0, 6)
            else:
                k = idx / n
                r = int(c1[0] * (1 - k) + c2[0] * k)
                g = int(c1[1] * (1 - k) + c2[1] * k)
                b = int(c1[2] * (1 - k) + c2[2] * k)
                pygame.draw.rect(self.screen, (r, g, b), rect, 0, 4)

    def _skin_of(self, side):
        return SKINS[self.skin_id.get(side, 0) % len(SKINS)]

    def _draw_food_and_powerups(self):
        for fx, fy in self.foods:
            fcx = fx * GRID_SIZE + GRID_SIZE // 2
            fcy = fy * GRID_SIZE + GRID_SIZE // 2
            pygame.draw.circle(self.screen, RED, (fcx, fcy), GRID_SIZE // 2 - 2)

        if self.gold:
            gx, gy = self.gold
            gcx, gcy = gx * GRID_SIZE + GRID_SIZE // 2, gy * GRID_SIZE + GRID_SIZE // 2
            remaining = self.gold_timer - pygame.time.get_ticks()
            if 0 < remaining < 1000:
                gcolor = (255, 255, 150) if (pygame.time.get_ticks() // 100) % 2 == 0 else (255, 140, 0)
            else:
                gcolor = GOLD
            pts = []
            for i in range(6):
                a = i * math.pi / 3 - math.pi / 6
                pts.append((gcx + math.cos(a) * 9, gcy + math.sin(a) * 9))
                pts.append((gcx + math.cos(a + math.pi/6) * 4.5, gcy + math.sin(a + math.pi/6) * 4.5))
            pygame.draw.polygon(self.screen, gcolor, pts)

        for pu in self.powerups:
            px, py = pu["pos"]
            pcx, pcy = px * GRID_SIZE + GRID_SIZE // 2, py * GRID_SIZE + GRID_SIZE // 2
            color = MAGENTA if pu["kind"] == "double" else CYAN
            pts = [(pcx, pcy - int(9 * UI_SCALE)), (pcx + int(9 * UI_SCALE), pcy), (pcx, pcy + int(9 * UI_SCALE)), (pcx - int(9 * UI_SCALE), pcy)]
            pygame.draw.polygon(self.screen, color, pts)
            pygame.draw.polygon(self.screen, WHITE, pts, 2)

    def _draw_danger_zone(self):
        """危险区外的红色警戒遮罩（缓存，仅收缩时重建）"""
        full = [0, 0, self.grid_w - 1, self.grid_h - 1]
        if list(self.safe) == full:
            return
        key = tuple(self.safe)
        if key != self._danger_key:
            self._danger_key = key
            l, t, r, b = self.safe
            surf = pygame.Surface((self.window_w, self.window_h), pygame.SRCALPHA)
            tint = (150, 25, 40, 88)
            pygame.draw.rect(surf, tint, (0, 0, l * GRID_SIZE, self.window_h))
            pygame.draw.rect(surf, tint, ((r + 1) * GRID_SIZE, 0,
                                          (self.grid_w - r - 1) * GRID_SIZE, self.window_h))
            pygame.draw.rect(surf, tint, (l * GRID_SIZE, 0,
                                          (r - l + 1) * GRID_SIZE, t * GRID_SIZE))
            pygame.draw.rect(surf, tint, (l * GRID_SIZE, (b + 1) * GRID_SIZE,
                                          (r - l + 1) * GRID_SIZE,
                                          (self.grid_h - b - 1) * GRID_SIZE))
            inner = pygame.Rect(l * GRID_SIZE, t * GRID_SIZE,
                                (r - l + 1) * GRID_SIZE, (b - t + 1) * GRID_SIZE)
            pygame.draw.rect(surf, (235, 60, 70, 220), inner, 3)
            self._danger_surf = surf
        self.screen.blit(self._danger_surf, (0, 0))

    def _draw_scoreboard(self, self_side, other_side, now):
        """顶部竞技计分板：我方 / 局分 / 对方"""
        w, h = 680, 104
        x = self.window_w // 2 - w // 2
        y = 4
        cx = self.window_w // 2

        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(panel, (8, 16, 34, 225), (0, 0, w, h), 0, 16)
        pygame.draw.rect(panel, (70, 150, 205, 130), (0, 0, w, h), 1, 16)
        pygame.draw.line(panel, (60, 110, 160, 110), (w // 2 - 70, 14),
                         (w // 2 - 70, h - 14), 1)
        pygame.draw.line(panel, (60, 110, 160, 110), (w // 2 + 70, 14),
                         (w // 2 + 70, h - 14), 1)
        self.screen.blit(panel, (x, y))

        def short_name(side):
            nick = self.nick[side]
            return nick[:6] + "…" if len(nick) > 6 else nick

        my_score = self.score[self_side]
        opp_score = self.score[other_side]
        lead_color = GOLD if my_score > opp_score else WHITE
        opp_color = GOLD if opp_score > my_score else WHITE

        self.screen.blit(render_text(self.font20, f"我方 · {short_name(self_side)}", CYAN),
                         (x + int(20 * UI_SCALE), y + int(8 * UI_SCALE)))
        self.screen.blit(render_text(self.font34, str(my_score), lead_color),
                         (x + int(20 * UI_SCALE), y + int(30 * UI_SCALE)))

        opp_name = render_text(self.font20, f"对方 · {short_name(other_side)}", (255, 150, 150))
        self.screen.blit(opp_name, (x + w - int(20 * UI_SCALE) - opp_name.get_width(), y + int(8 * UI_SCALE)))
        opp_surf = render_text(self.font34, str(opp_score), opp_color)
        self.screen.blit(opp_surf, (x + w - int(20 * UI_SCALE) - opp_surf.get_width(), y + int(30 * UI_SCALE)))

        # 中央：局号、局分、状态
        round_surf = render_text(self.font20, f"第 {self.round_no} 局", (160, 190, 225))
        self.screen.blit(round_surf, round_surf.get_rect(center=(cx, y + int(16 * UI_SCALE))))
        wins = f"{self.round_wins[self_side]} : {self.round_wins[other_side]}"
        wins_surf = render_text(self.font34, wins, (255, 235, 150))
        self.screen.blit(wins_surf, wins_surf.get_rect(center=(cx, y + int(48 * UI_SCALE))))

        duration = int(self.room_cfg.get("duration", 0))
        if duration or self.time_left > 0:
            sec = max(0, int(self.time_left / 1000))
            status = f"剩余 {sec}s"
            color = RED if sec <= 10 else CYAN
        elif list(self.safe) != [0, 0, self.grid_w - 1, self.grid_h - 1]:
            status = "危险区收缩中"
            color = (255, 90, 90)
        else:
            status = "三局两胜"
            color = (140, 170, 200)
        status_surf = render_text(self.font20, status, color)
        self.screen.blit(status_surf, status_surf.get_rect(center=(cx, y + int(82 * UI_SCALE))))

        # 连击展示（窗口内且 ≥2 连）
        for side, color in ((self_side, GOLD), (other_side, (255, 140, 140))):
            combo = self.combo.get(side, 0)
            remain = self.combo_until.get(side, 0) - now
            if combo < 2 or remain <= 0:
                continue
            text = f"{'我方' if side == self_side else '对方'}连击 ×{combo}"
            surf = render_text(self.font20, text, color)
            if side == self_side:
                self.screen.blit(surf, (x + int(20 * UI_SCALE), y + h + int(6 * UI_SCALE)))
                bar_w = int(120 * min(1.0, remain / COMBO_WINDOW_MS))
                pygame.draw.rect(self.screen, (60, 45, 20), (x + 20, y + h + 30, 120, 4), 0, 2)
                pygame.draw.rect(self.screen, color, (x + 20, y + h + 30, bar_w, 4), 0, 2)
            else:
                self.screen.blit(surf, (x + w - int(20 * UI_SCALE) - surf.get_width(), y + h + int(6 * UI_SCALE)))

    def _draw_round_break(self, self_side, other_side):
        """局间休整：本局结果 + 下一局倒计时"""
        cx = self.window_w // 2
        cy = self.window_h // 2
        overlay = pygame.Surface((self.window_w, self.window_h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 120))
        self.screen.blit(overlay, (0, 0))

        w, h = 560, 230
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(panel, (10, 18, 38, 235), (0, 0, w, h), 0, 18)
        pygame.draw.rect(panel, (80, 180, 230, 180), (0, 0, w, h), 2, 18)
        self.screen.blit(panel, (cx - w // 2, cy - h // 2))

        title = render_text(self.font28, f"第 {self.round_no} 局结束", (190, 220, 245))
        self.screen.blit(title, title.get_rect(center=(cx, cy - h // 2 + int(34 * UI_SCALE))))

        if self.round_winner == self_side:
            text, color = "你赢下了本局", (120, 255, 170)
        elif self.round_winner:
            text, color = "对方赢下了本局", (255, 110, 110)
        else:
            text, color = "本局平局", (220, 220, 220)
        res = render_text(self.font48, text, color)
        self.screen.blit(res, res.get_rect(center=(cx, cy - 30)))

        score = render_text(self.font28,
                            f"本局 {self.score[self_side]} : {self.score[other_side]}"
                            f"    局分 {self.round_wins[self_side]} : {self.round_wins[other_side]}",
                            (210, 230, 245))
        self.screen.blit(score, score.get_rect(center=(cx, cy + int(30 * UI_SCALE))))

        if (max(self.round_wins.values()) >= ROUNDS_TO_WIN
                or self.round_no >= MAX_ROUNDS):
            nxt = render_text(self.font20, "即将公布比赛结果…", (255, 220, 130))
        else:
            sec = max(0.0, self.round_break_left / 1000.0)
            nxt = render_text(self.font20,
                              f"{sec:.1f} 秒后开始第 {self.round_no + 1} 局", (160, 190, 220))
        self.screen.blit(nxt, nxt.get_rect(center=(cx, cy + int(78 * UI_SCALE))))

    def draw(self):
        self.screen.fill(DEEP_BLUE)
        for s in self.stars:
            s.update()
            s.draw(self.screen)

        self.screen.blit(get_grid_overlay(self.window_w, self.window_h, GRID_SIZE, GRAY), (0, 0))
        self._draw_danger_zone()

        # 障碍物（深棕色，借鉴论文地图设计）
        for ox, oy in self.obstacles:
            rect = pygame.Rect(ox * GRID_SIZE, oy * GRID_SIZE, GRID_SIZE, GRID_SIZE)
            pygame.draw.rect(self.screen, (101, 67, 33), rect, 0, 3)
            pygame.draw.rect(self.screen, (60, 40, 20), rect, 2, 3)

        self._draw_food_and_powerups()

        self_side = 'p1' if self.is_host else 'p2'
        other_side = 'p2' if self.is_host else 'p1'
        own_skin = self._skin_of(self_side)
        opp_skin = self._skin_of(other_side)
        self._draw_snake(self.smooth[self_side], own_skin["head"], own_skin["c1"], own_skin["c2"])
        self._draw_snake(self.smooth[other_side], opp_skin["head"], opp_skin["c1"], opp_skin["c2"])

        now = pygame.time.get_ticks()
        self._draw_scoreboard(self_side, other_side, now)

        duration = int(self.room_cfg.get("duration", 0))
        mode_surf = render_text(
            self.font20,
            f"联机对战 | 障碍 {self.room_cfg.get('obstacles', 0)} | 食物 {self.room_cfg.get('foods', FOOD_COUNT)}"
            + (f" | 限时 {duration}s" if duration else " | 标准赛"),
            (120, 150, 190))
        self.screen.blit(mode_surf, (int(16 * UI_SCALE), self.window_h - int(30 * UI_SCALE)))

        # 增益状态 HUD
        y_hud = 132
        d_left = max(0, self.eff[self_side]["double"] - now) // 1000
        s_left = max(0, self.eff[self_side]["shield"] - now) // 1000
        if d_left > 0:
            self.screen.blit(render_text(self.font20, f"双倍得分 {d_left}s", MAGENTA), (int(16 * UI_SCALE), y_hud))
            y_hud += 22
        if s_left > 0:
            self.screen.blit(render_text(self.font20, f"穿身护盾 {s_left}s", CYAN), (int(16 * UI_SCALE), y_hud))

        cx = self.window_w // 2
        cy = self.window_h // 2

        if not self.over and not self.disconnected and not self.confirm_leave:
            if self.countdown_left > 0:
                n = max(1, math.ceil(self.countdown_left / 1000))
                cd_surf = render_text(self.font64, str(n), CYAN)
                self.screen.blit(cd_surf, cd_surf.get_rect(center=(cx, cy)))
            elif now < self._go_until and not self.round_end_at:
                go = render_text(self.font64, "GO!", (110, 255, 170))
                self.screen.blit(go, go.get_rect(center=(cx, cy)))

        if self.round_end_at and not self.over and not self.disconnected and not self.confirm_leave:
            self._draw_round_break(self_side, other_side)

        if self.over and not self.over_ui.active:
            if self.winner == self_side:
                self.over_ui.set_title("胜 利 !", ((255, 255, 210), (255, 205, 70)))
            elif self.winner == other_side:
                self.over_ui.set_title("惜 败", ((255, 225, 225), (255, 90, 90)))
            else:
                self.over_ui.set_title("平 局", ((240, 250, 255), (160, 195, 235)))
            rec = self.record
            sub = (f"局分 {self.round_wins[self_side]} - {self.round_wins[other_side]}"
                   f"    总得分 {self.match_score[self_side]} : {self.match_score[other_side]}"
                   f"\n最高连击 ×{self.max_combo[self_side]}"
                   f"    联机战绩 {rec['win']}胜 {rec['lose']}负 {rec['draw']}平")
            if not self.is_host:
                sub += "\n等待主机再来一场..."
            self.over_ui.set_subtitle(sub)
            self.over_ui.open()

        self.over_ui.sync(self.over)
        self.confirm_ui.sync(self.confirm_leave and not self.over)
        self.disconnect_ui.sync(self.disconnected)

        if not self.quit_ui.active:
            if self.over_ui.is_visible():
                self.over_ui.draw(self.screen)
            if self.confirm_ui.is_visible():
                self.confirm_ui.draw(self.screen)
            if self.disconnect_ui.is_visible():
                self.disconnect_ui.draw(self.screen)
        if self.quit_ui.is_visible():
            self.quit_ui.draw(self.screen)

        pygame.display.flip()

    # ---------- 事件 ----------

    def _leave(self):
        try:
            self.conn.send({'t': 'bye'})
        except Exception:
            pass
        self.conn.close()
        self._stop_bgm()

    def _quit_game(self):
        self._leave()
        pygame.quit()
        sys.exit()

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            self._leave()
            pygame.quit()
            sys.exit()

        if self.disconnected:
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                self._leave()
                return False
            return True

        if self.quit_ui.handle_event(event):
            return True

        if self.over:
            clicked = self.over_ui.clicked(event)
            if clicked == "再来一场":
                self.reset()
                return True
            if clicked == "返回菜单":
                self._leave()
                self.over_ui.close()
                return False
            if clicked == "退出游戏":
                self.quit_ui.open()
                return True
            if event.type == pygame.KEYDOWN and key_is("menu", event.key):
                self._leave()
                self.over_ui.close()
                return False
            return True

        if self.confirm_leave:
            clicked = self.confirm_ui.clicked(event)
            if clicked == "继续对战":
                self.confirm_leave = False
                self.confirm_ui.close()
            elif clicked == "离开对战":
                self._leave()
                self.confirm_ui.close()
                return False
            if event.type == pygame.KEYDOWN and key_is("menu", event.key):
                self.confirm_leave = False
                self.confirm_ui.close()
            return True

        # 局间休整：只允许打开离开确认，避免输入堆积到下一局
        if self.round_end_at:
            if event.type == pygame.KEYDOWN and key_is("menu", event.key):
                self.confirm_leave = True
                self.confirm_ui.open()
            return True

        if event.type == pygame.KEYDOWN:
            if key_is("menu", event.key):
                self.confirm_leave = True
                self.confirm_ui.open()
                return True
            key_dir = None
            if key_is("up", event.key):
                key_dir = UP
            elif key_is("down", event.key):
                key_dir = DOWN
            elif key_is("left", event.key):
                key_dir = LEFT
            elif key_is("right", event.key):
                key_dir = RIGHT
            if key_dir is not None:
                if self.is_host:
                    self._set_direction('p1', key_dir)
                    self.held['p1'] = key_dir
                else:
                    self.conn.send({'t': 'dir', 'dir': list(key_dir)})
                    self.conn.send({'t': 'boost', 'on': True, 'dir': list(key_dir)})
        elif event.type == pygame.KEYUP:
            if any(key_is(a, event.key) for a in ("up", "down", "left", "right")):
                if self.is_host:
                    self.held['p1'] = None
                else:
                    self.conn.send({'t': 'boost', 'on': False})
        return True
