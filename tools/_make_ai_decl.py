# -*- coding: utf-8 -*-
"""生成《AI 使用声明》Word 文档（含附录：关键提示词原文）"""
from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(r'D:\python\src\pythonhomework\AI使用声明.docx')

ROWS = [
    ('AI 编程助手（可执行命令）',
     '2D 主玩法骨架：主循环、网格移动、吃食计分、暂停、结束重开、排行榜存档',
     '「用 pygame 写一个 48×27 网格的贪吃蛇：计分、暂停、结束重开、最高分存档，帧率上限可调」',
     '网格由 24 格改为 40 格以适配 1920×1080 画布；补上菜单状态机漏掉的 4 个新页面（原版点击无响应）；逐页截图比对布局'),
    ('AI 编程助手',
     '人机对战 AI 寻路',
     '「给贪吃蛇写人机对战 AI：BFS 追最近食物，同时避开自身、边界和对手堵截」',
     'AI 在窄通道内会把自身封死，增加「走完该步后可达格数不少于身长 60%」的安全检查；用脚本反复跑局观察自撞率'),
    ('AI 编程助手',
     '双人联机（socket 权威同步）',
     '「Python socket 双人联机贪吃蛇：服务端权威、客户端插值显示，端口 50007」',
     'Windows 下把 SO_REUSEADDR 换成 SO_EXCLUSIVEADDRUSE 防端口劫持，accept 后显式 setblocking(True)；已在本机完成真实双端对局实测，一局完整结束，无断连卡顿'),
    ('AI 编程助手（Ursina + Panda3D）',
     '3D「天空贪吃蛇」原型：球形世界、俯冲爬升、第三人称追尾相机、程序化地形与浮岛',
     '「用 ursina 做 3D 空中贪吃蛇：能上下飞，第三人称追尾相机，程序化地形和浮岛」',
     '相机穿进山体，加地形高度场限制与垂直方向非对称平滑；世界尺寸由 3200 扩到 6400；以离屏渲染截图与自检脚本验证'),
    ('大模型对话',
     '转向手感排障与重构（多轮往返，最终方案由本人选定）',
     '「ursina 的 mouse.locked 在 Windows 125% 缩放下令镜头自行偏移、指针越靠边转得越快，请给出根因与成熟方案」',
     'AI 建议改用 raw input，因引擎不暴露消息循环，改为 Win32 取位移并隐藏光标；符号错了两版，最终用「固定世界点屏幕投影」实测校正（±0.25 弧度对应 ±49.5 像素，严格对称）；已实机逐档试玩确认真跟手'),
    ('AI 编程助手',
     '安全区缩圈（3D 人机与联机模式）',
     '「做 3D 安全区：开局 20 秒预告下一圈，缓慢缩 30 秒、停 30 秒，共 5 次，屏障用深蓝半透明」',
     '半径序列（0.50 至 0.14）逐档手调；屏障不透明度改为底部 1.0、顶部 0.62 并加顶沿亮环（原版几乎不可见）；离屏截图核对轮次提示与圈外暗角'),
    ('AI 编程助手',
     '边界软碰撞墙与撞击反馈',
     '「软墙要能看见高度，撞上去在接触点出现圆形光环，越靠近越亮」',
     '墙高由 10 改到 46，拆为 10 层透明度渐变（底部 0.92 至顶部 0.07）；贴墙截图确认高空视角下仍可见，光环落在真实接触点'),
    ('文生图（Seedream 5.5）',
     '二级菜单底图，含天空贪吃蛇、经典贪吃蛇、难度选择共 5 张 2048×1152',
     '「2048×1152 游戏二级菜单底图：浅蓝天空、云海浮岛、卡通糖果色蛇环绕四周，中间留四条胶囊按钮位」',
     '图中按钮文字全部不采用，改由程序矢量绘制，保证文字与点击热区一致；按钮位置按底图实测重排；三档缩放截图核对对齐'),
    ('图像编辑',
     '抹除底图上已烘焙的按钮文字',
     '「把这张菜单图上的按钮文字抹掉并补齐背景，插画和蛇身不要动」',
     '只保留插画作为底图，文字全部重绘；放大检查抹除区接缝，并与原图逐处对比确认角色细节未被改动'),
    ('文生图',
     '3D 天空穹顶全景贴图',
     '「2048×1024 可平铺天空全景，卡通渲染，云海与浮岛，用作 3D 天空盒」',
     '贴到穹顶后接缝明显，做水平偏移与边缘融合；绕世界 360 度截图逐段检查接缝'),
    ('图像编辑',
     '碰撞点光环贴图',
     '「把这张图裁成圆形，中心颜色深、向外逐渐变淡，去背景只留圆形」',
     '输出 512×512 透明图并缩放至 256；在深色天空背景下贴墙截图，确认光环仍可分辨'),
    ('AI 编程助手',
     '首页循环视频（起因是本人质疑画面像静态图、分辨率仅 960×540）',
     '「首页视频要真正循环播放，分辨率能否提到 1080p，为什么现在看起来是静态的」',
     '确定视频转帧序列方案：243 帧 2048×1152 打包为 42.2MB 资源，按秒取帧闭环；比对首末帧确认循环处无跳变'),
    ('AI 编程助手',
     '打包与运行时依赖修复',
     '「打包后运行 3D 模式报 DLL load failed while importing _ctypes，怎么修」',
     '定位为本地 Python 环境的运行时库未被收集，在打包配置中显式加入 7 个动态库；打包产物实测启动正常、无崩溃日志'),
    ('大模型对话（代码审查）',
     '全项目代码审计',
     '「通读整个项目，按功能缺陷、依赖、资源冗余、性能给一份检查报告」',
     '产出检查报告；据其补齐菜单缺失页面、删除已废弃模块、清理未使用导入；19.5MB 冗余资源标记后排除出打包，原文件保留未删'),
]

HEAD = ('工具名称 / 类型', '用在作品的哪一步', '关键提示词（摘录）', 'AI 输出后所做的修改 / 验证方式')
WIDTHS = (Cm(3.0), Cm(3.6), Cm(4.5), Cm(4.9))

NOTES = [
    'AI 生成的图片资源均落盘至作品资源目录并纳入打包清单，可按文件名逐一追溯到具体环节。',
    '表中「真实局域网双端对局」与「鼠标转向手感」两项已完成实机验证：两台机器分别以服务端、客户端运行，一局对战完整结束，无断连与卡顿；转向在 125% DPI 缩放下逐档试玩，光标始终固定在客户区中心，未再出现自漂或轴向错乱。',
    '所有 AI 输出均经本地运行、截图比对或自检脚本核对后才合入作品，未直接采用未经验证的原始输出。',
    '玩法平衡数值（安全区缩圈节奏、冲刺耗能、转向灵敏度档位）由本人在实机试玩后确定，非 AI 给出的默认值。',
]

PROMPTS = [
    ('A.1　2D 主玩法骨架',
     ['用 pygame 从零写一个贪吃蛇游戏：网格移动、吃食物加分、蛇身变长、撞墙和撞自己结束、空格暂停、'
      '结束后按提示重开、最高分保存到本地文件；帧率上限可调，风格做成像素风。']),
    ('A.2　人机对战 AI',
     ['给贪吃蛇写一个电脑对手：AI 用 BFS 找最近的食物，同时避开自己的身体和地图边界，也要避开玩家可能堵截的位置；'
      '难度分三档，简单档故意降低决策质量。']),
    ('A.3　双人联机',
     ['用 Python 的 socket 做一个双人联机贪吃蛇：一台机器做服务端、另一台做客户端；服务端权威判定吃食与死亡，'
      '客户端只负责渲染和插值显示；端口 50007；断线要能被检测到并给出提示。',
      '联机模式我这边能开两个窗口对战，但担心真实两台机器连不上，帮我检查监听地址、端口复用和首包握手有没有隐患。']),
    ('A.4　3D 天空贪吃蛇原型',
     ['用 ursina 做一个 3D 空中贪吃蛇：蛇能在地图里上下飞（俯冲和爬升），相机在蛇头后方第三人称跟随；'
      '世界里要有程序化生成的地形和浮岛；蛇身要按跟随关系逐节排列，身体不能穿过地面。']),
    ('A.5　转向排障（第 1 轮）',
     ['3D 天空贪吃蛇游玩时镜头乱晃、转向过快，鼠标往一个方向稍微动一点视角就飞出去，这是不是系统兼容问题？'
      '请给出根因分析和成熟的处理方案。']),
    ('A.6　转向排障（第 2 轮）',
     ['转向依旧有问题：当转向幅度超过某个阈值时就开始乱飘，而且左右不分、上下不分。'
      '请找开源成熟的方案来优化，不要自己发明算法。']),
    ('A.7　转向排障（第 3 轮）',
     ['光标要固定在屏幕中心，采用现在主流 FPS 的那种转向方式：每帧读一次位移，然后把指针钉回中心。'
      '但改完之后光标还是会移动，当前主流的做法到底是什么？']),
    ('A.8　实现方式对照',
     ['和平精英这类手游是怎么实现转向的？PC 端 3A 大作是用鼠标如何实现的？我这种自己取 Win32 位移的做法还有没有兼容问题？']),
    ('A.9　菜单底图（文生图）',
     ['2048×1152 的游戏二级菜单底图：浅蓝色天空与云海，周围环绕卡通糖果色的蛇，中间留出四条胶囊按钮的位置；'
      '不要出现任何文字。']),
    ('A.10　首页循环视频',
     ['首页的视频要真正循环播放、看不出接缝；现在看起来像一张静态图，而且只有 960×540，能不能提到 1080p？'
      '请先说明为什么会看起来是静止的。']),
    ('A.11　打包报错',
     ['用 pyinstaller 打包后运行 3D 模式报 DLL load failed while importing _ctypes，找不到指定的模块，'
      '但源码直接运行完全正常，怎么修？']),
    ('A.12　代码审查',
     ['通读整个项目，按功能缺陷、第三方依赖、资源冗余、性能问题四个维度给一份检查报告，'
      '每一项标注严重程度和修复建议。']),
]


def set_run(run, cn='宋体', en='Times New Roman', size=12, bold=False, color=None):
    run.font.name = en
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn('w:rFonts'))
    if fonts is None:
        fonts = OxmlElement('w:rFonts')
        rpr.append(fonts)
    fonts.set(qn('w:eastAsia'), cn)
    fonts.set(qn('w:ascii'), en)
    fonts.set(qn('w:hAnsi'), en)
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = color if color else RGBColor(0, 0, 0)


def shade(cell, hex_fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_fill)
    tcpr.append(shd)


def row_flag(row, tag):
    trpr = row._tr.get_or_add_trPr()
    trpr.append(OxmlElement('w:' + tag))


def add_page_number(par):
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r1 = par.add_run()
    set_run(r1, size=10.5)
    begin = OxmlElement('w:fldChar')
    begin.set(qn('w:fldCharType'), 'begin')
    r1._element.append(begin)

    r2 = par.add_run()
    set_run(r2, size=10.5)
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = ' PAGE '
    r2._element.append(instr)

    r3 = par.add_run()
    set_run(r3, size=10.5)
    end = OxmlElement('w:fldChar')
    end.set(qn('w:fldCharType'), 'end')
    r3._element.append(end)


def body(text, indent_cm=0.85, size=12, cn='宋体', space_before=0):
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(indent_cm)
    p.paragraph_format.line_spacing = 1.5
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(0)
    set_run(p.add_run(text), cn=cn, size=size)
    return p


doc = Document()

sec = doc.sections[0]
sec.page_width = Cm(21.0)
sec.page_height = Cm(29.7)
for m in ('top_margin', 'bottom_margin', 'left_margin', 'right_margin'):
    setattr(sec, m, Cm(2.5))

normal = doc.styles['Normal']
normal.font.name = 'Times New Roman'
normal.font.size = Pt(12)
normal.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
normal.paragraph_format.line_spacing = 1.5
normal.paragraph_format.space_before = Pt(0)
normal.paragraph_format.space_after = Pt(0)

add_page_number(sec.footer.paragraphs[0])

# 标题
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(18)
set_run(p.add_run('AI 使用声明'), cn='黑体', size=18, bold=True)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(12)
set_run(p.add_run('作品《像素贪吃蛇》：2D 经典 / 人机 / 联机 与 3D 天空贪吃蛇　　'
                  '开发期：2026 年 7 月—2026 年 11 月'), cn='楷体', size=12)

body('本作品在开发过程中使用生成式人工智能辅助完成部分代码、美术资源与排障工作。'
     '下表逐条记录每一次真实使用：所用工具、介入的具体环节、当时输入的关键提示词，'
     '以及本人对 AI 输出所做的修改和验证方式。凡涉及玩法数值、视觉呈现与发行质量的决定，'
     '均由本人复核后确定。完整提示词原文见附录 A。')

# 一、工具使用明细
h = doc.add_paragraph()
h.paragraph_format.space_before = Pt(14)
h.paragraph_format.space_after = Pt(6)
set_run(h.add_run('一、工具使用明细'), cn='黑体', size=14, bold=True)

cap = doc.add_paragraph()
cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
cap.paragraph_format.space_after = Pt(4)
set_run(cap.add_run('表 1　AI 工具使用明细'), size=10.5)

tbl = doc.add_table(rows=1, cols=4)
tbl.style = 'Table Grid'
tbl.autofit = False

hdr = tbl.rows[0]
row_flag(hdr, 'cantSplit')
row_flag(hdr, 'tblHeader')
for i, text in enumerate(HEAD):
    cell = hdr.cells[i]
    cell.width = WIDTHS[i]
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    shade(cell, 'D9D9D9')
    par = cell.paragraphs[0]
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    par.paragraph_format.line_spacing = 1.0
    par.paragraph_format.first_line_indent = Cm(0)
    par.paragraph_format.space_before = Pt(0)
    par.paragraph_format.space_after = Pt(0)
    set_run(par.add_run(text), size=10.5, bold=True)

for idx, data in enumerate(ROWS):
    row = tbl.add_row()
    row_flag(row, 'cantSplit')
    for i, text in enumerate(data):
        cell = row.cells[i]
        cell.width = WIDTHS[i]
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        par = cell.paragraphs[0]
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER if i == 0 else WD_ALIGN_PARAGRAPH.LEFT
        par.paragraph_format.line_spacing = 1.0
        par.paragraph_format.first_line_indent = Cm(0)
        par.paragraph_format.space_before = Pt(1)
        par.paragraph_format.space_after = Pt(1)
        set_run(par.add_run(text), size=10.5)
    if idx % 2 == 1:
        for i in range(4):
            shade(row.cells[i], 'F2F2F2')

# 二、补充说明
h = doc.add_paragraph()
h.paragraph_format.space_before = Pt(14)
h.paragraph_format.space_after = Pt(6)
set_run(h.add_run('二、补充说明'), cn='黑体', size=14, bold=True)

for i, text in enumerate(NOTES, 1):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.85)
    p.paragraph_format.first_line_indent = Cm(0)
    p.paragraph_format.line_spacing = 1.5
    set_run(p.add_run('%d. %s' % (i, text)))

# 落款
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(24)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
p.paragraph_format.first_line_indent = Cm(0)
set_run(p.add_run('声明人（签名）：　　　　　　　　'), size=12)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
set_run(p.add_run('日期：　　　　年　　月　　日'), size=12)

# 附录 A
ap = doc.add_paragraph()
ap.alignment = WD_ALIGN_PARAGRAPH.CENTER
ap.paragraph_format.page_break_before = True
ap.paragraph_format.space_after = Pt(12)
set_run(ap.add_run('附录 A　关键提示词原文'), cn='黑体', size=16, bold=True)

body('以下按发问顺序记录各环节实际输入的提示词原文，长对话按轮次分段列出。'
     '表中「关键提示词」一栏为据此摘录。完整提示词原文见附录 A。'.replace('。完整提示词原文见附录 A。', '。'),
     indent_cm=0.85, size=10.5, cn='楷体')

for title, texts in PROMPTS:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(9)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.first_line_indent = Cm(0)
    set_run(p.add_run(title), cn='黑体', size=12, bold=True)
    for t in texts:
        q = doc.add_paragraph()
        q.paragraph_format.left_indent = Cm(0.85)
        q.paragraph_format.right_indent = Cm(0.4)
        q.paragraph_format.first_line_indent = Cm(0)
        q.paragraph_format.line_spacing = 1.5
        q.paragraph_format.space_after = Pt(3)
        set_run(q.add_run('「%s」' % t), size=12)

doc.save(str(OUT))
print('saved', OUT, OUT.stat().st_size)
