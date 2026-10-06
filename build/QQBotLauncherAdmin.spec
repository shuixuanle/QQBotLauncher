# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— **管理员模式**。

产出：dist/QQBot启动管理器（管理员）.exe
特点：
  · 内嵌 requireAdministrator 清单（build/admin.manifest）：
    双击时系统直接弹**一次** UAC，之后进程带管理员令牌；
  · 需要提权的程序（如消防栓）由它启动时**不再弹 UAC**（子进程继承令牌）；
  · --windowed：无控制台。

构建：
    pyinstaller build\\QQBotLauncherAdmin.spec --noconfirm --clean
"""

from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821

block_cipher = None

#  只打"给使用者看的说明"，**绝不整目录打包** ——
#  scripts/ 里可能躺着作者本机专用的东西（真机事故 2026-10-06：
#  整目录打包把 hydrant_dir.txt 里的个人路径 / start_hydrant.bat 一起塞进了 exe，
#  别人下载到的包里就带着作者的本机路径）。下面用白名单逐个列出。
datas = [
    (str(PROJECT_ROOT / "scripts" / "README.md"), "scripts"),
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
    name="QQBot启动管理器（管理员）",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 关键：内嵌"需要管理员"清单
    manifest=str(PROJECT_ROOT / "build" / "admin.manifest"),
    icon=str(PROJECT_ROOT / "assets" / "app.ico")
    if (PROJECT_ROOT / "assets" / "app.ico").exists()
    else None,
)
