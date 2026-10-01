# -*- coding: utf-8 -*-
"""N2.3 实验：Qt 样式表选择器到底怎么写才能命中 QPlainTextEdit。

只读。用法：
    python _selector_probe.py
"""

import sys

from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QVBoxLayout, QWidget

app = QApplication(sys.argv[:1])
host = QWidget()
host.resize(400, 300)
layout = QVBoxLayout(host)

CASES = (
    ("QPlainTextEdit#programLog", "objectName=programLog"),      # 目前用的写法
    ("QPlainTextEdit", "只写类型"),                                # 候选写法
    ("#programLog", "只写 id"),                                   # 候选写法
    ("QPlainTextEdit#otherName", "id 不匹配（应当不生效）"),        # 反例
)

print("测试：给 QPlainTextEdit 设 objectName=programLog，比较各种选择器是否生效")
print("（判据：把样式设在 editor 自身，看 dump 出的调色板是否变成样式里的值）\n")

for selector, note in CASES:
    editor = QPlainTextEdit(host)
    editor.setObjectName("programLog")
    editor.setPlainText("测试")
    layout.addWidget(editor)
    editor.setStyleSheet("{} {{ background-color: #123456; color: #abcdef; }}".format(selector))
    app.processEvents()
    base = editor.palette().color(editor.backgroundRole()).name()
    text = editor.palette().color(editor.foregroundRole()).name()
    hit = base.lower() == "#123456"
    print("  选择器 {:<26} {:<22} 底色={:<9} 文字={:<9} {}".format(
        selector, note, base, text, "命中" if hit else "未命中"))

print("\n结论：哪个选择器命中，就用哪个 —— 同时说明 Qt 里 #id 的语义是『后代』而不是『自身』。")
host.close()
