import pygame
import sys
import math
import random
import numpy as np
from config import *
from ui import Button, Star, ModalOverlay, QuitConfirm
from utils import UI_SCALE, create_bg, get_ui_font, resource_path, generate_obstacles, sound_volume, key_is, render_text, get_grid_overlay

FOOD_COUNT = 3

class VersusGame:
    def __init__(self, screen, difficulty="normal", duration=0):
        self.screen = screen
        self.difficulty = difficulty
        self.duration = duration          # 限时秒数，0 表示不限时
        self.clock = pygame.time.Clock()
        self.running = True
        self.pause = False
        self.over = False
        self.winner = None

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

        self.ai_interval = DIFFICULTY_CFG[difficulty]["interval"]
        self.player_normal_interval = PLAYER_FIXED_INTERVAL
        self.player_boost_interval = int(self.player_normal_interval * BOOST_SPEED_FACTOR)

        self.font20 = get_ui_font(20)
        self.font22 = get_ui_font(22)
        self.font28 = get_ui_font(28)
        self.font48 = get_ui_font(48)

        bg_path = create_bg(self.window_w, self.window_h)
        self.bg = pygame.image.load(bg_path).convert() if bg_path else None
        self.stars = [Star(self.window_w, self.window_h) for _ in range(80)]

        self.pause_menu_active = False
        window = (self.window_w, self.window_h)
        self.pause_ui = ModalOverlay(
            window, "暂停", self.font48, self.font22, self.font28,
            [("继续游戏", "primary"), ("重新开始", "card"),
             ("返回菜单", "card"), ("退出游戏", "ghost")],
            accent=(80, 220, 255))
        self.pause_ui.set_subtitle("游戏已暂停 · P 继续 · ESC 关闭")
        self.pause_badge = ModalOverlay(
            window, "已暂停", self.font48, self.font22, self.font28, [],
            min_width=320, accent=(80, 220, 255))
        self.pause_badge.set_subtitle("按 P 继续 · ESC 打开菜单")
        self.over_ui = ModalOverlay(
            window, "对局结束", self.font48, self.font22, self.font28,
            [("重新开始", "primary"), ("返回菜单", "card"),
             ("退出游戏", "ghost")],
            accent=(255, 205, 110),
            title_colors=((255, 250, 220), (255, 190, 60)))
        self.quit_ui = QuitConfirm(window, self.font48, self.font22, self.font28,
                                   self._quit_game)
        self.reset()

    def _quit_game(self):
        pygame.quit()
        sys.exit()

    def reset(self):
        self.player_speed = GRID_SIZE / (self.player_normal_interval / 1000.0)
        self.ai_speed = GRID_SIZE / (self.ai_interval / 1000.0)
        self.last_player_move = pygame.time.get_ticks()
        self.last_ai_move = pygame.time.get_ticks()

        # 对角出生（借鉴论文：左下角 vs 右上角），自动成长计数
        self.player_logic = [(4, self.grid_h - 4)]
        self.ai_logic = [(self.grid_w - 5, 3)]
        # 空间换时间：身体占用集合，碰撞/BFS 查询 O(1)
        self.player_full = set(self.player_logic)
        self.player_body = set(self.player_logic[1:])
        self.ai_full = set(self.ai_logic)
        self.ai_body = set(self.ai_logic[1:])
        self.player_move_cnt = 0
        self.ai_move_cnt = 0

        self.player_dir = RIGHT
        self.player_next_dir = RIGHT
        self.ai_dir = LEFT
        self.ai_next_dir = LEFT

        self.player_score = 0
        self.ai_score = 0

        # 随机障碍（带连通性校验，数量随难度）
        self.obstacles = generate_obstacles(
            self.grid_w, self.grid_h, OBSTACLE_COUNT[self.difficulty],
            self.player_logic[0], self.ai_logic[0])

        self.player_target = [(x*GRID_SIZE, y*GRID_SIZE) for (x,y) in self.player_logic]
        self.player_smooth = self.player_target.copy()
        self.ai_target = [(x*GRID_SIZE, y*GRID_SIZE) for (x,y) in self.ai_logic]
        self.ai_smooth = self.ai_target.copy()

        self.foods = []
        self._respawn_all_foods()

        # 金色特殊食物 / 强化道具 / 增益效果
        now = pygame.time.get_ticks()
        self.gold = None
        self.gold_timer = 0
        self.powerups = []                 # [{"pos": (x,y), "kind": "double"/"shield"}]
        self.next_powerup_time = now + POWERUP_INTERVAL
        self.eff = {
            "player": {"double": 0, "shield": 0},
            "ai": {"double": 0, "shield": 0},
        }

        self.over = False
        self.winner = None
        self.pause = False
        self.pause_menu_active = False
        self.bgm_playing = False
        self.end_time = (now + self.duration * 1000) if self.duration else None

        self.skin = SKINS[SETTINGS["skin"] % len(SKINS)]

        self.player_boost_active = False
        self.player_boost_counter = 0
        self.held_dir = None
        if hasattr(self, "pause_ui"):
            self.pause_ui.close()
            self.pause_badge.close()
            self.over_ui.close()
            self.quit_ui.close()

    def _stop_bgm(self):
        self.bgm.stop()
        self.bgm_playing = False

    def _start_bgm(self):
        if not self.bgm_playing and not self.pause and not self.over:
            self.bgm.play(-1)
            self.bgm_playing = True

    def _random_empty_pos(self, exclude_set=None):
        occupied = self.player_full | self.ai_full | set(self.foods) | self.obstacles
        if getattr(self, "gold", None):
            occupied.add(self.gold)
        for pu in getattr(self, "powerups", []):
            occupied.add(pu["pos"])
        if exclude_set:
            occupied.update(exclude_set)
        if len(occupied) >= self.grid_w * self.grid_h:
            return None
        for _ in range(MAX_ATTEMPTS):
            x = random.randint(0, self.grid_w-1)
            y = random.randint(0, self.grid_h-1)
            if (x, y) not in occupied:
                return (x, y)
        for x in range(self.grid_w):
            for y in range(self.grid_h):
                if (x, y) not in occupied:
                    return (x, y)
        return None

    def _respawn_all_foods(self):
        self.foods.clear()
        for _ in range(FOOD_COUNT):
            pos = self._random_empty_pos()
            if pos:
                self.foods.append(pos)

    def _respawn_one_food(self):
        pos = self._random_empty_pos()
        if pos:
            self.foods.append(pos)

    def _spawn_gold(self, now):
        """吃完普通食物后按概率生成金色特殊食物（限时 5 秒）"""
        if self.gold is not None or random.random() >= GOLD_FOOD_CHANCE:
            return
        pos = self._random_empty_pos()
        if pos:
            self.gold = pos
            self.gold_timer = now + GOLD_FOOD_DURATION

    def _spawn_powerup(self, now):
        """周期生成强化道具（场上最多 POWERUP_MAX 个）"""
        if len(self.powerups) >= POWERUP_MAX:
            return
        pos = self._random_empty_pos()
        if pos is None:
            return
        kind = random.choice(POWERUP_TYPES)
        self.powerups.append({"pos": pos, "kind": kind})

    def _apply_powerup(self, side, kind, now):
        if kind == "double":
            self.eff[side]["double"] = now + POWERUP_DOUBLE_MS
        else:
            self.eff[side]["shield"] = now + POWERUP_SHIELD_MS

    def _gain(self, side, base, now):
        """加分（双倍得分生效时翻倍）"""
        if self.eff[side]["double"] > now:
            base *= 2
        if side == "player":
            self.player_score += base
        else:
            self.ai_score += base

    def _double_left(self, side, now):
        return max(0, self.eff[side]["double"] - now)

    def _shield_left(self, side, now):
        return max(0, self.eff[side]["shield"] - now)

    def _finish_by_time(self):
        if self.over:
            return
        self.over = True
        if self.player_score > self.ai_score:
            self.winner = "player"
        elif self.ai_score > self.player_score:
            self.winner = "ai"
        else:
            self.winner = None
        self._stop_bgm()

    def set_player_direction(self, new_dir):
        if len(self.player_logic) >= 3:
            if (self.player_dir == UP and new_dir == DOWN) or (self.player_dir == DOWN and new_dir == UP):
                return
            if (self.player_dir == LEFT and new_dir == RIGHT) or (self.player_dir == RIGHT and new_dir == LEFT):
                return
        self.player_next_dir = new_dir

    def _ai_choose_direction(self):
        head = self.ai_logic[0]
        # 头已经越界（刚撞墙，本帧还没判定死亡）：直接保持当前方向，别再去
        # 做 BFS —— 越界坐标写进障碍矩阵会直接 IndexError 崩掉游戏
        if not (0 <= head[0] < self.grid_w and 0 <= head[1] < self.grid_h):
            return self.ai_dir
        player_head = self.player_logic[0] if self.player_logic else None

        # 找最近食物
        food_target = None
        food_dist = float('inf')
        if self.foods:
            food_target = min(self.foods, key=lambda p: abs(p[0] - head[0]) + abs(p[1] - head[1]))
            food_dist = abs(food_target[0] - head[0]) + abs(food_target[1] - head[1])

        # 到玩家的距离
        player_dist = float('inf')
        if player_head:
            player_dist = abs(head[0] - player_head[0]) + abs(head[1] - player_head[1])

        # 食物优先阈值（随难度变化）
        if self.difficulty == "easy":
            food_priority_threshold = 12
        elif self.difficulty == "normal":
            food_priority_threshold = 15
        else:
            food_priority_threshold = 18

        # 拦截阈值统一为 2 格
        intercept_threshold = 4

        target = None

        # 如果食物距离在阈值内，直接去吃
        if food_target and food_dist <= food_priority_threshold:
            target = food_target
        else:
            # 食物较远，若玩家距离 ≤ 2 则考虑拦截
            if player_head and player_dist <= intercept_threshold:
                p_dir = self.player_dir
                # 计算玩家左右方向（垂直方向）
                left_dir = (-p_dir[1], p_dir[0])
                right_dir = (p_dir[1], -p_dir[0])
                # 预测点：玩家前方 6 步 + 侧移 1 格（左右各一个候选）
                candidates = []
                for steps in [6, 8, 10]:
                    for side_dir in [left_dir, right_dir]:
                        future_x = player_head[0] + p_dir[0] * steps + side_dir[0] * 1
                        future_y = player_head[1] + p_dir[1] * steps + side_dir[1] * 1
                        if 0 <= future_x < self.grid_w and 0 <= future_y < self.grid_h:
                            candidates.append((future_x, future_y))
                # 选择距离 AI 最近且可达的候选点
                best_candidate = None
                best_dist = float('inf')
                for cand in candidates:
                    dist_cand = abs(head[0]-cand[0]) + abs(head[1]-cand[1])
                    if dist_cand < best_dist:
                        path = self._bfs_path(head, cand, self.ai_logic)
                        if path and len(path) > 1:
                            best_candidate = cand
                            best_dist = dist_cand
                            break  # 找到第一个可行即可（已按步骤排序）
                if best_candidate:
                    target = best_candidate
                else:
                    # 若无侧面预测点，直接尝试到玩家头部，但概率较低
                    path = self._bfs_path(head, player_head, self.ai_logic)
                    if path and len(path) > 1:
                        target = player_head

        # 若仍无目标，回到最近食物
        if target is None and food_target:
            target = food_target

        # BFS 寻路到目标
        if target:
            path = self._bfs_path(head, target, self.ai_logic)
            if path and len(path) > 1:
                next_cell = path[1]
                return (next_cell[0] - head[0], next_cell[1] - head[1])

        # 安全方向（无路径时）
        safe_dirs = []
        for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
            nx, ny = head[0] + dx, head[1] + dy
            if 0 <= nx < self.grid_w and 0 <= ny < self.grid_h:
                if (nx, ny) not in self.ai_body and (nx, ny) not in self.player_full \
                        and (nx, ny) not in self.obstacles:
                    safe_dirs.append((dx, dy))
        if safe_dirs:
            if target:
                return min(safe_dirs, key=lambda d: abs(head[0]+d[0]-target[0]) + abs(head[1]+d[1]-target[1]))
            return random.choice(safe_dirs)
        return self.ai_dir

    def _bfs_path(self, start, target, snake_body):
        if start == target:
            return [start]
        # 复用障碍/访问缓冲，避免每次 BFS 重新分配（空间换时间）
        if getattr(self, "_bfs_obstacle", None) is None:
            self._bfs_obstacle = np.zeros((self.grid_w, self.grid_h), dtype=bool)
            self._bfs_visited = np.zeros((self.grid_w, self.grid_h), dtype=bool)
        else:
            self._bfs_obstacle.fill(False)
            self._bfs_visited.fill(False)
        obstacle = self._bfs_obstacle
        visited = self._bfs_visited
        for seg in self.ai_body:
            if 0 <= seg[0] < self.grid_w and 0 <= seg[1] < self.grid_h:
                obstacle[seg[0], seg[1]] = True
        for seg in self.player_full:
            if 0 <= seg[0] < self.grid_w and 0 <= seg[1] < self.grid_h:
                obstacle[seg[0], seg[1]] = True
        for seg in self.obstacles:
            if 0 <= seg[0] < self.grid_w and 0 <= seg[1] < self.grid_h:
                obstacle[seg[0], seg[1]] = True

        queue = [(start, [start])]
        visited[start[0], start[1]] = True
        dirs = [(1,0), (-1,0), (0,1), (0,-1)]
        idx = 0
        while idx < len(queue):
            (x, y), path = queue[idx]
            idx += 1
            for dx, dy in dirs:
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.grid_w and 0 <= ny < self.grid_h:
                    if (nx, ny) == target:
                        return path + [(nx, ny)]
                    if not visited[nx, ny] and not obstacle[nx, ny]:
                        visited[nx, ny] = True
                        queue.append(((nx, ny), path + [(nx, ny)]))
        return None

    # ---------- 身体索引（空间换时间） ----------

    def _advance_body(self, side, new_head, grow):
        """推进蛇身并同步 full/body 集合，语义与列表切片完全一致"""
        if side == "player":
            logic, full, body = self.player_logic, self.player_full, self.player_body
        else:
            logic, full, body = self.ai_logic, self.ai_full, self.ai_body
        old_head = logic[0]
        logic.insert(0, new_head)
        full.add(new_head)
        if grow:
            body.add(old_head)
        else:
            tail = logic.pop()
            if tail != new_head:
                full.discard(tail)
            body.discard(tail)
            if tail != old_head:
                body.add(old_head)

    def _shrink_body(self, side):
        """加速瘦身：移除尾格并同步集合"""
        if side == "player":
            logic, full, body = self.player_logic, self.player_full, self.player_body
        else:
            logic, full, body = self.ai_logic, self.ai_full, self.ai_body
        if len(logic) > 1:
            tail = logic.pop()
            full.discard(tail)
            body.discard(tail)

    def _move_player(self):
        old_dir = self.player_dir
        self.player_dir = self.player_next_dir
        new_dir = self.player_dir

        hx, hy = self.player_logic[0]
        nx, ny = hx + new_dir[0], hy + new_dir[1]
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
            self._gain("player", 1, now)
            self.foods.remove((nx, ny))
            self._respawn_one_food()
            self._spawn_gold(now)
        elif ate_gold:
            self.eat_sound.play()
            self._gain("player", GOLD_FOOD_SCORE, now)
            self.gold = None
        if pu_kind:
            self.eat_sound.play()
            self._apply_powerup("player", pu_kind, now)
        self.player_move_cnt += 1
        # 自动成长（借鉴论文：初期每步长1格，后期每3步长1格）
        grow = ate or ate_gold or self.player_move_cnt <= GROW_EVERY_STEP_UNTIL \
            or self.player_move_cnt % GROW_INTERVAL_AFTER == 0
        self._advance_body("player", (nx, ny), grow)
        self.player_target = [(x*GRID_SIZE, y*GRID_SIZE) for (x,y) in self.player_logic]
        if len(self.player_smooth) < len(self.player_target):
            self.player_smooth.append(self.player_target[-1])
        elif len(self.player_smooth) > len(self.player_target):
            self.player_smooth = self.player_smooth[:len(self.player_target)]

        # 玩家加速
        if self.held_dir == new_dir and self.held_dir is not None:
            self.player_boost_active = True
            self.player_boost_counter += 1
            if self.player_boost_counter >= 10:
                if len(self.player_logic) > 1:
                    self._shrink_body("player")
                    self.player_score = max(0, self.player_score - 1)
                    self.player_target = [(x*GRID_SIZE, y*GRID_SIZE) for (x,y) in self.player_logic]
                    if len(self.player_smooth) > len(self.player_target):
                        self.player_smooth = self.player_smooth[:len(self.player_target)]
                self.player_boost_counter = 0
        else:
            self.player_boost_active = False
            self.player_boost_counter = 0

        return True

    def _move_ai(self):
        self.ai_dir = self._ai_choose_direction()
        hx, hy = self.ai_logic[0]
        nx, ny = hx + self.ai_dir[0], hy + self.ai_dir[1]
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
            self._gain("ai", 1, now)
            self.foods.remove((nx, ny))
            self._respawn_one_food()
            self._spawn_gold(now)
        elif ate_gold:
            self.eat_sound.play()
            self._gain("ai", GOLD_FOOD_SCORE, now)
            self.gold = None
        if pu_kind:
            self.eat_sound.play()
            self._apply_powerup("ai", pu_kind, now)
        self.ai_move_cnt += 1
        grow = ate or ate_gold or self.ai_move_cnt <= GROW_EVERY_STEP_UNTIL \
            or self.ai_move_cnt % GROW_INTERVAL_AFTER == 0
        self._advance_body("ai", (nx, ny), grow)
        self.ai_target = [(x*GRID_SIZE, y*GRID_SIZE) for (x,y) in self.ai_logic]
        if len(self.ai_smooth) < len(self.ai_target):
            self.ai_smooth.append(self.ai_target[-1])
        elif len(self.ai_smooth) > len(self.ai_target):
            self.ai_smooth = self.ai_smooth[:len(self.ai_target)]
        return True

    def _check_death(self):
        now = pygame.time.get_ticks()
        player_shield = self._shield_left("player", now) > 0
        ai_shield = self._shield_left("ai", now) > 0
        player_dead = False
        ai_dead = False
        pnx, pny = self.player_logic[0]
        if pnx < 0 or pnx >= self.grid_w or pny < 0 or pny >= self.grid_h:
            player_dead = True
        elif (pnx, pny) in self.player_body:
            player_dead = True
        elif (pnx, pny) in self.ai_full and not player_shield:
            player_dead = True
        elif (pnx, pny) in self.obstacles:
            player_dead = True
        anx, any = self.ai_logic[0]
        if anx < 0 or anx >= self.grid_w or any < 0 or any >= self.grid_h:
            ai_dead = True
        elif (anx, any) in self.ai_body:
            ai_dead = True
        elif (anx, any) in self.player_full and not ai_shield:
            ai_dead = True
        elif (anx, any) in self.obstacles:
            ai_dead = True
        if player_dead and ai_dead:
            self.over = True
            self.winner = None
            self._stop_bgm()
            self.death_sound.play()
        elif player_dead:
            self.over = True
            self.winner = 'ai'
            self._stop_bgm()
            self.death_sound.play()
        elif ai_dead:
            self.over = True
            self.winner = 'player'
            self._stop_bgm()
            self.death_sound.play()

    def _smooth_update(self, dt):
        current_player_interval = self.player_boost_interval if self.player_boost_active else self.player_normal_interval
        self.player_speed = GRID_SIZE / (current_player_interval / 1000.0)

        if self.player_smooth:
            max_move = self.player_speed * dt
            for i in range(len(self.player_smooth)):
                sx, sy = self.player_smooth[i]
                tx, ty = self.player_target[i]
                dx = tx - sx
                dy = ty - sy
                dist = math.hypot(dx, dy)
                if dist < 0.1:
                    self.player_smooth[i] = (tx, ty)
                    continue
                move = min(max_move, dist)
                ratio = move / dist
                new_x = sx + dx * ratio
                new_y = sy + dy * ratio
                self.player_smooth[i] = (new_x, new_y)
        if self.ai_smooth:
            max_move = self.ai_speed * dt
            for i in range(len(self.ai_smooth)):
                sx, sy = self.ai_smooth[i]
                tx, ty = self.ai_target[i]
                dx = tx - sx
                dy = ty - sy
                dist = math.hypot(dx, dy)
                if dist < 0.1:
                    self.ai_smooth[i] = (tx, ty)
                    continue
                move = min(max_move, dist)
                ratio = move / dist
                new_x = sx + dx * ratio
                new_y = sy + dy * ratio
                self.ai_smooth[i] = (new_x, new_y)

    def update(self, now):
        dt = min(0.05, (now - getattr(self, '_last_dt_time', now)) / 1000.0)
        self._last_dt_time = now

        if self.over or self.pause or self.pause_menu_active:
            if self.pause and self.end_time is not None:
                self.end_time += dt * 1000      # 暂停时冻结限时倒计时
            if self.bgm_playing:
                self._stop_bgm()
            return
        self._start_bgm()

        self._smooth_update(dt)

        current_player_interval = self.player_boost_interval if self.player_boost_active else self.player_normal_interval
        player_ready = (abs(self.player_smooth[0][0] - self.player_target[0][0]) < 0.5 and
                        abs(self.player_smooth[0][1] - self.player_target[0][1]) < 0.5)
        if player_ready and now - self.last_player_move >= current_player_interval:
            self.last_player_move = now
            self._move_player()
            self._check_death()

        ai_ready = (abs(self.ai_smooth[0][0] - self.ai_target[0][0]) < 0.5 and
                    abs(self.ai_smooth[0][1] - self.ai_target[0][1]) < 0.5)
        if ai_ready and now - self.last_ai_move >= self.ai_interval:
            self.last_ai_move = now
            self._move_ai()
            self._check_death()

        # 金色食物到期 / 强化道具生成 / 限时结束
        if self.gold and now > self.gold_timer:
            self.gold = None
        if now >= self.next_powerup_time:
            self.next_powerup_time = now + POWERUP_INTERVAL
            self._spawn_powerup(now)
        if self.end_time is not None and now >= self.end_time:
            self._finish_by_time()

    def draw(self):
        self.screen.fill(DEEP_BLUE)
        for s in self.stars:
            s.update()
            s.draw(self.screen)

        self.screen.blit(get_grid_overlay(self.window_w, self.window_h, GRID_SIZE, GRAY), (0, 0))

        # 障碍物（深棕色，借鉴论文地图设计）
        for ox, oy in self.obstacles:
            rect = pygame.Rect(ox*GRID_SIZE, oy*GRID_SIZE, GRID_SIZE, GRID_SIZE)
            pygame.draw.rect(self.screen, (101, 67, 33), rect, 0, 3)
            pygame.draw.rect(self.screen, (60, 40, 20), rect, 2, 3)

        for fx, fy in self.foods:
            cx = fx * GRID_SIZE + GRID_SIZE//2
            cy = fy * GRID_SIZE + GRID_SIZE//2
            pygame.draw.circle(self.screen, RED, (cx, cy), GRID_SIZE//2 - 2)

        # 金色特殊食物（六角星，末尾 1 秒闪烁）
        if self.gold:
            gx, gy = self.gold
            gcx, gcy = gx * GRID_SIZE + GRID_SIZE//2, gy * GRID_SIZE + GRID_SIZE//2
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

        # 强化道具（菱形：品红=双倍得分，青色=穿身护盾）
        for pu in self.powerups:
            px, py = pu["pos"]
            pcx, pcy = px * GRID_SIZE + GRID_SIZE//2, py * GRID_SIZE + GRID_SIZE//2
            color = MAGENTA if pu["kind"] == "double" else CYAN
            pts = [(pcx, pcy - int(9 * UI_SCALE)), (pcx + int(9 * UI_SCALE), pcy), (pcx, pcy + int(9 * UI_SCALE)), (pcx - int(9 * UI_SCALE), pcy)]
            pygame.draw.polygon(self.screen, color, pts)
            pygame.draw.polygon(self.screen, WHITE, pts, 2)

        skin = self.skin
        for idx, (px, py) in enumerate(self.player_smooth):
            rect = pygame.Rect(px, py, GRID_SIZE, GRID_SIZE)
            if idx == 0:
                pygame.draw.rect(self.screen, skin["head"], rect, 0, 6)
            else:
                k = idx / len(self.player_smooth)
                r = int(skin["c1"][0]*(1-k)+skin["c2"][0]*k)
                g = int(skin["c1"][1]*(1-k)+skin["c2"][1]*k)
                b = int(skin["c1"][2]*(1-k)+skin["c2"][2]*k)
                pygame.draw.rect(self.screen, (r,g,b), rect, 0, 4)

        for idx, (px, py) in enumerate(self.ai_smooth):
            rect = pygame.Rect(px, py, GRID_SIZE, GRID_SIZE)
            if idx == 0:
                pygame.draw.rect(self.screen, (100,150,255), rect, 0, 6)
            else:
                k = idx / len(self.ai_smooth)
                r = int(BLUE[0]*(1-k)+DARK_GREEN[0]*k)
                g = int(BLUE[1]*(1-k)+DARK_GREEN[1]*k)
                b = int(BLUE[2]*(1-k)+DARK_GREEN[2]*k)
                pygame.draw.rect(self.screen, (r,g,b), rect, 0, 4)

        score_surf = render_text(self.font28, f"玩家: {self.player_score}   AI: {self.ai_score}", WHITE)
        self.screen.blit(score_surf, (int(20 * UI_SCALE), int(20 * UI_SCALE)))
        diff_name = DIFFICULTY_CFG[self.difficulty]["name"]
        diff_surf = render_text(self.font28, f"AI难度: {diff_name}", MAGENTA)
        self.screen.blit(diff_surf, (int(20 * UI_SCALE), int(50 * UI_SCALE)))

        # 限时倒计时
        now = pygame.time.get_ticks()
        if self.end_time is not None:
            left = max(0, int((self.end_time - now) / 1000))
            tcolor = RED if left <= 10 else CYAN
            time_surf = render_text(self.font28, f"剩余: {left}s", tcolor)
            self.screen.blit(time_surf, (self.window_w - time_surf.get_width() - 20, 20))

        # 增益状态 HUD
        y_hud = 84
        d_left = self._double_left("player", now) // 1000
        s_left = self._shield_left("player", now) // 1000
        if d_left > 0:
            t = render_text(self.font20, f"双倍得分 {d_left}s", MAGENTA)
            self.screen.blit(t, (int(20 * UI_SCALE), y_hud))
            y_hud += 24
        if s_left > 0:
            t = render_text(self.font20, f"穿身护盾 {s_left}s", CYAN)
            self.screen.blit(t, (int(20 * UI_SCALE), y_hud))

        cx = self.window_w // 2
        if self.over and not self.over_ui.active:
            if self.winner == 'player':
                self.over_ui.set_title("你赢了！", ((255, 255, 210), (255, 190, 60)))
            elif self.winner == 'ai':
                self.over_ui.set_title("AI 赢了！", ((255, 225, 225), (255, 90, 90)))
            else:
                self.over_ui.set_title("平局！", ((240, 250, 255), (150, 190, 230)))
            sub = f"你 {self.player_score} : {self.ai_score} AI"
            if self.duration:
                sub += f"\n限时 {self.duration} 秒 · 按得分判定"
            self.over_ui.set_subtitle(sub)
            self.over_ui.open()

        self.pause_ui.sync(self.pause_menu_active)
        self.pause_badge.sync(self.pause and not self.pause_menu_active)
        self.over_ui.sync(self.over)

        if not self.quit_ui.active:
            if self.over_ui.is_visible():
                self.over_ui.draw(self.screen)
            if self.pause_badge.is_visible():
                self.pause_badge.draw(self.screen)
            if self.pause_ui.is_visible():
                self.pause_ui.draw(self.screen)
        if self.quit_ui.is_visible():
            self.quit_ui.draw(self.screen)

        pygame.display.flip()

    def _resume_game(self):
        self.pause_menu_active = False
        self.pause = False
        self.pause_ui.close()
        self._start_bgm()

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()

        if self.quit_ui.handle_event(event):
            return True

        if self.over:
            clicked = self.over_ui.clicked(event)
            if clicked == "重新开始":
                self.reset()
                return True
            if clicked == "返回菜单":
                self.over_ui.close()
                return False
            if clicked == "退出游戏":
                self.quit_ui.open()
                return True
            if event.type == pygame.KEYDOWN and key_is("menu", event.key):
                self.over_ui.close()
                return False
            return True

        if self.pause_menu_active:
            clicked = self.pause_ui.clicked(event)
            if clicked == "继续游戏":
                self._resume_game()
            elif clicked == "重新开始":
                self.reset()
            elif clicked == "返回菜单":
                self.pause_ui.close()
                return False
            elif clicked == "退出游戏":
                self.quit_ui.open()
            if event.type == pygame.KEYDOWN and \
                    (key_is("menu", event.key) or key_is("pause", event.key)):
                self._resume_game()
            return True

        if event.type == pygame.KEYDOWN:
            if key_is("menu", event.key):
                self.pause_menu_active = True
                self.pause = True
                self.pause_ui.open()
                self._stop_bgm()
                return True
            if key_is("pause", event.key):
                self.pause = not self.pause
                if self.pause:
                    self._stop_bgm()
                else:
                    self._start_bgm()
                return True
            if not self.pause:
                if key_is("up", event.key):
                    self.set_player_direction(UP)
                    self.held_dir = UP
                elif key_is("down", event.key):
                    self.set_player_direction(DOWN)
                    self.held_dir = DOWN
                elif key_is("left", event.key):
                    self.set_player_direction(LEFT)
                    self.held_dir = LEFT
                elif key_is("right", event.key):
                    self.set_player_direction(RIGHT)
                    self.held_dir = RIGHT
        elif event.type == pygame.KEYUP:
            if any(key_is(a, event.key) for a in ("up", "down", "left", "right")):
                self.held_dir = None

        if event.type == pygame.MOUSEBUTTONDOWN and not self.pause_menu_active:
            self.pause = not self.pause
            if self.pause:
                self._stop_bgm()
            else:
                self._start_bgm()
        return True