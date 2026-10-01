# -*- coding: utf-8 -*-
"""菜单栏状态诊断：原生开关 / 样式表 / 各主题下的实际颜色。

只读（临时改主题，结束恢复）。用法：
    python tools\\diagnostics\\_menu_probe.py
"""

import sys

from PyQt6.QtCore import QEventLoop, QSettings, QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.config import BotConfig  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = QSettings(ORG_NAME, APP_NAME)
    keep = settings.value(theme_tokens.SETTINGS_THEME_KEY, None)

    config = BotConfig.load("bots_config.json", create_if_missing=False)
    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    wait(400)

    bar = window.menuBar()
    print("=" * 66)
    print("菜单栏基本信息")
    print("  isNativeMenuBar()   =", bar.isNativeMenuBar())
    print("  样式表长度          =", len(bar.styleSheet()))
    print("  窗口样式表长度      =", len(window.styleSheet()))
    print("  是否可见            =", bar.isVisible())
    print("=" * 66)

    menu = bar.actions()[0].menu() if bar.actions() else None

    try:
        for mode, name in ((theme_tokens.MODE_LIGHT, "浅色"),
                           (theme_tokens.MODE_DARK, "深色"),
                           (theme_tokens.MODE_LIGHT, "再浅色"),
                           (theme_tokens.MODE_SYSTEM, "跟随系统")):
            window.set_theme_mode(mode)
            wait(120)
            bar_pal = bar.palette()
            win_pal = window.palette()
            window_bg = win_pal.color(QPalette.ColorRole.Window)
            print("\n--- {} ---".format(name))
            print("  窗口 Window        = {} 亮度={}".format(
                window_bg.name(), window_bg.lightness()))
            print("  菜单栏 palette     WindowText={} Button={} Base={}".format(
                bar_pal.color(QPalette.ColorRole.WindowText).name(),
                bar_pal.color(QPalette.ColorRole.Button).name(),
                bar_pal.color(QPalette.ColorRole.Base).name()))
            print("  当前 scheme        = {} | is_dark={}".format(
                theme_tokens.current_scheme(), theme_tokens.is_dark(bar)))
            # 菜单栏文字色：从 QSS 里能查到的（QT 不暴露"最终解析色"，只能看样式表）
            qss = bar.styleSheet()
            text_in_qss = "?"
            if "QMenuBar { color: " in qss:
                text_in_qss = qss.split("QMenuBar { color: ", 1)[1].split(";", 1)[0].strip()
            print("  菜单栏 QSS 里的文字色 = {}".format(text_in_qss))
            if menu is not None:
                print("  首个菜单 palette   WindowText={}".format(
                    menu.palette().color(QPalette.ColorRole.WindowText).name()))
    finally:
        if keep is None:
            settings.remove(theme_tokens.SETTINGS_THEME_KEY)
        else:
            settings.setValue(theme_tokens.SETTINGS_THEME_KEY, keep)
        settings.sync()
        window._closing = True
        window.close()

    print("\n判读：")
    print("  · isNativeMenuBar=True → 原生菜单栏没关掉（颜色由系统决定，改不动）")
    print("  · QSS 文字色在浅色下应偏暗、深色下应偏亮")
    print("  · 若 QSS 正确但界面不对 → 原生绘制覆盖了 QSS，需改成自绘菜单栏")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
