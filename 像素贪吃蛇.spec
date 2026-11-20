# -*- mode: python ; coding: utf-8 -*-

import sys
import os
from PyInstaller.utils.hooks import collect_all

# conda 解释器的运行时 DLL（ffi / ssl / crypto 等）在 <base>\Library\bin，
# PyInstaller 不会自动收集；_ctypes.pyd 依赖 ffi.dll，缺它会在 frozen 下 ImportError。
_libbin = os.path.join(sys.base_prefix, 'Library', 'bin')
_extra_dlls = ['ffi.dll', 'ffi-7.dll', 'ffi-8.dll',
               'libcrypto-3-x64.dll', 'libssl-3-x64.dll', 'sqlite3.dll', 'zlib.dll']

datas = [
    ('assets/fonts/msyh.ttc', 'assets/fonts'),
    ('assets/audio/eat.wav', 'assets/audio'),
    ('assets/audio/death.wav', 'assets/audio'),
    ('assets/audio/game.wav', 'assets/audio'),
    ('assets/audio/menu.wav', 'assets/audio'),
    ('assets/menu_bg.png', 'assets'),
    ('assets/home_bg.jpg', 'assets'),
    ('assets/home_bg_wide.jpg', 'assets'),
    ('assets/btn_texture.png', 'assets'),
    ('assets/home_loop.bin', 'assets'),
    ('assets/home_loop_hd.bin', 'assets'),
    ('assets/home_menu_original.jpg', 'assets'),
    ('assets/home_menu_bg_clean.jpg', 'assets'),
    ('assets/home_menu_bg.jpg', 'assets'),
    ('assets/menu_sky_art.png', 'assets'),
    ('assets/menu_classic_art.png', 'assets'),
    ('assets/menu_diff_art.png', 'assets'),
    ('assets/menu_diff_art_clean.png', 'assets'),
    ('assets/menu_diff_solo_art.png', 'assets'),
    ('assets/menu_diff_sky_art.png', 'assets'),
    ('assets/sky_panorama.png', 'assets'),
    ('assets/soft_ripple_ring.png', 'assets'),
    ('assets/icons', 'assets/icons'),
    # 如有其他资源（如图片、字体）请在此追加
]
binaries = []
for _f in _extra_dlls:
    _p = os.path.join(_libbin, _f)
    if os.path.exists(_p):
        binaries.append((_p, '.'))
hiddenimports = []

# 3D 引擎（天空贪吃蛇 3D / --sky3d 子进程）：ursina + panda3d 全部资源与动态库
for _pkg in ('ursina', 'panda3d', 'pandac'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

hiddenimports += ['sky3d_mode', 'sky3d_net_mode', 'sky3d_ai_mode']

a = Analysis(
    ['app_shell.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='像素贪吃蛇',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
