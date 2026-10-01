# -*- coding: utf-8 -*-
"""N2.3 决定性实验：把 log_colors() 的输入调色板与输出颜色同时打出来。

只读。用法：
    python _logcolor_probe.py
"""

import sys

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.ui import theme as theme_tokens  # noqa: E402

app = QApplication(sys.argv[:1])

original = theme_tokens.log_colors


def traced(widget=None):
    result = original(widget)
    pal = widget.palette() if widget is not None else app.palette()
    print("    [log_colors] 输入 Base={} Text={} Mid={} Highlight={}".format(
        pal.color(QPalette.ColorRole.Base).name(),
        pal.color(QPalette.ColorRole.Text).name(),
        pal.color(QPalette.ColorRole.Mid).name(),
        pal.color(QPalette.ColorRole.Highlight).name(),
    ))
    print("    [log_colors] 输出 bg={} fg={} border={} sel={}".format(*result))
    return result


theme_tokens.log_colors = traced

for mode in (theme_tokens.MODE_DARK, theme_tokens.MODE_LIGHT, theme_tokens.MODE_SYSTEM):
    theme_tokens.apply_theme(app, mode)
    pal = app.palette()
    print("\n===== mode={} | Window亮度={} =====".format(
        mode, theme_tokens.palette_window_lightness(app)))
    print("  app.palette(): Window={} WindowText={} Base={} Text={} Mid={} Highlight={}".format(
        pal.color(QPalette.ColorRole.Window).name(),
        pal.color(QPalette.ColorRole.WindowText).name(),
        pal.color(QPalette.ColorRole.Base).name(),
        pal.color(QPalette.ColorRole.Text).name(),
        pal.color(QPalette.ColorRole.Mid).name(),
        pal.color(QPalette.ColorRole.Highlight).name(),
    ))
    print("  最终 qss =", theme_tokens.log_editor_qss().replace("\n", " "))

print("\n===== 两个构造器的直接输出 =====")
for name, builder in (("dark", theme_tokens._build_dark_palette),
                      ("light", theme_tokens._build_light_palette)):
    pal = builder(app.style().standardPalette())
    print("  {}: Window={} Base={} Text={} Mid={} Highlight={}".format(
        name,
        pal.color(QPalette.ColorRole.Window).name(),
        pal.color(QPalette.ColorRole.Base).name(),
        pal.color(QPalette.ColorRole.Text).name(),
        pal.color(QPalette.ColorRole.Mid).name(),
        pal.color(QPalette.ColorRole.Highlight).name(),
    ))

theme_tokens.log_colors = original
