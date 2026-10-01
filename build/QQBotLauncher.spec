# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 普通模式（不提权）。

产出：dist/QQBot启动管理器.exe
特点：
  · --windowed：不带控制台，双击即用，只有管理器界面
  · 未请求提权：需要提权的程序（如消防栓）启动时仍会弹一次 UAC
  · 打包后 bots_config.json 放在 **exe 旁边**（见 app/config.py 的 bundle_root）

构建（推荐用 tools\\build_exe.py，它会做前置检查）：
    pyinstaller build\\QQBotLauncher.spec --noconfirm --clean

注意：构建机需要装好 PyQt6 + PyInstaller（见 requirements.txt）。
"""

from pathlib import Path

# SPECPATH 由 PyInstaller 注入，指向本文件所在目录（build/）
PROJECT_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821

block_cipher = None

# 需要一起打进去的数据文件（当前只有脚本示例；配置文件不打进去，
# 因为首次运行会在 exe 旁边自动生成）
datas = [
    (str(PROJECT_ROOT / "scripts"), "scripts"),
]

hiddenimports = [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
]

a = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 明显用不到的大块头，减小体积
        "tkinter",
        "unittest",
        "pydoc",
        "doctest",
        "test",
        "PyQt6.QtQml",
        "PyQt6.QtQuick",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtNetwork",
        "PyQt6.QtSql",
        "PyQt6.QtTest",
        "PyQt6.QtMultimedia",
        "PyQt6.QtBluetooth",
        "PyQt6.QtNfc",
        "PyQt6.QtPositioning",
        "PyQt6.QtSensors",
        "PyQt6.QtSerialPort",
        "PyQt6.QtDesigner",
        "PyQt6.QtHelp",
        "PyQt6.QtPdf",
        "PyQt6.QtPdfWidgets",
        "PyQt6.QtSvgWidgets",
        "PyQt6.QtOpenGL",
        "PyQt6.QtOpenGLWidgets",
        "PyQt6.Qt3DCore",
        "PyQt6.QtCharts",
        "PyQt6.QtDataVisualization",
        "numpy",
        "PIL",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="QQBot启动管理器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无控制台：双击只出界面
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # manifest 决定"要不要管理员"：普通模式用 asInvoker（默认，不显式指定）
    # 图标：存在就带上
    icon=str(PROJECT_ROOT / "assets" / "app.ico")
    if (PROJECT_ROOT / "assets" / "app.ico").exists()
    else None,
)
