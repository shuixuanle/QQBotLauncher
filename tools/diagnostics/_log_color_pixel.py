# -*- coding: utf-8 -*-
"""诊断：日志区的文字颜色，以及"切换布局后日志内容消失"。

为什么用"渲染成图片再采样像素"：调色板 / 样式表都可能被 Qt 缓存或覆盖，
读属性只能知道"我们设了什么"，**读像素才知道"屏幕上是什么"**。

用法（在有 PyQt6 的机器上运行）：
    python tools\\diagnostics\\_log_color_pixel.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QImage, QPalette  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.program_widget import ProgramWidget  # noqa: E402

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def luminance(color) -> float:
    return 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()


def sample_pixels(editor: QPlainTextEdit):
    """把日志区渲染成图片，返回 (平均亮度, 最暗像素, 最亮像素)。"""
    size = editor.viewport().size()
    if size.width() < 10 or size.height() < 10:
        return None
    image = QImage(size, QImage.Format.Format_RGB32)
    image.fill(0)
    editor.viewport().render(image)

    darkest = None
    lightest = None
    total = 0.0
    count = 0
    for y in range(0, size.height(), 2):
        for x in range(0, size.width(), 2):
            color = image.pixelColor(x, y)
            value = luminance(color)
            total += value
            count += 1
            if darkest is None or value < darkest[0]:
                darkest = (value, color.name())
            if lightest is None or value > lightest[0]:
                lightest = (value, color.name())
    return (total / max(1, count), darkest, lightest)


def describe(editor: QPlainTextEdit) -> None:
    palette = editor.palette()
    base = palette.color(QPalette.ColorRole.Base).name()
    text = palette.color(QPalette.ColorRole.Text).name()
    window_text = palette.color(QPalette.ColorRole.WindowText).name()
    qss = editor.styleSheet().replace("\n", " ")
    print("    objectName            = {}".format(editor.objectName()))
    print("    调色板 Base（底色）    = {}".format(base))
    print("    调色板 Text（文字）    = {}".format(text))
    print("    调色板 WindowText      = {}".format(window_text))
    print("    自身 styleSheet 摘要   = {}".format(qss[:120] or "(空)"))
    print("    纯文本                 = {!r}".format(editor.toPlainText()[:40]))
    # 继承来的样式（祖先链上第一个非空 styleSheet）
    node, hops = editor, 0
    while node is not None and hops < 8:
        if node.styleSheet():
            print("    最近带样式的祖先       = {}（第 {} 层，长度 {}）".format(
                type(node).__name__, hops, len(node.styleSheet())))
            break
        node = node.parent()
        hops += 1
    else:
        print("    最近带样式的祖先       = 无")


def scenario(app: QApplication, mode: str) -> None:
    print("\n================ 场景：{} ================".format(mode))
    theme_tokens.apply_theme(app, mode)
    app.processEvents()

    widget = ProgramWidget(
        program_name="测试程序",
        status="未启动",
        parent=None,
        show_toolbar=True,
    )
    widget.resize(560, 260)
    widget.show()
    app.processEvents()

    editor = widget.editor
    editor.setPlainText("深色模式下这行字必须看得清 —— hello QQBot launcher")
    app.processEvents()
    widget._apply_style()
    app.processEvents()

    print("  ---- 控件属性 ----")
    describe(editor)

    expected_dark = mode == "dark"
    is_dark = theme_tokens.is_dark(editor)
    check("is_dark(editor) == {}".format(expected_dark), is_dark == expected_dark,
          "实际 {}".format(is_dark))

    _, fg, _, _ = theme_tokens.log_colors(editor)
    palette_text = editor.palette().color(QPalette.ColorRole.Text).name()
    check("调色板文字色 == log_colors() 的文字色", palette_text.lower() == fg.lower(),
          "调色板 {} vs 期望 {}".format(palette_text, fg))

    sample = sample_pixels(editor)
    if sample is None:
        check("日志区可渲染取色", False, "尺寸太小")
    else:
        average, darkest, lightest = sample
        print("    ---- 渲染取色 ----")
        print("    平均亮度 = {:.1f}".format(average))
        print("    最暗像素 = {} ({})".format(darkest[1], round(darkest[0], 1)))
        print("    最亮像素 = {} ({})".format(lightest[1], round(lightest[0], 1)))
        base_color = editor.palette().color(QPalette.ColorRole.Base)
        text_color = editor.palette().color(QPalette.ColorRole.Text)
        check("底色与文字色对比足够（能看清）",
              abs(luminance(base_color) - luminance(text_color)) >= 60,
              "底 {:.0f} vs 字 {:.0f}".format(luminance(base_color),
                                              luminance(text_color)))
        if expected_dark:
            check("深色下文字比底色亮（不是黑字配深底）",
                  luminance(text_color) > luminance(base_color),
                  "字 {:.0f} > 底 {:.0f} ?".format(luminance(text_color),
                                                   luminance(base_color)))
        else:
            check("浅色下文字比底色暗", luminance(text_color) < luminance(base_color))

    widget.deleteLater()
    app.processEvents()


def scenario_layout(app: QApplication) -> None:
    """切换布局后，日志内容是否还在。"""
    print("\n================ 场景：切换布局是否保留日志 ================")
    from app.config import BotConfig

    config_path = ROOT / "bots_config.json"
    config = BotConfig.load(str(config_path), create_if_missing=False)
    from app.ui.main_window import MainWindow

    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    app.processEvents()

    bot = config.bots[0]
    window.open_bot_tab(bot.id, focus=True)
    app.processEvents()
    tab = window._tabs.get(bot.id)
    if tab is None:
        check("打开了机器人窗口", False)
        return

    marker = "布局切换探针-这行不能丢"
    for view in tab._views.values():
        view.append_log(marker)
    app.processEvents()

    before = sum(1 for view in tab._views.values()
                 if marker in view.editor.toPlainText())
    print("  切换前含标记的窗格数 = {}".format(before))
    check("切换前日志里有标记", before > 0)

    original = tab.layout_kind()
    for kind in ("v", "h", "single", "tabs"):
        if kind == original:
            continue
        tab.set_layout(kind)
        app.processEvents()
        kept = sum(1 for view in tab._views.values()
                   if marker in view.editor.toPlainText())
        total_lines = sum(len(view.editor.toPlainText().splitlines())
                          for view in tab._views.values())
        print("  切到 {} -> 含标记窗格 {}，总行数 {}".format(kind, kept, total_lines))
        check("切到 {} 后日志仍保留".format(kind), kept > 0)

    window.close()


def main() -> int:
    app = QApplication(sys.argv[:1])
    scenario(app, "dark")
    scenario(app, "light")
    scenario_layout(app)
    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
