# -*- coding: utf-8 -*-
"""真机级检查（**需要 PyQt6**）：把带 ANSI 的日志真喂进日志控件，断言它不炸、且真的上了色。

为什么必须单独写这一条（真机事故，2026-10-02）
----------------------------------------------
给日志区加终端颜色时，`theme.py` 里出现了**两个同名** `log_colors`
（后定义的静默覆盖先定义的，返回 4 个值），而 `_format_for()` 里按
"只返回两个值"去解包：

    ValueError: too many values to unpack (expected 2, got 4)

异常发生在 Qt 的 `output_text` 信号槽里，**PyQt6 对槽里的未捕获异常会直接终止进程** ——
用户看到的就是"启动实例后闪退"，界面上一个字都不给。

这种错静态检查挡不住（名字对、语法对、AST 全绿），必须真的建一个控件、
真的喂一段日志进 `append_log()`。所以本检查器离屏（offscreen）跑完整链路：

    append_log(带 ANSI) → AnsiParser 解析 → QTextCharFormat 上色 → 从文档里读回颜色

用法：
    python tools\\check_ansi_live.py          # 需要 PyQt6；没有会提示装不上就跳过
"""

import os
import sys
from pathlib import Path

#: 离屏渲染：没有显示器 / 远程会话里也能跑
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import PyQt6  # noqa: F401
except ImportError:
    # 这台机器没装 PyQt6：跳过而不是报失败（run_all_checks 里也会跳过本文件）
    print("需要 PyQt6（pip install -r requirements.txt），本机没装 —— 跳过。")
    raise SystemExit(0)

from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtWidgets import QApplication                       # noqa: E402

from app.ui import theme as theme_tokens                       # noqa: E402
from app.ui.program_widget import ProgramWidget                # noqa: E402

failures = []

#: 真机日志里的两行（ATRI 主程序）：INFO 绿色（32），DEBUG 是残缺的扩展色（38;20 → 忽略）
INFO_LINE = "\x1b[32;20m10-02 13:22:52 [INFO] atri-bot.PluginLoader | 插件已加载\x1b[0m\n"
DEBUG_LINE = "\x1b[38;20m10-02 13:22:53 [DEBUG] atri-bot.whitelist | 群相关事件\x1b[0m\n"


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def line_fragments(widget: ProgramWidget, index: int):
    """取某一行的 [(文字, 格式)] 片段（QPlainTextEdit 里每行是一个 block）。"""
    block = widget.editor.document().findBlockByNumber(index)
    out = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            out.append((fragment.text(), fragment.charFormat()))
        iterator += 1
    return out


def color_of(fmt) -> str:
    """片段的前景色；没设颜色时返回空串（用 NoBrush 判断，别拿黑色当"没设"）。"""
    brush = fmt.foreground()
    if brush.style() == Qt.BrushStyle.NoBrush:
        return ""
    return brush.color().name()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    widget = ProgramWidget("ATRI 主程序", "主程序", "", max_lines=500)

    print("[1] 喂真机日志：这一步以前会 ValueError → 整个程序退出")
    try:
        widget.append_log(INFO_LINE)
        widget.append_log(DEBUG_LINE)
        crashed = ""
    except Exception as exc:  # noqa: BLE001 - 诊断脚本：任何异常都要报出来
        crashed = "{}: {}".format(type(exc).__name__, exc)
    check("append_log 带 ANSI 不抛异常", not crashed, crashed)

    shown = widget.text()
    check("界面上没有转义序列", "\x1b" not in shown, repr(shown[:60]))
    check("文字内容完整", "10-02 13:22:52 [INFO] 插件已加载" in shown
          and "10-02 13:22:53 [DEBUG] 群相关事件" in shown)

    print("\n[2] 颜色真的写进文档了（不是「看着像」）")
    palette = theme_tokens.ansi_palette(widget)
    expect_green = palette[2]
    info_frags = line_fragments(widget, 0)
    info_colors = {color_of(fmt) for _text, fmt in info_frags}
    check("INFO 行有片段带显式颜色", any(info_colors), str(sorted(info_colors)))
    check("INFO 行的颜色 = 主题色板里的绿色（32）", expect_green in info_colors,
          "期望 {} 实际 {}".format(expect_green, sorted(info_colors)))

    debug_colors = {color_of(fmt) for _text, fmt in line_fragments(widget, 1)}
    check("残缺的 38;20 不上色（DEBUG 行保持默认色）",
          all(not c for c in debug_colors), str(sorted(debug_colors)))

    print("\n[3] 原文、清空、重建")
    check("原文里保留了序列（换主题要重画）", "\x1b[32;20m" in widget.raw_text())
    widget.apply_theme()          # 换主题会按新色板重画 —— 这里只要求不炸、文字不丢
    check("换主题后文字不变", widget.text() == shown, repr(widget.text()[:40]))
    widget.load_raw_text(INFO_LINE + DEBUG_LINE)
    check("重建后仍然上色", any(color_of(fmt) for _t, fmt in line_fragments(widget, 0)))
    check("重建后没有转义序列", "\x1b" not in widget.text())
    widget.clear_log()
    check("清空连原文一起清", widget.raw_text() == "" and widget.line_count() == 0)

    print("\n[4] 深浅两套主题都要能跑（切主题后取色不能崩）")
    for dark in (False, True):
        try:
            theme_tokens.apply_log_palette(widget.editor, dark)
            theme_tokens.ansi_palette(widget)
            bg, fg = theme_tokens.log_colors(widget)[:2]
            ok, detail = True, "底色 {} 文字 {}".format(bg, fg)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, "{}: {}".format(type(exc).__name__, exc)
        check("{} 主题下取色正常".format("深色" if dark else "浅色"), ok, detail)

    widget.close()
    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
