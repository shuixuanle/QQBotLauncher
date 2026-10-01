# -*- coding: utf-8 -*-
"""N2.2 诊断：把界面各处真正的颜色打出来（找出"白字/黑块"来自哪里）。

只读。用法：
    python _theme_dump.py
"""

import sys

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QLabel, QPlainTextEdit

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.config import BotConfig  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def qss_bg(qss: str) -> str:
    try:
        return qss.split("background-color: ", 1)[1].split(";", 1)[0].strip()
    except IndexError:
        return "?"


def describe(label: str, color: QColor) -> str:
    return "{:<22} {:<10} 亮度={:<4} 明暗={}".format(
        label, color.name(), color.lightness(),
        "深" if color.lightness() < 128 else "浅",
    )


def dump(app, window, title: str) -> None:
    print("\n================ {} ================".format(title))
    pal = app.palette()
    for role_name in ("Window", "WindowText", "Base", "Text", "Button",
                      "ButtonText", "Mid", "Highlight"):
        role = getattr(QPalette.ColorRole, role_name)
        print("  " + describe("调色板 " + role_name, pal.color(role)))

    print("  " + describe("nav_tree_qss 底色",
                          QColor(qss_bg(theme_tokens.nav_tree_qss(window)))))
    print("  " + describe("pane_qss 标题栏底色",
                          QColor(qss_bg(theme_tokens.pane_qss(window)))))
    log_qss = theme_tokens.log_editor_qss(window)
    print("  " + describe("log_editor_qss 底色", QColor(qss_bg(log_qss))))
    text_color = log_qss.split("color: ", 1)[1].split(";", 1)[0].strip()
    print("  " + describe("log_editor_qss 文字色", QColor(text_color)))

    # 真正落到控件上的值
    for name, widget in (
        ("nav_panel", window.nav_panel),
        ("placeholder_page", window.placeholder_page),
    ):
        if widget is None:
            continue
        print("  " + describe("{} 实际底色".format(name),
                              widget.palette().color(QPalette.ColorRole.Window)))
    editors = window.findChildren(QPlainTextEdit)
    for editor in editors[:2]:
        print("  " + describe("日志控件 {} 底色".format(editor.objectName()),
                              editor.palette().color(QPalette.ColorRole.Base)))
        print("  " + describe("日志控件 {} 文字色".format(editor.objectName()),
                              editor.palette().color(QPalette.ColorRole.Text)))
    labels = window.findChildren(QLabel)
    tints = {}
    for label in labels:
        # 只看窗口标题栏/窗格标题这类"上方文字"
        if label.objectName() in ("paneTitle", "paneStatus", "panePid"):
            color = label.palette().color(label.foregroundRole())
            tints[label.objectName()] = color
    for key, color in tints.items():
        print("  " + describe("窗格标题 {} 文字色".format(key), color))


def main() -> int:
    app = QApplication(sys.argv[:1])
    config = BotConfig.load("bots_config.json", create_if_missing=False)
    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    app.processEvents()
    # 打开一个窗口，让窗格、日志控件都真实存在
    window.open_bot_tab(config.bots[0].id, focus=True)
    app.processEvents()

    for mode, name in ((theme_tokens.MODE_DARK, "深色"),
                       (theme_tokens.MODE_LIGHT, "浅色"),
                       (theme_tokens.MODE_SYSTEM, "跟随系统")):
        window.set_theme_mode(mode)
        app.processEvents()
        dump(app, window, "{} 模式（Window 亮度 {}）".format(
            name, theme_tokens.palette_window_lightness(app)))

    print("\n判读：")
    print("  · 若「log_editor_qss 底色」在浅色下是白的，但你看到的右侧是黑的")
    print("    → 说明控件没重新套样式（apply_theme 没走到它）")
    print("  · 若「窗格标题 XXX 文字色」在浅色下仍是白的（亮度很高）")
    print("    → 说明取的是 Text 角色，应改用 WindowText")
    window._closing = True
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
