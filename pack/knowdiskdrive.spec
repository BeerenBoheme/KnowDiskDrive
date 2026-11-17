# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格：把 KnowDiskDrive 打成可执行程序。

用法（在项目根目录）：

    pip install "pyinstaller>=6.3"
    pyinstaller --noconfirm --clean pack/knowdiskdrive.spec
    ./dist/knowdiskdrive/knowdiskdrive-gui

产物是 dist/knowdiskdrive/ 整个目录，一起拷贝到目标机器即可
（要求同 CPU 架构、目标机 glibc >= 2.28；不需要装 Python 或 PySide6）。
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - SPECPATH 由 PyInstaller 注入
sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "knowdiskdrive", "data")

# 随包资源：.desktop 菜单项模板与 SVG 图标。
# 目标路径必须与包内路径一致，importlib.resources 才找得到。
datas = [
    (os.path.join(DATA_DIR, "knowdiskdrive.desktop"), "knowdiskdrive/data"),
    (os.path.join(DATA_DIR, "knowdiskdrive.svg"), "knowdiskdrive/data"),
]

# Qt 里本项目用不到的大块头，排除后体积能少一大截。
excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtDataVisualization",
    "PySide6.QtCharts",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "tkinter",
    "matplotlib",
    "numpy",
    "pytest",
]

a = Analysis(
    [os.path.join(ROOT, "pack", "entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=["knowdiskdrive.gui", "knowdiskdrive.gui.app"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="knowdiskdrive-gui",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="knowdiskdrive",
)

# 单文件模式：把 EXE 换成下面这段、删掉 COLLECT（启动慢几秒，但只有一个文件）。
#    exe_onefile = EXE(
#        pyz, a.scripts, a.binaries, a.datas, [],
#        name="knowdiskdrive-gui", debug=False, strip=False, upx=False, console=False,
#    )
# 单文件模式下资源解到 sys._MEIPASS/knowdiskdrive/data，knowdiskdrive/gui/icon.py
# 与 knowdiskdrive/data/__init__.py 都已处理该路径。
