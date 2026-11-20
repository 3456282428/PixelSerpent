# 窗口与网格（16:9 主窗口，可自由缩放 / 全屏）
import os
import sys
import pygame

# 项目根与数据目录：存档/设置统一写到 data/，与启动时的当前工作目录无关。
# 源码运行按脚本位置；打包成 exe 后按 exe 所在目录（否则会写进 _MEIPASS 临时目录，重启即丢）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    pass

WINDOW_WIDTH = 1920                 # 画布尺寸：所有页面统一（720p 的 2 倍）
WINDOW_HEIGHT = 1080
GRID_SIZE = 40                      # 格子 20 → 40，地图仍是 48×27 格，玩法/速度不变
GRID_WIDTH = WINDOW_WIDTH // GRID_SIZE
GRID_HEIGHT = WINDOW_HEIGHT // GRID_SIZE

# 人机对战专用尺寸：与主画布完全一致（场地不再另开大窗口）
VERSUS_GRID_HEIGHT = WINDOW_HEIGHT // GRID_SIZE
VERSUS_GRID_WIDTH = WINDOW_WIDTH // GRID_SIZE
VERSUS_WINDOW_HEIGHT = VERSUS_GRID_HEIGHT * GRID_SIZE
VERSUS_WINDOW_WIDTH = VERSUS_GRID_WIDTH * GRID_SIZE

RENDER_FPS = 180
# 最大帧率可选值（0 = 不限帧）
FPS_OPTIONS = [60, 90, 120, 144, 165, 180, 240, 0]

# 难度配置
DIFFICULTY_CFG = {
    "easy":   {"interval": 300, "min_interval": 80, "step": 12, "name": "简单"},
    "normal": {"interval": 250, "min_interval": 50, "step": 8,  "name": "普通"},
    "hard":   {"interval": 50,  "min_interval": 30, "step": 6,  "name": "困难"}
}

PLAYER_FIXED_INTERVAL = 250
SPECIAL_FOOD_INTERVAL = 5
SPECIAL_FOOD_DURATION = 5000
MAX_ATTEMPTS = 1000
SCORE_FILE = os.path.join(DATA_DIR, "snake_scores.json")

# 方向常量
UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)

# 颜色
BLACK = (0, 0, 0)
DEEP_BLUE = (10, 10, 30)
CYAN = (0, 255, 255)
MAGENTA = (255, 0, 255)
GREEN = (0, 255, 128)
DARK_GREEN = (0, 180, 0)
RED = (255, 50, 50)
GOLD = (255, 215, 0)
WHITE = (240, 240, 255)
GRAY = (80, 80, 100)
LIGHT_CYAN = (180, 255, 255)
BLUE = (0, 100, 255)

# ----- 设置项 -----
MASTER_VOLUME = 0.5   # 0.0 ~ 1.0

# ----- 各音效独立音量 (0.0 ~ 1.0) -----
EAT_VOLUME   = 0.2
DEATH_VOLUME = 1.0
BGM_VOLUME   = 0.6
MENU_VOLUME  = 0.4

# 默认按键绑定（动作名 -> pygame键值）
DEFAULT_KEY_BINDINGS = {
    "up": pygame.K_UP,
    "down": pygame.K_DOWN,
    "left": pygame.K_LEFT,
    "right": pygame.K_RIGHT,
    "pause": pygame.K_p,
    "menu": pygame.K_ESCAPE,
}
KEY_BINDINGS = DEFAULT_KEY_BINDINGS.copy()

# ----- 设置持久化 -----
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
DEFAULT_NICKNAME = "玩家"

# 运行时设置（可变字典，导入后修改对所有模块生效）
SETTINGS = {
    "master": MASTER_VOLUME,
    "eat": EAT_VOLUME,
    "death": DEATH_VOLUME,
    "bgm": BGM_VOLUME,
    "menu": MENU_VOLUME,
    "nickname": DEFAULT_NICKNAME,
    "skin": 0,
    "fps": RENDER_FPS,
    "sky_sens": 3.0,          # 天空贪吃蛇鼠标转向灵敏度（弧度/屏幕宽）
    "sky_invert_y": 0,        # 天空贪吃蛇上下反转（0=鼠标上移抬头，1=反转）
}

# 天空贪吃蛇灵敏度可调范围（每档 0.2）
SKY_SENS_MIN = 0.6
SKY_SENS_MAX = 6.0
SKY_SENS_STEP = 0.2

# 皮肤：头部颜色 + 身体渐变起止色
SKINS = [
    {"name": "翠绿", "head": (100, 255, 100), "c1": GREEN, "c2": DARK_GREEN},
    {"name": "天蓝", "head": (100, 150, 255), "c1": BLUE, "c2": (0, 40, 120)},
    {"name": "烈焰", "head": (255, 130, 80), "c1": (255, 90, 40), "c2": (140, 20, 0)},
    {"name": "霓虹", "head": (255, 110, 255), "c1": (230, 60, 255), "c2": (90, 0, 140)},
    {"name": "流金", "head": (255, 235, 120), "c1": GOLD, "c2": (150, 90, 0)},
]

# 动作名称列表（用于显示和遍历）
ACTION_NAMES = ["up", "down", "left", "right", "pause", "menu"]
ACTION_DISPLAY = {
    "up": "上",
    "down": "下",
    "left": "左",
    "right": "右",
    "pause": "游戏暂停",
    "menu": "返回主菜单"
}

# ----- 加速设置 -----
BOOST_SPEED_FACTOR = 0.6   # 加速时移动间隔变为原来的 60%

# ----- 联机设置 -----
NET_PORT = 50007           # 联机对战默认 TCP 端口

# ----- 对战地图障碍（借鉴论文：中心区随机障碍 + 连通性校验） -----
OBSTACLE_COUNT = {"easy": 8, "normal": 14, "hard": 20}   # 人机对战按难度
NET_OBSTACLE_COUNT = 14                                   # 联机对战固定数量
SPAWN_SAFE_RADIUS = 4                                     # 出生点安全区（曼哈顿距离）

# ----- 自动成长（借鉴论文：初期每步长1格，后期每3步长1格） -----
GROW_EVERY_STEP_UNTIL = 10   # 前 10 步每步长 1 格
GROW_INTERVAL_AFTER = 3      # 之后每 3 步长 1 格

# ----- 联机战绩 -----
NET_RECORD_FILE = os.path.join(DATA_DIR, "net_record.json")

# ----- 房间参数（房主可配置，客户端随快照同步） -----
ROOM_SPEEDS = [("慢速", 300), ("标准", 250), ("快速", 200)]
ROOM_OBSTACLES = [("无障碍", 0), ("标准", 14), ("密集", 24)]
ROOM_FOODS = [2, 3, 4]
ROOM_DURATIONS = [("不限时", 0), ("60秒", 60), ("90秒", 90), ("120秒", 120)]
ROOM_DEFAULT = {"speed": 1, "obstacle": 1, "food": 1, "duration": 0}

# ----- 对战限时模式（人机） -----
VERSUS_DURATIONS = ROOM_DURATIONS

# ----- 金色特殊食物（对战） -----
GOLD_FOOD_SCORE = 5
GOLD_FOOD_DURATION = 5000   # 存在时间（毫秒）
GOLD_FOOD_CHANCE = 0.35     # 每次吃到普通食物后生成概率

# ----- 强化道具（对战） -----
POWERUP_INTERVAL = 8000     # 尝试生成间隔（毫秒）
POWERUP_MAX = 1             # 场上同时存在上限
POWERUP_DOUBLE_MS = 10000   # 双倍得分持续
POWERUP_SHIELD_MS = 5000    # 穿身护盾持续（忽略对方蛇身碰撞）
POWERUP_TYPES = ("double", "shield")

# ----- 天空贪吃蛇（鼠标 360° 滑动操控） -----
SKY_WORLD_SIZE = 3200                              # 世界地图尺寸（正方形）
SKY_SPEED = 230.0                                  # 自动前进速度（像素/秒）
SKY_TURN_RATE = 14.0                               # 最大转向角速度（弧度/秒，约 800°/s）：跟手但仍留飞行惯性
SKY_HEAD_RADIUS = 9
SKY_TRAIL_SPACING = 3                              # 身体轨迹采样间距
SKY_INITIAL_LENGTH = 100                           # 初始身体长度（像素）
SKY_LENGTH_PER_XP = 5                              # 每点经验增加的长度
SKY_MAX_LENGTH = 600
SKY_FRUIT_MAX = 30
SKY_FRUIT_INTERVAL = 700                           # 果实刷新间隔（毫秒）
SKY_GOLD_XP = 3                                    # 金色经验果实经验值
SKY_XP_PER_LEVEL = 10
SKY_SPAWN_CLEAR = 420                              # 出生点安全半径
SKY_SCORE_FILE = os.path.join(DATA_DIR, "sky_scores.json")

# ----- 天空贪吃蛇：软碰撞墙（和平精英风格，非硬阻挡） -----
SKY_SOFT_WALL_BUFFER = 190        # 缓冲距离（像素）：进入后开始受到向外的推力
SKY_SOFT_WALL_PUSH = 200.0        # 贴墙时的最大外推速度（像素/秒），低于前进速度
SKY_SOFT_WALL_INSET = 60          # 四周边界软墙相对地图边缘的内缩距离（像素）
SKY_SOFT_WALL_CUSTOM = []         # 自定义软墙矩形 [(x, y, 宽, 高), ...]（世界像素坐标，可多个）

# ----- 天空贪吃蛇联机对战（HP 制） -----
SKY_NET_HP = 100                   # 初始血量
SKY_NET_DURATION = 180000          # 每局限时（毫秒），到点按剩余血量判定
SKY_NET_SNAPSHOT_MS = 50           # 主机快照广播间隔（20Hz）
SKY_NET_INPUT_MS = 60              # 客户端输入上报间隔
SKY_NET_COUNTDOWN = 3000           # 开局倒计时
SKY_HEAL_AMOUNT = 30               # 治疗道具
SKY_ATK_BOOST = 0.4                # 攻击道具：伤害 +40%
SKY_ATK_DURATION = 15000
SKY_DEF_REDUCE = 0.4               # 防御道具：受伤 -40%
SKY_DEF_DURATION = 15000
SKY_SLOW_FACTOR = 0.55             # 减速道具：对手速度倍率
SKY_SLOW_DURATION = 6000
SKY_BLEED_DPS = 4.0                # 流血道具：对手每秒掉血
SKY_BLEED_DURATION = 6000
SKY_ITEM_MAX = 12                  # 场上道具数量上限
SKY_ITEM_INTERVAL = 2500           # 道具刷新间隔
SKY_INV_MAX = 5                    # 道具背包容量
SKY_CLAW_RANGE = 75                # 爪子：近程攻击距离
SKY_CLAW_DMG = 15
SKY_CLAW_CD = 1000
SKY_CLAW_ARC = 1.2                 # 爪子扇形半角（弧度）
SKY_BOW_DMG = 12                   # 弓箭：远程伤害
SKY_BOW_CD = 800
SKY_ARROW_SPEED = 520.0
SKY_ARROW_RANGE = 650.0
