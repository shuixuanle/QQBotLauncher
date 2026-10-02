# -*- coding: utf-8 -*-
"""静态 + 轻量运行检查：日志区要"像终端"，ANSI 序列不能漏到界面上。

挡住的坑（真机截图）：
  机器人往 stdout 写了 `←[32;20m10-02 13:22:52 [INFO] …←[0m`，
  而日志区把转义序列**原样显示**成 `[32;20m…[0m` 这种乱码，一行行全是它。
同一个程序在 Windows Terminal 里是"INFO 绿色、DEBUG 默认色"。

本检查器做两件事：
  1. **跑一遍 `app/ansi.py` 的解析逻辑**（纯 Python，不需要 PyQt6）：
     真机那两种序列、跨块切开的序列、256 色/真彩色、复位、残缺参数；
  2. **用 AST 确认界面层真的接上了**（免得"解析器写好了但没人调用"）：
     · `append_log` 走 `_write_runs()` / `_remember_raw()`；
     · `_write_runs()` 用解析器，且**颜色全部来自 theme**（文件里不许出现 #rrggbb）；
     · `clear_log()` 重置解析器与原文；`apply_theme()` 会按新主题重画；
     · 布局切换的快照走 `raw_text()` / `load_raw_text()`（颜色不丢）。

用法：
    python tools\\check_ansi_log.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ansi import AnsiColor, AnsiParser, has_ansi, strip_ansi  # noqa: E402

PROGRAM_WIDGET = ROOT / "app" / "ui" / "program_widget.py"
BOT_TAB = ROOT / "app" / "ui" / "bot_tab.py"
ANSI_MODULE = ROOT / "app" / "ansi.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def find_function(tree: ast.AST, name: str):
    """按名字找类方法 / 函数（只找顶层与类体一层，够用且不误伤嵌套）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def calls_in(node: ast.AST) -> set:
    """收集一个函数里出现过的调用名（`self._write_runs(x)` → `_write_runs`）。"""
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def run_parser_checks() -> None:
    """真机序列跑一遍（这部分不需要 PyQt6）。"""
    print("[1] 解析真机日志里的序列")
    parser = AnsiParser()
    runs = parser.feed("\x1b[32;20m10-02 13:22:52 [INFO] 插件已加载\x1b[0m\n")
    text = "".join(part for part, _style in runs)
    check("绿色 INFO 行：序列被吃掉", "\x1b" not in text, repr(text[:40]))
    check("绿色 INFO 行：文字保留", text.startswith("10-02 13:22:52 [INFO] 插件已加载"), repr(text[:40]))
    check("绿色 INFO 行：识别为绿色（32 → 索引 2）",
          runs[0][1].fg == AnsiColor.basic(2), str(runs[0][1].fg))
    check("行尾 ESC[0m 回到默认样式", runs[-1][1].is_plain(), str(runs[-1][1]))

    debug = AnsiParser().feed("\x1b[38;20m10-02 13:22:53 [DEBUG] 群相关事件\x1b[0m\n")
    check("残缺扩展色 38;20 不上色（与 Windows Terminal 一致）",
          all(style.fg is None for _part, style in debug), str(debug[0][1].fg))

    print("\n[2] 跨块 / 跨行（QProcess 会随机切块）")
    stream = AnsiParser()
    first = stream.feed("\x1b[3")
    second = stream.feed("1m红色\x1b[0m 普通")
    check("半截序列不产生文字", first == [], str(first))
    check("拼起来后文字正确", "".join(p for p, _s in second) == "红色 普通",
          repr("".join(p for p, _s in second)))
    check("第一段是红色", second[0][1].fg == AnsiColor.basic(1), str(second[0][1].fg))
    check("ESC[0m 之后恢复默认", second[-1][1].is_plain(), str(second[-1][1]))

    carry = AnsiParser()
    carry.feed("\x1b[33m警告：")
    later = carry.feed("下一块还是黄的\n")
    check("颜色跨块延续（终端行为）", later[-1][1].fg == AnsiColor.basic(3), str(later[-1][1].fg))

    print("\n[3] 256 色 / 真彩色 / 其它控制序列")
    check("38;5;208 → 256 色", AnsiParser().feed("\x1b[38;5;208mx")[0][1].fg
          == AnsiColor.basic(208))
    check("38;2;255;128;0 → 真彩色", AnsiParser().feed("\x1b[38;2;255;128;0mx")[0][1].fg
          == AnsiColor.truecolor(255, 128, 0))
    noisy = AnsiParser().feed("\x1b[2J\x1b[H\x1b[?25l\x1b]0;标题\x07正常\x1b[?25h")
    check("清屏/光标/私有模式/OSC 都被丢掉",
          "".join(p for p, _s in noisy) == "正常", repr("".join(p for p, _s in noisy)))
    check("strip_ansi 只留文字", strip_ansi("\x1b[31m红\x1b[0m字") == "红字")
    check("has_ansi 判定", has_ansi("\x1b[31m") and not has_ansi("普通日志"))

    print("\n[4] 畸形输入不炸、不吃内存")
    weird = AnsiParser()
    weird.feed("\x1b[" + "9" * 20000)
    check("超长未结束序列被丢弃", len(weird.pending) <= 4096, str(len(weird.pending)))
    check("孤立 ESC 不抛异常", isinstance(strip_ansi("a\x1bb"), str))


def run_color_checks() -> None:
    """把 theme.py 里**不依赖 PyQt6 的纯函数**抠出来执行，真验一遍颜色数学。

    为什么要这么绕：`theme.py` 顶部 import PyQt6，而这台机器上不一定装了它
    （本检查器要能在只有标准库的环境里跑）。所以用 AST 取源码 + 注入常量，
    与 `check_branch_qss.py` 是同一套办法。
    """
    theme_path = ROOT / "app" / "ui" / "theme.py"
    theme_src = theme_path.read_text(encoding="utf-8")
    theme_tree = ast.parse(theme_src)

    wanted = ("ansi_index_rgb", "color_luminance", "readable_rgb", "_hex_color",
              "ansi_palette", "log_colors")
    # 第一行必须是 future import：theme.py 顶部就有它，所以那些
    # `Optional[QWidget]` 注解在真模块里不求值；抠出来单独 exec 时若缺这句，
    # 注解会在 def 时求值 → NameError: name 'QWidget' is not defined
    # （`check_branch_qss.py` 就是踩了这个坑，这里一开始也踩了一次）。
    code = (
        "from __future__ import annotations\n"
        "import os\n"
        "from typing import Optional, Tuple\n"
        "is_dark = lambda widget=None: False\n"
    )
    for node in theme_tree.body:
        # 常量（ANSI 色板 / 阈值 / 色立方）用字面量取出来。
        # 注意：带类型注解的赋值是 AnnAssign，不是 Assign —— 两种都要管。
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value_node = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value_node = node.value
        else:
            name, value_node = "", None
        if name.startswith("ANSI_") or name.startswith("LOG_"):
            try:
                code += "{} = {!r}\n".format(name, ast.literal_eval(value_node))
            except ValueError:
                continue
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            code += "\n" + (ast.get_source_segment(theme_src, node) or "") + "\n"

    namespace: dict = {}
    exec(code, namespace)  # noqa: S102 - 只执行本项目自己的纯函数
    index_rgb = namespace["ansi_index_rgb"]
    luma = namespace["color_luminance"]
    readable = namespace["readable_rgb"]

    check("xterm 256 色：16 是纯黑", index_rgb(16) == (0, 0, 0), str(index_rgb(16)))
    check("xterm 256 色：231 是纯白", index_rgb(231) == (255, 255, 255), str(index_rgb(231)))
    check("xterm 256 色：232-255 是灰阶", index_rgb(232) == (8, 8, 8), str(index_rgb(232)))
    check("xterm 256 色：越界不抛异常", isinstance(index_rgb(-5), tuple) and isinstance(index_rgb(999), tuple))

    bright_white = (255, 255, 255)
    fixed = readable(bright_white, False)
    limit = namespace.get("ANSI_LIGHT_MAX_LUMA", 0.42)
    check("浅色底：亮白被压到与色板同一深度（近似亮度 ≤ {:.2f}）".format(limit),
          luma(fixed) <= limit, "{} → {} 亮度 {:.2f}".format(bright_white, fixed, luma(fixed)))
    dark_fix = readable((0, 0, 0), True)
    check("深色底：纯黑被提到看得见（亮度 ≥ 0.10）", luma(dark_fix) >= 0.10,
          "(0,0,0) → {} 亮度 {:.2f}".format(dark_fix, luma(dark_fix)))
    keep = (0x09, 0x53, 0x09)          # 浅色板的绿色：本来就够深，不该被改
    check("浅色底：正常的绿色原样保留", readable(keep, False) == keep,
          "{} → {}".format(keep, readable(keep, False)))

    light = namespace.get("ANSI_LIGHT_COLORS") or ()
    dark = namespace.get("ANSI_DARK_COLORS") or ()
    check("浅色板 16 色", len(light) == 16, str(len(light)))
    check("深色板 16 色", len(dark) == 16, str(len(dark)))
    check("浅色板全部是 #rrggbb",
          all(len(c) == 7 and c.startswith("#") for c in light), str(light[:3]))

    # 真机反馈："浅色模式下带颜色的字体颜色深些观感更好" ——
    # 把"够不够深"变成可计算的 WCAG 对比度，而不是靠眼睛。
    ratios = sorted(contrast_ratio(hex_to_rgb(c)) for c in light)
    worst = ratios[0]
    median = ratios[len(ratios) // 2]
    check("浅色板没有一个颜色低于 WCAG AA（4.5:1）", worst >= 4.5, "最低 {:.2f}".format(worst))
    check("浅色板对比度中位数 ≥ 6:1", median >= 6.0, "中位 {:.2f}".format(median))

    base_ratios = [contrast_ratio(hex_to_rgb(light[index])) for index in range(1, 7)]
    bright_ratios = [contrast_ratio(hex_to_rgb(light[index])) for index in range(9, 15)]
    check("基础色 1-6 全部达到 7:1（与正文同级）", min(base_ratios) >= 7.0,
          "最低 {:.2f}".format(min(base_ratios)))
    check("亮色 9-14 全部达到 6:1（够清楚）", min(bright_ratios) >= 6.0,
          "最低 {:.2f}".format(min(bright_ratios)))

    # 亮色必须比对应的基础色**亮一档**（否则"亮色"就失去意义了）
    lighter_ok = all(
        luma(hex_to_rgb(light[index + 8])) > luma(hex_to_rgb(light[index]))
        for index in range(1, 7)
    )
    check("浅色板 9-14 比 1-6 亮一档（保住终端明暗语义）", lighter_ok)


def hex_to_rgb(value: str):
    """`#rrggbb` → (r, g, b)。"""
    text = str(value or "").lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _srgb_channel(value: float) -> float:
    """sRGB 分量线性化（WCAG 用的那一步）。"""
    value = max(0.0, min(1.0, value / 255.0))
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb) -> float:
    """WCAG 相对亮度。"""
    red, green, blue = (_srgb_channel(v) for v in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(rgb, background=(0xF0, 0xEF, 0xE9)) -> float:
    """与日志区底色（浅色 `#f0efe9`）的 WCAG 对比度。"""
    first, second = relative_luminance(rgb), relative_luminance(background)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def check_log_colors_arity() -> None:
    """真跑一遍 `theme.log_colors()`，确认"返回几个值"和调用方的取法对得上。

    真机事故（2026-10-02）：`program_widget._format_for()` 写的是
    ``base_bg, base_fg = theme_tokens.log_colors(self)``，
    而这个函数返回**四个**值 `(底色, 文字色, 边框, 选中色)` →
    `ValueError` 抛在 Qt 信号槽里 → PyQt6 直接终止进程（用户看到"闪退"）。

    现在调用方写成 ``log_colors(self)[:2]``（只取前两个），这里把真实返回抠出来对一遍：
    长度必须 ≥ 2，且前两个是合法的 `#rrggbb`。
    """
    theme_path = ROOT / "app" / "ui" / "theme.py"
    theme_src = theme_path.read_text(encoding="utf-8")
    theme_tree = ast.parse(theme_src)

    code = (
        "from __future__ import annotations\n"
        "import os\n"
        "from typing import Optional, Tuple\n"
        "def is_dark(widget=None):\n"
        "    return bool(_FORCE_DARK[0])\n"
        "_FORCE_DARK = [False]\n"
    )
    for node in theme_tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value_node = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name, value_node = node.targets[0].id, node.value
        else:
            name, value_node = "", None
        if (name.startswith("ANSI_") or name.startswith("LOG_")) and value_node is not None:
            try:
                code += "{} = {!r}\n".format(name, ast.literal_eval(value_node))
            except ValueError:
                continue
        elif isinstance(node, ast.FunctionDef) and node.name == "log_colors":
            code += "\n" + (ast.get_source_segment(theme_src, node) or "") + "\n"

    namespace: dict = {}
    exec(code, namespace)  # noqa: S102 - 只执行本项目自己的纯函数
    log_colors = namespace["log_colors"]

    for dark in (False, True):
        namespace["_FORCE_DARK"][0] = dark
        try:
            colors = log_colors(None)
            detail = str(colors)
            ok = len(colors) >= 2
            hexes_ok = all(
                isinstance(item, str) and len(item) == 7 and item.startswith("#")
                for item in colors[:2]
            )
        except Exception as exc:  # noqa: BLE001 - 诊断脚本：异常也要报出来
            ok, hexes_ok, detail = False, False, "{}: {}".format(type(exc).__name__, exc)
        label = "{}主题".format("深色" if dark else "浅色")
        check("log_colors() 至少返回 (底色, 文字色) 两个值（{}）".format(label), ok, detail)
        check("前两个值是合法十六进制颜色（{}）".format(label), hexes_ok, detail)


def main() -> int:
    source = PROGRAM_WIDGET.read_text(encoding="utf-8")
    tree = ast.parse(source)
    tab_source = BOT_TAB.read_text(encoding="utf-8")
    tab_tree = ast.parse(tab_source)
    ansi_source = ANSI_MODULE.read_text(encoding="utf-8")

    run_parser_checks()

    print("\n[5] 界面层确实接上了（AST）")
    append_log = find_function(tree, "append_log")
    check("找得到 ProgramWidget.append_log", append_log is not None)
    if append_log is not None:
        names = calls_in(append_log)
        check("append_log 会按 ANSI 上色（_write_runs）", "_write_runs" in names, str(sorted(names)))
        check("append_log 会留原文（_remember_raw）", "_remember_raw" in names, str(sorted(names)))

    write_runs = find_function(tree, "_write_runs")
    check("找得到 _write_runs", write_runs is not None)
    if write_runs is not None:
        names = calls_in(write_runs)
        check("_write_runs 调用解析器 feed()", "feed" in names, str(sorted(names)))
        check("_write_runs 通过 _insert 落笔（每段都带格式）", "_insert" in names, str(sorted(names)))

    insert = find_function(tree, "_insert")
    check("找得到 _insert", insert is not None)
    if insert is not None:
        names = calls_in(insert)
        check("_insert 用 _format_for 造格式", "_format_for" in names, str(sorted(names)))
        check("_insert 有兜底纯文本路径（_plain_format）", "_plain_format" in names,
              str(sorted(names)))

    format_for = find_function(tree, "_format_for")
    check("找得到 _format_for", format_for is not None)
    if format_for is not None:
        names = calls_in(format_for)
        check("颜色来自 theme.ansi_color()", "ansi_color" in names, str(sorted(names)))
        check("底色/文字色来自 theme.log_colors()", "log_colors" in names, str(sorted(names)))

    clear_log = find_function(tree, "clear_log")
    if clear_log is not None:
        names = calls_in(clear_log)
        check("clear_log 重置解析器（reset）", "reset" in names, str(sorted(names)))

    apply_theme = find_function(tree, "apply_theme")
    if apply_theme is not None:
        names = calls_in(apply_theme)
        check("apply_theme 会按新主题重画（_rerender_if_colored）",
              "_rerender_if_colored" in names, str(sorted(names)))

    capture = find_function(tab_tree, "_capture_log_texts")
    if capture is not None:
        check("布局快照抓的是 raw_text()（颜色不丢）", "raw_text" in calls_in(capture))
    restore = find_function(tab_tree, "_restore_log_texts")
    if restore is not None:
        check("布局还原走 load_raw_text()", "load_raw_text" in calls_in(restore))

    print("\n[6] 颜色只能有一个出处（theme）")
    hexes = [
        line.strip() for line in source.splitlines()
        if "#" in line and any(
            token in line.lower() for token in ("#0", "#1", "#2", "#3", "#4", "#5",
                                                "#6", "#7", "#8", "#9", "#a", "#b",
                                                "#c", "#d", "#e", "#f")
        ) and line.strip().startswith(("fmt.set", "color", "background", "foreground"))
    ]
    check("program_widget 里没有硬编码颜色", not hexes, str(hexes))

    print("\n[7] 色板与可读性（把 theme 里的纯函数抠出来真跑一遍）")
    run_color_checks()

    print("\n[8] `theme.log_colors()` 的返回长度（2026-10-02 闪退的那一条）")
    check_log_colors_arity()

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
