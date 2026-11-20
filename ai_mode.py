import pygame
import sys
import random
import numpy as np
from collections import deque
from config import *
from ui import Button, Star, ModalOverlay, QuitConfirm
from utils import UI_SCALE, create_bg, get_ui_font, resource_path, sound_volume, key_is, render_text, get_grid_overlay

class AISnakeGame:
    def __init__(self, screen, difficulty="normal"):
        self.screen = screen
        self.difficulty = difficulty
        self.clock = pygame.time.Clock()
        self.running = True
        self.pause = False
        self.over = False

        pygame.mixer.init()
        self.eat_sound = pygame.mixer.Sound(resource_path("assets/audio/eat.wav"))
        self.eat_sound.set_volume(sound_volume("eat"))
        self.death_sound = pygame.mixer.Sound(resource_path("assets/audio/death.wav"))
        self.death_sound.set_volume(sound_volume("death"))
        self.bgm = pygame.mixer.Sound(resource_path("assets/audio/game.wav"))
        self.bgm.set_volume(sound_volume("bgm"))
        self.bgm_playing = False

        self.font22 = get_ui_font(22)
        self.font28 = get_ui_font(28)
        self.font48 = get_ui_font(48)
        self.font64 = get_ui_font(64)

        bg_path = create_bg(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.bg = pygame.image.load(bg_path).convert() if bg_path else None
        self.stars = [Star() for _ in range(60)]

        self.pause_menu_active = False
        window = (WINDOW_WIDTH, WINDOW_HEIGHT)
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
            window, "游戏结束", self.font48, self.font22, self.font28,
            [("重新开始", "primary"), ("返回菜单", "card"),
             ("退出游戏", "ghost")],
            accent=(255, 118, 118),
            title_colors=((255, 232, 228), (255, 90, 90)))
        self.quit_ui = QuitConfirm(window, self.font48, self.font22, self.font28,
                                   self._quit_game)
        self.reset()

    def _quit_game(self):
        pygame.quit()
        sys.exit()

    def reset(self):
        cfg = DIFFICULTY_CFG[self.difficulty]
        self.interval = cfg["interval"]
        self.last_move_time = pygame.time.get_ticks()

        cx, cy = GRID_WIDTH // 2, GRID_HEIGHT // 2
        self.snake = [(cx, cy), (cx-1, cy), (cx-2, cy)]
        self.dir = RIGHT
        self.score = 0
        self.eat_count = 0
        self.special_food = None
        self.special_timer = 0
        self.over = False
        self.pause = False
        self.pause_menu_active = False
        self.bgm_playing = False

        self.food = self._random_pos()
        if self.food is None:
            self.food = (5, 5)
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

    def _random_pos(self, exclude_set=None):
        occupied = set(self.snake)
        if getattr(self, "food", None):
            occupied.add(self.food)
        if getattr(self, "special_food", None):
            occupied.add(self.special_food)
        if exclude_set:
            occupied.update(exclude_set)
        if len(occupied) >= GRID_WIDTH * GRID_HEIGHT:
            return None
        for _ in range(MAX_ATTEMPTS):
            x = random.randint(0, GRID_WIDTH-1)
            y = random.randint(0, GRID_HEIGHT-1)
            if (x, y) not in occupied:
                return (x, y)
        for x in range(GRID_WIDTH):
            for y in range(GRID_HEIGHT):
                if (x, y) not in occupied:
                    return (x, y)
        return None

    def _spawn_special_food(self):
        if self.special_food is None:
            pos = self._random_pos(exclude_set={self.food})
            if pos:
                self.special_food = pos
                self.special_timer = pygame.time.get_ticks() + SPECIAL_FOOD_DURATION

    def _bfs_path(self, start, target, snake_body):
        if start == target:
            return [start]
        obstacle = np.zeros((GRID_WIDTH, GRID_HEIGHT), dtype=bool)
        for idx, seg in enumerate(snake_body):
            if idx != 0:
                obstacle[seg[0], seg[1]] = True

        queue = deque([(start, [start])])
        visited = np.zeros((GRID_WIDTH, GRID_HEIGHT), dtype=bool)
        visited[start[0], start[1]] = True

        dirs = [(1,0), (-1,0), (0,1), (0,-1)]
        while queue:
            (x, y), path = queue.popleft()
            for dx, dy in dirs:
                nx, ny = x + dx, y + dy
                if 0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT:
                    if (nx, ny) == target:
                        return path + [(nx, ny)]
                    if not visited[nx, ny] and not obstacle[nx, ny]:
                        visited[nx, ny] = True
                        queue.append(((nx, ny), path + [(nx, ny)]))
        return None

    def _get_safe_direction(self):
        head = self.snake[0]
        target = self.special_food if self.special_food else self.food
        path = self._bfs_path(head, target, self.snake) if target else None
        if path and len(path) > 1:
            next_cell = path[1]
            return (next_cell[0] - head[0], next_cell[1] - head[1])

        safe_dirs = []
        for dx, dy in [(1,0), (-1,0), (0,1), (0,-1)]:
            nx, ny = head[0] + dx, head[1] + dy
            if 0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT:
                if (nx, ny) not in self.snake[1:]:
                    safe_dirs.append((dx, dy))
        if safe_dirs:
            if target:
                dists = np.array([abs(head[0]+dx-target[0]) + abs(head[1]+dy-target[1]) for dx, dy in safe_dirs])
                best_idx = np.argmin(dists)
                return safe_dirs[best_idx]
            return random.choice(safe_dirs)
        return self.dir

    def _move(self, direction):
        head = self.snake[0]
        nx, ny = head[0] + direction[0], head[1] + direction[1]
        ate_normal = ((nx, ny) == self.food)
        ate_special = (self.special_food is not None and (nx, ny) == self.special_food)

        new_snake = [(nx, ny)] + self.snake
        if not (ate_normal or ate_special):
            new_snake.pop()

        if nx < 0 or nx >= GRID_WIDTH or ny < 0 or ny >= GRID_HEIGHT:
            return False
        if (nx, ny) in self.snake[1:]:
            return False

        if ate_normal:
            self.score += 1
            self.eat_count += 1
            self.eat_sound.play()
            self.food = self._random_pos()
            if self.food is None:
                return False
            if self.eat_count % SPECIAL_FOOD_INTERVAL == 0:
                self._spawn_special_food()
        elif ate_special:
            self.score += 5
            self.eat_sound.play()
            self.special_food = None

        self.snake = new_snake
        if self.special_food and pygame.time.get_ticks() > self.special_timer:
            self.special_food = None
        return True

    def update(self, now):
        if self.over or self.pause or self.pause_menu_active:
            if self.bgm_playing:
                self._stop_bgm()
            return
        self._start_bgm()

        if now - self.last_move_time >= self.interval:
            self.last_move_time = now
            next_dir = self._get_safe_direction()
            if len(self.snake) >= 3:
                if (self.dir[0] == -next_dir[0] and self.dir[1] == -next_dir[1]):
                    next_dir = self.dir
            self.dir = next_dir
            if not self._move(self.dir):
                self.over = True
                self._stop_bgm()
                self.death_sound.play()

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
        if event.type == pygame.MOUSEBUTTONDOWN and not self.pause_menu_active:
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
        pygame.draw.circle(self.screen, RED, (cx, cy), 8)

        if self.special_food:
            sx, sy = self.special_food
            scx, scy = sx*GRID_SIZE+10, sy*GRID_SIZE+10
            flash_color = GOLD if (pygame.time.get_ticks() // 100) % 2 == 0 else (255,255,100)
            pygame.draw.circle(self.screen, flash_color, (scx, scy), 8)

        for idx, (px, py) in enumerate(self.snake):
            rect = pygame.Rect(px*GRID_SIZE, py*GRID_SIZE, GRID_SIZE, GRID_SIZE)
            if idx == 0:
                pygame.draw.rect(self.screen, CYAN, rect, 0, 6)
            else:
                k = idx / len(self.snake)
                r = int(CYAN[0]*(1-k)+DARK_GREEN[0]*k)
                g = int(CYAN[1]*(1-k)+DARK_GREEN[1]*k)
                b = int(CYAN[2]*(1-k)+DARK_GREEN[2]*k)
                pygame.draw.rect(self.screen, (r,g,b), rect, 0, 4)

        self.screen.blit(render_text(self.font28, f"得分: {self.score}", CYAN), (int(10 * UI_SCALE), int(10 * UI_SCALE)))
        diff_name = DIFFICULTY_CFG[self.difficulty]["name"]
        self.screen.blit(render_text(self.font28, f"难度: {diff_name}", MAGENTA), (int(10 * UI_SCALE), int(50 * UI_SCALE)))

        if self.over and not self.over_ui.active:
            diff_name = DIFFICULTY_CFG[self.difficulty]["name"]
            self.over_ui.set_subtitle(f"最终得分 {self.score}    难度 {diff_name}")
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