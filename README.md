# 像素贪吃蛇 · PixelSerpent

一个用 Python 写的贪吃蛇合集：2D 网格版（经典单人 / AI 演示 / 人机对战 / 双人联机）与 3D 空中版（Ursina + Panda3D，含人机与联机对战）。全部界面统一 1920×1080 画布，中文界面，鼠标 + 键盘双操作。

## 玩法一览

| 模式 | 引擎 | 说明 |
| :--- | :--- | :--- |
| 经典单人 | pygame | 48×27 网格，金色食物、皮肤渐变、最高分与最近 10 局记录 |
| AI 演示 | pygame | BFS 寻路自动游玩，可观察 AI 决策 |
| 人机对战 | pygame | 与 AI 同场竞技，障碍按难度 8/14/20，限时可选 |
| 双人联机 | pygame + TCP | 主机权威，房间码或 IP:端口加入，默认 50007 |
| 天空贪吃蛇 | Ursina | 空中飞行、俯冲爬升、安全区缩圈、光环、5 种强化道具 |
| 天空人机 / 联机 | Ursina | HP 制对战，爪子近战 + 弓箭远程 |

> 3D 天空模式为第三人称追尾视角：鼠标控制转向与俯冲爬升，左键冲刺消耗能量，右键释放道具，对战中安全区按阶段缩圈、圈外持续掉血。

## 环境

- Python 3.10+（开发环境为 3.13.9）
- `pip install -r requirements.txt`（pygame、Pillow、numpy）
- 3D 模式另需 `pip install ursina panda3d`

## 运行

```bash
python main.py                          # 2D 主程序（首页菜单）
python sky3d_mode.py                    # 单独运行 3D 天空贪吃蛇
set SKY3D_TEST=1 && python sky3d_mode.py # 3D 离屏自检（Windows）
```

操作：方向键 / WASD 移动，P 暂停，ESC 菜单；3D 模式鼠标控制转向、左键冲刺、右键释放道具。

## 目录

```
main.py / config.py / utils.py / ui.py      入口、配置、工具、UI 组件
game.py / ai_mode.py / versus_mode.py       2D 单人、AI 演示、人机对战
netbattle_mode.py / network.py              2D 联机与 TCP 协议
sky3d_mode.py / sky3d_ai_mode.py            3D 主模块与 3D 人机
sky3d_net_mode.py                           3D 联机（HP 制）
home_bg.py / home_scene.py / menu_art.py    首页背景与菜单底图
assets/                                     图片、音效、字体、帧序列
data/                                       运行时存档（首次运行自动创建）
docs/                                       项目说明、结构文档、AI 使用声明
```

详细说明见 [`docs/项目说明.md`](docs/项目说明.md)。

## 资源说明

- 仓库内 `assets/` 只包含自制图片与音效；**中文字体 `msyh.ttc` 因授权原因未上传**，程序会自动回退到系统自带的微软雅黑 / 黑体。
- `assets/source/`、`assets/menu_video_frames/` 为素材原件与旧帧序列，体积较大，未纳入仓库。
- 打包成品 exe 未上传（单文件约 157MB），可自行用 `pyinstaller --noconfirm --clean "像素贪吃蛇.spec"` 构建。

## 已知限制

- 联机未做断线重连与 NAT 穿透，仅适用于局域网。
- 3D 模式依赖 OpenGL，虚拟机或远程桌面下可能无法创建窗口。

## 版权声明

本仓库将源码公开，供学习与参考，**未附任何开源许可证**（不含 MIT / Apache / GPL 等授权条款），作者保留全部著作权。

未经作者书面许可，不得复制、修改后重新发布、商用或再分发本项目及其派生作品；引用其中的算法或实现时请注明出处。

---

作者：LiuKang · 2026
