import pygame
import os
import sys
import random
import math
from collections import deque
from config import *
import menu_art
from ui import Star, Button, ModalOverlay, QuitConfirm
from utils import load_score_data, save_score_data, update_score, create_bg, get_chinese_font, get_ui_font, UI_SCALE, create_history_surface, resource_path, sound_volume, key_is, render_text, get_grid_overlay, create_display

class SnakeGame:
    def __init__(self):
        pygame.init()
        pygame.mixer.init()
        self.screen = create_display((WINDOW_WIDTH, WINDOW_HEIGHT))
        self.clock = pygame.time.Clock()
        self.state = "menu"
        self.show_help = False
        self.show_history = False
        self.history_surface = None
        self.curr_diff = "normal"

        self.eat_sound = pygame.mixer.Sound(resource_path("assets/audio/eat.wav"))
        self.eat_sound.set_volume(sound_volume("eat"))
        self.death_sound = pygame.mixer.Sound(resource_path("assets/audio/death.wav"))
        self.death_sound.set_volume(sound_volume("death"))
        self.bgm = pygame.mixer.Sound(resource_path("assets/audio/game.wav"))
        self.bgm.set_volume(sound_volume("bgm"))
        self.bgm_playing = False

        data = load_score_data()
        self.high_score = data["highest_score"]
        self.recent_scores = data["recent_scores"]

        self.font22 = get_chinese_font(22)
        self.font28 = get_chinese_font(28)
        self.font28_ui = get_ui_font(28)      # 对局画面按画布放大的字号
        self.font48 = get_chinese_font(48)
        self.font64 = get_chinese_font(64)
        # 对局内的弹层（暂停 / 结束 / 退出确认）画在 1920 画布上，字号同步放大
        self.font22_ui = get_ui_font(22)
        self.font48_ui = get_ui_font(48)
        self.font64_ui = get_ui_font(64)

        bg_path = create_bg(WINDOW_WIDTH, WINDOW_HEIGHT)
        if bg_path and os.path.exists(bg_path):
            self.bg = pygame.image.load(bg_path).convert()
            os.remove(bg_path)
        else:
            self.bg = None

        self.stars = [Star() for _ in range(60)]
        self._build_buttons()

        self.pause_menu_active = False
        window = (WINDOW_WIDTH, WINDOW_HEIGHT)
        self.pause_ui = ModalOverlay(
            window, "暂停", self.font48_ui, self.font22_ui, self.font28_ui,
            [("继续游戏", "primary"), ("重新开始", "card"),
             ("返回菜单", "card"), ("退出游戏", "ghost")],
            accent=(80, 220, 255))
        self.pause_ui.set_subtitle("游戏已暂停 · P 继续 · ESC 关闭")

        self.pause_badge = ModalOverlay(
            window, "已暂停", self.font48_ui, self.font22_ui, self.font28_ui, [],
            min_width=320, accent=(80, 220, 255))
        self.pause_badge.set_subtitle("按 P 继续 · ESC 打开菜单")

        self.over_ui = ModalOverlay(
            window, "游戏结束", self.font48_ui, self.font22_ui, self.font28_ui,
            [("重新开始", "primary"), ("返回菜单", "card"),
             ("退出游戏", "ghost")],
            accent=(255, 118, 118),
            title_colors=((255, 232, 228), (255, 90, 90)))
        self.quit_ui = QuitConfirm(window, self.font48_ui, self.font22_ui, self.font28_ui,
                                   self._quit_game)
        self.reset()

    def _quit_game(self):
        pygame.quit()
        sys.exit()

    def _build_buttons(self):
        cx = WINDOW_WIDTH // 2
        f_card = get_chinese_font(24)
        f_small = get_chinese_font(20)
        # 首页按钮热区：与视频底图中四个按钮的像素位置严格对齐（960×540）
        self.btns_main = [
            Button("天空贪吃蛇", (442, 217, 228, 46),
                   DEEP_BLUE, (24, 64, 96), WHITE, get_chinese_font(28)),
            Button("经典贪吃蛇", (442, 273, 228, 46),
                   DEEP_BLUE, (20, 40, 60), WHITE, get_chinese_font(22)),
            Button("设置", (442, 330, 228, 45),
                   DEEP_BLUE, (20, 40, 60), WHITE, get_chinese_font(22)),
            Button("退出游戏", (442, 387, 228, 46),
                   DEEP_BLUE, (40, 20, 30), WHITE, get_chinese_font(22)),
        ]
        # 左下角奖杯 → 更多功能（成绩 / 排行 / 操作说明），仅作点击热区
        self.btn_more_main = Button("更多功能", (36, 420, 100, 104),
                                    DEEP_BLUE, (16, 30, 48), (150, 190, 220),
                                    get_chinese_font(13), style="ghost")
        # 二级菜单：经典贪吃蛇（热区与 assets/menu_classic_art.png 的胶囊对齐）
        self.btns_classic = [
            Button(label, rect, DEEP_BLUE, (20, 40, 60), WHITE, font, style)
            for (label, font, style), rect in zip(
                (("经典单人", f_card, "classic"), ("人机对战", f_card, "classic"),
                 ("经典联机", f_card, "classic"), ("返回", f_small, "ghost")),
                menu_art.rects(menu_art.KEY_CLASSIC))
        ]
        # 二级菜单：天空贪吃蛇（热区与 assets/menu_sky_art.png 的胶囊对齐）
        self.btns_sky = [
            Button(label, rect, DEEP_BLUE, (20, 40, 60), WHITE, font, style)
            for (label, font, style), rect in zip(
                (("天空贪吃蛇", f_card, "classic"), ("天空人机", f_card, "classic"),
                 ("天空联机", f_card, "classic"), ("返回", f_small, "ghost")),
                menu_art.rects(menu_art.KEY_SKY))
        ]
        # 二级菜单：更多功能
        self.btns_more = [
            Button("历史成绩", (cx - 130, 158, 260, 48),
                   DEEP_BLUE, (20, 40, 60), WHITE, f_card),
            Button("排行榜", (cx - 130, 214, 260, 48),
                   DEEP_BLUE, (20, 40, 60), WHITE, f_card),
            Button("设置", (cx - 130, 270, 260, 48),
                   DEEP_BLUE, (20, 40, 60), WHITE, f_card),
            Button("操作说明", (cx - 130, 326, 260, 48),
                   DEEP_BLUE, (20, 40, 60), WHITE, f_card),
            Button("返回", (cx - 130, 392, 260, 44),
                   DEEP_BLUE, (30, 30, 60), WHITE, f_small, style="ghost"),
        ]
        self.btns_diff = [
            Button("简单", (cx-90, 200, 180, 50), DEEP_BLUE, (20,40,60), WHITE, self.font28),
            Button("普通", (cx-90, 270, 180, 50), DEEP_BLUE, (20,40,60), WHITE, self.font28),
            Button("困难", (cx-90, 340, 180, 50), DEEP_BLUE, (20,40,60), WHITE, self.font28),
            Button("返回", (cx-90, 410, 180, 50), DEEP_BLUE, (20,40,60), WHITE, self.font28),
        ]
        # 人机对战难度页热区：与 assets/menu_diff_art_clean.png 的胶囊对齐
        self.btns_diff_art = [
            Button(label, rect, DEEP_BLUE, (20, 40, 60), WHITE, self.font28)
            for label, rect in zip(("简单", "普通", "困难", "返回"),
                                   menu_art.rects(menu_art.KEY_DIFF))
        ]

    def reset(self):
        cfg = DIFFICULTY_CFG[self.curr_diff]
        x, y = GRID_WIDTH // 2, GRID_HEIGHT // 2
        self.snake_logic = [(x, y)]
        # 空间换时间：除蛇头外的身体占用集合，碰撞查询 O(1)
        self.body_set = set()
        self.target_positions = [(x * GRID_SIZE, y * GRID_SIZE)]
        self.smooth_positions = self.target_positions.copy()
        self.snake_pos = self.smooth_positions.copy()
        self.dir = RIGHT
        self.dir_queue = deque(maxlen=2)
        self.score = 0
        self.over = False
        self.pause = False
        self.special = None
        self.special_timer = 0
        self.eat_count = 0
        self.food = self._random_pos()
        self.last_update_time = pygame.time.get_ticks()
        self.pause_menu_active = False
        self.bgm_playing = False

        self.normal_interval = cfg["interval"]
        self.boost_interval = int(self.normal_interval * BOOST_SPEED_FACTOR)
        self.boost_active = False
        self.boost_counter = 0
        self.last_move_time = pygame.time.get_ticks()
        self.speed = GRID_SIZE / (self.normal_interval / 1000.0)
        self.held_dir = None
        if hasattr(self, "pause_ui"):
            self.pause_ui.close()
            self.pause_badge.close()
            self.over_ui.close()
            self.quit_ui.close()

    def toggle_history_chart(self):
        """切换历史成绩显示/隐藏"""
        if self.show_history:
            self.show_history = False
            self.history_surface = None
        else:
            self.history_surface = create_history_surface(WINDOW_WIDTH, WINDOW_HEIGHT, self.recent_scores)
            self.show_history = True

    def _random_pos(self, exclude_set=None):
        occupied = set(self.snake_logic)
        if exclude_set:
            occupied.update(exclude_set)
        if len(occupied) >= GRID_WIDTH * GRID_HEIGHT:
            return None
        for _ in range(MAX_ATTEMPTS):
            p = (random.randint(0, GRID_WIDTH-1), random.randint(0, GRID_HEIGHT-1))
            if p not in occupied:
                return p
        for x in range(GRID_WIDTH):
            for y in range(GRID_HEIGHT):
                p = (x, y)
                if p not in occupied:
                    return p
        return None

    def _spawn_special(self):
        if self.special is not None:
            return
        exclude = {self.food} if self.food else set()
        p = self._random_pos(exclude)
        if p is not None:
            self.special = p
            self.special_timer = pygame.time.get_ticks() + SPECIAL_FOOD_DURATION

    def set_next_dir(self, new_dir):
        if len(self.snake_logic) >= 3:
            if (self.dir == UP and new_dir == DOWN) or (self.dir == DOWN and new_dir == UP):
                return
            if (self.dir == LEFT and new_dir == RIGHT) or (self.dir == RIGHT and new_dir == LEFT):
                return
        if new_dir not in self.dir_queue:
            self.dir_queue.append(new_dir)

    def _get_next_dir(self):
        while self.dir_queue:
            candidate = self.dir_queue.popleft()
            if len(self.snake_logic) >= 3:
                if (self.dir == UP and candidate == DOWN) or (self.dir == DOWN and candidate == UP):
                    continue
                if (self.dir == LEFT and candidate == RIGHT) or (self.dir == RIGHT and candidate == LEFT):
                    continue
            return candidate
        return self.dir

    def _advance_body(self, new_head, grow):
        """推进蛇身并同步 body_set（空间换时间，查询 O(1)）"""
        old_head = self.snake_logic[0]
        self.snake_logic.insert(0, new_head)
        if grow:
            self.body_set.add(old_head)
        else:
            tail = self.snake_logic.pop()
            self.body_set.discard(tail)
            if tail != old_head:
                self.body_set.add(old_head)

    def _shrink_tail(self):
        """加速瘦身：移除尾格并同步 body_set"""
        if len(self.snake_logic) > 1:
            tail = self.snake_logic.pop()
            self.body_set.discard(tail)
            return True
        return False

    def _logic_move(self):
        new_dir = self._get_next_dir()
        old_dir = self.dir
        self.dir = new_dir

        hx, hy = self.snake_logic[0]
        nx, ny = hx + new_dir[0], hy + new_dir[1]
        ate_normal = ((nx, ny) == self.food)
        ate_special = (self.special is not None and (nx, ny) == self.special)

        if ate_normal:
            self.score += 1
            self.eat_count += 1
            self.eat_sound.play()
            self.snake_logic.insert(0, (nx, ny))
            self.body_set.add(self.snake_logic[1])
            exclude = {self.special} if self.special else set()
            self.food = self._random_pos(exclude)
            if self.food is None:
                self.over = True
                self._save_score()
                self._stop_bgm()
                self.death_sound.play()
                return False
            if self.eat_count % SPECIAL_FOOD_INTERVAL == 0 and self.special is None:
                self._spawn_special()
        elif ate_special:
            self.score += 5
            self.eat_sound.play()
            self.special = None
            self._advance_body((nx, ny), False)
        else:
            self._advance_body((nx, ny), False)

        if nx < 0 or nx >= GRID_WIDTH or ny < 0 or ny >= GRID_HEIGHT or (nx, ny) in self.body_set:
            self.over = True
            self._save_score()
            self._stop_bgm()
            self.death_sound.play()
            return False

        self.target_positions = [(gx * GRID_SIZE, gy * GRID_SIZE) for gx, gy in self.snake_logic]
        if len(self.smooth_positions) < len(self.target_positions):
            self.smooth_positions.append(self.target_positions[-1])
        elif len(self.smooth_positions) > len(self.target_positions):
            self.smooth_positions = self.smooth_positions[:len(self.target_positions)]

        if self.held_dir == new_dir and self.held_dir is not None:
            self.boost_active = True
            self.boost_counter += 1
            if self.boost_counter >= 10:
                if self._shrink_tail():
                    self.score = max(0, self.score - 1)
                    self.target_positions = [(gx * GRID_SIZE, gy * GRID_SIZE) for gx, gy in self.snake_logic]
                    if len(self.smooth_positions) > len(self.target_positions):
                        self.smooth_positions = self.smooth_positions[:len(self.target_positions)]
                self.boost_counter = 0
        else:
            self.boost_active = False
            self.boost_counter = 0

        return True

    def _stop_bgm(self):
        self.bgm.stop()
        self.bgm_playing = False

    def _start_bgm(self):
        if not self.bgm_playing and not self.pause and not self.over:
            self.bgm.play(-1)
            self.bgm_playing = True

    def _smooth_update(self, dt):
        if not self.smooth_positions:
            return
        max_move = self.speed * dt
        for i in range(len(self.smooth_positions)):
            sx, sy = self.smooth_positions[i]
            tx, ty = self.target_positions[i]
            dx = tx - sx
            dy = ty - sy
            dist = math.hypot(dx, dy)
            if dist < 0.1:
                self.smooth_positions[i] = (tx, ty)
                continue
            move = min(max_move, dist)
            ratio = move / dist
            new_x = sx + dx * ratio
            new_y = sy + dy * ratio
            self.smooth_positions[i] = (new_x, new_y)
        self.snake_pos = self.smooth_positions.copy()

    def _save_score(self):
        self.high_score, self.recent_scores = update_score(self.score, self.high_score, self.recent_scores)
        save_score_data(self.high_score, self.recent_scores)

    def update(self, now):
        if self.over or self.pause or self.pause_menu_active or self.show_history:
            if self.bgm_playing:
                self._stop_bgm()
            return
        self._start_bgm()

        current_interval = self.boost_interval if self.boost_active else self.normal_interval
        self.speed = GRID_SIZE / (current_interval / 1000.0)

        dt = min(0.05, (now - self.last_update_time) / 1000.0)
        if dt <= 0:
            return
        self.last_update_time = now
        self._smooth_update(dt)

        if (abs(self.smooth_positions[0][0] - self.target_positions[0][0]) < 0.5 and
            abs(self.smooth_positions[0][1] - self.target_positions[0][1]) < 0.5):
            if now - self.last_move_time >= current_interval:
                self.last_move_time = now
                self._logic_move()

        if self.special and now > self.special_timer:
            self.special = None

    def _resume_game(self):
        self.pause_menu_active = False
        self.pause = False
        self.pause_ui.close()
        self._start_bgm()

    def handle_event(self, event):
        if self.show_history:
            if event.type == pygame.MOUSEBUTTONDOWN or event.type == pygame.KEYDOWN:
                self.show_history = False
                self.history_surface = None
            return True

        if self.quit_ui.handle_event(event):
            return True

        if self.over:
            clicked = self.over_ui.clicked(event)
            if clicked == "重新开始":
                self.reset()
                return True
            if clicked == "返回菜单":
                self.over_ui.close()
                self.state = "menu"
                return True
            if clicked == "退出游戏":
                self.quit_ui.open()
                return True
            if event.type == pygame.KEYDOWN and key_is("menu", event.key):
                self.over_ui.close()
                self.state = "menu"
                return True
            return True

        if self.pause_menu_active:
            clicked = self.pause_ui.clicked(event)
            if clicked == "继续游戏":
                self._resume_game()
            elif clicked == "重新开始":
                self.reset()
            elif clicked == "返回菜单":
                self.pause_ui.close()
                self.state = "menu"
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
                    self.set_next_dir(UP)
                    self.held_dir = UP
                elif key_is("down", event.key):
                    self.set_next_dir(DOWN)
                    self.held_dir = DOWN
                elif key_is("left", event.key):
                    self.set_next_dir(LEFT)
                    self.held_dir = LEFT
                elif key_is("right", event.key):
                    self.set_next_dir(RIGHT)
                    self.held_dir = RIGHT
        elif event.type == pygame.KEYUP:
            if any(key_is(a, event.key) for a in ("up", "down", "left", "right")):
                self.held_dir = None

        if event.type == pygame.MOUSEBUTTONDOWN:
            self.pause = not self.pause
            if self.pause:
                self._stop_bgm()
            else:
                self._start_bgm()
        return True

    def draw(self):
        self.screen.fill(DEEP_BLUE)
        for s in self.stars:
            s.update()
            s.draw(self.screen)
        self.screen.blit(get_grid_overlay(WINDOW_WIDTH, WINDOW_HEIGHT, GRID_SIZE, GRAY), (0, 0))

        fx, fy = self.food
        cx, cy = fx*GRID_SIZE+10, fy*GRID_SIZE+10
        for r in range(8,2,-1):
            pygame.draw.circle(self.screen, RED, (cx,cy), r)

        if self.special:
            remaining = self.special_timer - pygame.time.get_ticks()
            if 0 < remaining < 1000:
                flash_color = (255,255,100) if (pygame.time.get_ticks()//100)%2==0 else (255,100,0)
            else:
                flash_color = GOLD
            sx, sy = self.special
            scx, scy = sx*GRID_SIZE+10, sy*GRID_SIZE+10
            pts = []
            for i in range(6):
                a = i*math.pi/3 - math.pi/6
                pts.append((scx+math.cos(a)*8, scy+math.sin(a)*8))
                pts.append((scx+math.cos(a+math.pi/6)*4, scy+math.sin(a+math.pi/6)*4))
            pygame.draw.polygon(self.screen, flash_color, pts)

        for idx, (px, py) in enumerate(self.snake_pos):
            ix, iy = int(px), int(py)
            rect = pygame.Rect(ix, iy, GRID_SIZE, GRID_SIZE)
            if idx == 0:
                pygame.draw.rect(self.screen, CYAN, rect, 0, 6)
            else:
                k = idx / len(self.snake_pos)
                r = int(CYAN[0]*(1-k)+DARK_GREEN[0]*k)
                g = int(CYAN[1]*(1-k)+DARK_GREEN[1]*k)
                b = int(CYAN[2]*(1-k)+DARK_GREEN[2]*k)
                pygame.draw.rect(self.screen, (r,g,b), rect, 0, 4)

        m = UI_SCALE * 10
        f = self.font28_ui              # 对局画面是 1920 画布，HUD 字号按画布放大
        self.screen.blit(render_text(f, f"得分: {self.score}", CYAN),
                         (m, m))
        self.screen.blit(render_text(f, f"最高: {self.high_score}", MAGENTA),
                         (m, m + f.get_height() + int(6 * UI_SCALE)))

        if self.over and not self.over_ui.active:
            self.over_ui.set_subtitle(
                f"本局得分 {self.score}    历史最高 {self.high_score}")
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

        if self.show_history and self.history_surface:
            self.screen.blit(self.history_surface, (0, 0))

        pygame.display.flip()