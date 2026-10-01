# -*- coding: utf-8 -*-
"""QQBot 启动管理器 - 应用包。

模块划分
--------
    main.py                 程序入口（在项目根目录，不属于本包）
    app.config              配置模型与 bots_config.json 读写
    app.process_manager     QProcess 封装：启动 / 日志转发 / 停止进程树
    app.ui                  界面层
        app.ui.main_window        主窗口：工具栏 + Bot 标签页容器 + QSettings
        app.ui.bot_tab            单个 Bot 的大标签页（单日志窗或上下分屏）
        app.ui.program_widget     只读日志控件（append_log）
        app.ui.edit_bot_dialog    新建 / 编辑 Bot 的对话框

依赖方向（避免循环导入）
------------------------
    app.ui.*  ->  app.process_manager  ->  app.config
    app.ui.*  ->  app.config

    app.config 不导入任何其它子模块，可以单独使用：
        from app.config import BotConfig
        config = BotConfig.load()
"""

from __future__ import annotations

__all__ = [
    "APP_DISPLAY_NAME",
    "SUBMODULES",
    "VERSION",
    "__version__",
]

#: 程序版本号
VERSION = "1.0.0"
__version__ = VERSION

#: 界面显示名（与 app.ui.main_window.APP_NAME 保持一致）
APP_DISPLAY_NAME = "QQBot启动管理器"

#: 子模块清单，便于 tools/check_setup.py 等脚本遍历验证
SUBMODULES = (
    "app.config",
    "app.process_manager",
    "app.ui",
    "app.ui.main_window",
    "app.ui.bot_tab",
    "app.ui.program_widget",
    "app.ui.edit_bot_dialog",
)
