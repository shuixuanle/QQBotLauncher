# -*- coding: utf-8 -*-
"""最小可视化实验：QPlainTextEdit 的样式串 vs Qt 实际调色板。

会打开一个小窗口，两个按钮切换深浅；每次切换后：
· 打印"我设进去的样式串颜色"
· 打印"Qt 实际认为的调色板颜色（渲染就用它）"
这样就能看出样式串有没有真的生效。

用法：
    python _minimal_theme.py
"""

import sys

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.ui import theme as theme_tokens  # noqa: E402

app = QApplication(sys.argv[:1])

window = QWidget()
window.resize(660, 320)
outer = QVBoxLayout(window)

row = QHBoxLayout()
dark_button = QPushButton("切深色")
light_button = QPushButton("切浅色")
row.addWidget(dark_button)
row.addWidget(light_button)
row.addStretch(1)
outer.addLayout(row)

editor = QPlainTextEdit(window)
editor.setObjectName("programLog")
editor.setReadOnly(True)
editor.setPlaceholderText("（暂无日志输出）")
editor.setPlainText("这是一行日志：样式有没有生效，看底色和文字色")
outer.addWidget(editor, 1)

info = QLabel("（切换后会打印数值）", window)
outer.addWidget(info)


def qss_value(qss: str, key: str) -> str:
    tag = "{}: ".format(key)
    if tag not in qss:
        return "?"
    return qss.split(tag, 1)[1].split(";", 1)[0].strip()


def refresh(mode: str) -> None:
    theme_tokens.apply_theme(app, mode)
    qss = theme_tokens.log_editor_qss(editor)
    editor.setStyleSheet(qss)
    app.processEvents()

    want_bg = qss_value(qss, "background-color")
    want_fg = qss_value(qss, "color")
    got_bg = editor.palette().color(QPalette.ColorRole.Base).name()
    got_fg = editor.palette().color(QPalette.ColorRole.Text).name()
    same = want_bg.lower() == got_bg.lower()

    line = ("mode={:<6} 样式串 bg={} fg={} | Qt 实际 bg={} fg={} | {}"
            .format(mode, want_bg, want_fg, got_bg, got_fg,
                    "一致" if same else "不一致 <<< 问题在这"))
    print(line)
    info.setText(line)


dark_button.clicked.connect(lambda: refresh(theme_tokens.MODE_DARK))
light_button.clicked.connect(lambda: refresh(theme_tokens.MODE_LIGHT))

print("窗口已打开：点按钮切换，或看下面自动跑的两种模式\n")
refresh(theme_tokens.MODE_DARK)
refresh(theme_tokens.MODE_LIGHT)
refresh(theme_tokens.MODE_SYSTEM)
print("\n（关掉窗口即可结束）")
window.show()
sys.exit(app.exec())
