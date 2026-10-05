# -*- coding: utf-8 -*-
"""QQBot 启动管理器 - 界面包。

包含六个界面模块（本文件刻意不在导入时自动加载它们，以避免循环导入）：

    main_window.py       主窗口：顶部工具栏、左侧竖栏导航、实例区、QSettings 持久化
    bot_tab.py           单个 Bot 的实例区：递归窗格树（分屏 / 底部程序标签）
    program_widget.py    只读日志控件，核心接口 append_log(text)（含 ANSI 上色）
    edit_bot_dialog.py   新建 / 编辑 Bot 的对话框
    palette_dialog.py    配色工作台（视图 → 外观 → 配色工作台…），改完立即生效
    start_menu.py        「启动全部 ▾」的层级勾选菜单（挑这次要启动哪些程序）

推荐导入方式（显式到模块，不做包级别的预加载）：

    from app.ui.main_window import MainWindow
    from app.ui.bot_tab import BotTab
    from app.ui.program_widget import ProgramWidget
    from app.ui.edit_bot_dialog import EditBotDialog
"""

from __future__ import annotations

__all__ = [
    "BotTab",
    "EditBotDialog",
    "MainWindow",
    "PaletteDialog",
    "ProgramWidget",
    "load_all",
]


def load_all():
    """按依赖顺序导入全部界面模块，返回 (MainWindow, BotTab, ProgramWidget, EditBotDialog)。

    仅在需要一次性拿到全部界面类时调用（例如自检脚本）；
    正常代码请直接 from app.ui.xxx import yyy，避免不必要的加载顺序问题。

    注意：本函数需要 PyQt6 与已创建的 QApplication，且只应在 PyQt6 安装后调用。
    """
    import importlib

    order = ("program_widget", "edit_bot_dialog", "bot_tab", "main_window", "palette_dialog")
    classes = {}
    for name in order:
        module = importlib.import_module("app.ui." + name)
        for attribute in ("ProgramWidget", "EditBotDialog", "BotTab", "MainWindow",
                          "PaletteDialog"):
            if hasattr(module, attribute):
                classes[attribute] = getattr(module, attribute)
    return (
        classes["MainWindow"],
        classes["BotTab"],
        classes["ProgramWidget"],
        classes["EditBotDialog"],
    )


def __getattr__(name: str):
    """按需加载：`from app.ui import MainWindow` 时自动导入对应模块。"""
    mapping = {
        "MainWindow": "app.ui.main_window",
        "BotTab": "app.ui.bot_tab",
        "ProgramWidget": "app.ui.program_widget",
        "EditBotDialog": "app.ui.edit_bot_dialog",
        "PaletteDialog": "app.ui.palette_dialog",
    }
    module_name = mapping.get(name)
    if module_name is None:
        raise AttributeError("module {!r} has no attribute {!r}".format(__name__, name))
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
