# -*- coding: utf-8 -*-
"""最小实验 2：样式串原文 / 编辑器调色板 / 计算值 三者并排。

只读。用法：
    python _minimal_theme2.py
"""

import sys

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QPlainTextEdit

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.ui import theme as theme_tokens  # noqa: E402

app = QApplication(sys.argv[:1])

editor = QPlainTextEdit()
editor.setObjectName("programLog")
editor.setReadOnly(True)
editor.setPlaceholderText("（暂无日志输出）")
editor.setPlainText("日志内容")
editor.resize(600, 160)
editor.show()

for mode in (theme_tokens.MODE_DARK, theme_tokens.MODE_LIGHT, theme_tokens.MODE_SYSTEM):
    theme_tokens.apply_theme(app, mode)
    app.processEvents()
    qss = theme_tokens.log_editor_qss()
    editor.setStyleSheet(qss)
    app.processEvents()

    print("\n########## mode={} ##########".format(mode))
    print("  样式串 repr :", repr(qss))
    print("  log_colors() 无参数        :", theme_tokens.log_colors())
    print("  log_colors(editor)         :", theme_tokens.log_colors(editor))
    print("  应用调色板 Base/Text/Mid   : {} / {} / {}".format(
        app.palette().color(QPalette.ColorRole.Base).name(),
        app.palette().color(QPalette.ColorRole.Text).name(),
        app.palette().color(QPalette.ColorRole.Mid).name(),
    ))
    print("  编辑器调色板 Base/Text     : {} / {}".format(
        editor.palette().color(QPalette.ColorRole.Base).name(),
        editor.palette().color(QPalette.ColorRole.Text).name(),
    ))
    print("  编辑器 styleSheet 前 100 字:", repr(editor.styleSheet()[:100]))

print("\n判读：")
print("  · 样式串里 bg 与 fg 应当不同；若相同 → 生成逻辑问题")
print("  · 编辑器调色板 Base/Text 若与样式串一致 → Qt 已采用样式串")
print("  · 若一直不变 → Qt 忽略样式串（要改用 editor.setPalette）")
