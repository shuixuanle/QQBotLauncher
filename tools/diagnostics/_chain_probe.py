# -*- coding: utf-8 -*-
"""主程序链路诊断：切换主题时，日志控件的样式到底有没有被更新。

只读（不改注册表以外的东西；会临时改当前主题，结束时恢复）。
用法：
    python _chain_probe.py
"""

import sys

from PyQt6.QtCore import QEventLoop, QSettings, QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QPlainTextEdit

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.config import BotConfig  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def report(window, label: str) -> None:
    editors = window.findChildren(QPlainTextEdit)
    print("  --- {} （日志控件 {} 个）---".format(label, len(editors)))
    print("      当前主题 mode={} scheme={} Window亮度={}".format(
        theme_tokens.current_mode(), theme_tokens.current_scheme(),
        theme_tokens.palette_window_lightness()))
    for index, editor in enumerate(editors[:2]):
        qss = editor.styleSheet()
        bg = "?"
        if "background-color: " in qss:
            bg = qss.split("background-color: ", 1)[1].split(";", 1)[0].strip()
        print("      控件#{} 样式串bg={:<9} 调色板Base={:<9} Text={:<9} 可见={}".format(
            index + 1, bg,
            editor.palette().color(QPalette.ColorRole.Base).name(),
            editor.palette().color(QPalette.ColorRole.Text).name(),
            editor.isVisible()))


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = QSettings(ORG_NAME, APP_NAME)
    keep = settings.value(theme_tokens.SETTINGS_THEME_KEY, None)

    config = BotConfig.load("bots_config.json", create_if_missing=False)
    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    wait(300)

    # 打开两个窗口，让日志控件真实存在
    for bot in config.bots[:2]:
        window.open_bot_tab(bot.id, focus=False)
    wait(300)

    try:
        for mode, name in ((theme_tokens.MODE_DARK, "切深色"),
                           (theme_tokens.MODE_LIGHT, "切浅色"),
                           (theme_tokens.MODE_DARK, "再切深色")):
            print("\n=========== {} ===========".format(name))
            before = [e.styleSheet() for e in window.findChildren(QPlainTextEdit)]
            window.set_theme_mode(mode)
            wait(120)
            after = [e.styleSheet() for e in window.findChildren(QPlainTextEdit)]
            changed = sum(1 for a, b in zip(before, after) if a != b)
            print("  刷新计数 =", window.theme_refresh_count(),
                  "| 样式串发生变化的控件数 = {}/{}".format(changed, len(after)))
            report(window, "切换后")
    finally:
        if keep is None:
            settings.remove(theme_tokens.SETTINGS_THEME_KEY)
        else:
            settings.setValue(theme_tokens.SETTINGS_THEME_KEY, keep)
        settings.sync()
        window._closing = True
        window.close()

    print("\n判读：")
    print("  · 若『样式串发生变化的控件数 = 0』→ _apply_theme 没走到日志控件（补刷新链）")
    print("  · 若样式串变了但『调色板Base』不变 → QSS 不生效（改用 editor.setPalette）")
    print("  · 若两者都变 → 主程序没问题，问题在别处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
