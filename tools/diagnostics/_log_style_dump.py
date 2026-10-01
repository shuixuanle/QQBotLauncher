# -*- coding: utf-8 -*-
"""N2.3 诊断：日志控件为什么不跟主题（样式表到底有没有设进去）。

只读。用法：
    python _log_style_dump.py
"""

import sys

from PyQt6.QtWidgets import QApplication, QPlainTextEdit

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.config import BotConfig  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def inspect(app, window, title: str) -> None:
    print("\n================ {} ================".format(title))
    editors = window.findChildren(QPlainTextEdit)
    print("  找到日志控件 {} 个".format(len(editors)))
    for index, editor in enumerate(editors[:3]):
        print("  ---- 控件 #{} ----".format(index + 1))
        print("    objectName      =", repr(editor.objectName()))
        print("    isReadOnly      =", editor.isReadOnly())
        own = editor.styleSheet()
        print("    自身 styleSheet 长度 =", len(own))
        if own:
            head = own.strip().replace("\n", " ")[:160]
            print("    自身 styleSheet 摘要 =", head)
        else:
            print("    !! 自身没有 styleSheet —— 样式没设进去")
        parent = editor.parent()
        print("    父控件          =", type(parent).__name__,
              "| 父的 styleSheet 长度 =",
              len(parent.styleSheet()) if parent is not None else "无父")
        # 逐级向上找最近的 styleSheet
        node, hops = editor, 0
        while node is not None and hops < 6:
            if node.styleSheet():
                print("    最近带样式的祖先 =", type(node).__name__,
                      "（第 {} 层，长度 {}）".format(hops, len(node.styleSheet())))
                break
            node = node.parent()
            hops += 1
        else:
            print("    最近带样式的祖先 = 无")


def main() -> int:
    app = QApplication(sys.argv[:1])
    config = BotConfig.load("bots_config.json", create_if_missing=False)
    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    app.processEvents()
    window.open_bot_tab(config.bots[0].id, focus=True)
    app.processEvents()

    for mode, name in ((theme_tokens.MODE_DARK, "深色"),
                       (theme_tokens.MODE_LIGHT, "浅色")):
        window.set_theme_mode(mode)
        app.processEvents()
        inspect(app, window, "{} 模式".format(name))

    print("\n判读：")
    print("  · 若「自身 styleSheet」为空 → ProgramWidget._apply_style 没被调用或报了异常")
    print("  · 若自身样式里 background-color 是对的，但 dump 显示的底色不对")
    print("    → 说明选择器写错了（objectName/类型不匹配）")
    window._closing = True
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
