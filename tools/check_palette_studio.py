# -*- coding: utf-8 -*-
"""静态检查：日志配色工作台 + 自定义色板这条链路是通的（不需要 PyQt6）。

背景（真机需求）
----------------
"浅色模式下带颜色的字够不够清楚"是主观的，靠改代码猜很慢；底色和颜色又相互影响
（`#f0efe9` 偏暖，蓝/青天然显脏）。于是加了 `tools/palette_studio.py` 工作台：
真控件上试色 → 满意了存成自定义色板 → 管理器启动时载入。

这条链路最容易出的问题是"**某一环没接上**"，而且全都不报错：
  · 工作台自己写了一套上色逻辑 → 预览和真机不一样（等于白试）；
  · 存进了设置但启动时没载入 → 重启后没变化；
  · 自定义色板读取时没校验 → 一个坏值把界面颜色搞乱。

所以这里逐环断言：
  [1] 解析函数（纯函数，抠出来真跑）：合法/非法输入、大小写、JSON 与逗号两种写法；
  [2] WCAG 对比度公式（和本检查器自己的独立实现交叉验证）；
  [3] theme.py 的接线：ansi_palette / log_colors_for 看自定义、load/save/clear 都在；
  [4] main.py 启动时确实调了 load_custom_colors；
  [5] 工作台用的是**真控件真色板**（不许自带一套上色/色板），且保存/清除按钮接的是 theme 的函数；
  [6] 全局不许出现无参数的 `disconnect()`（Qt 会刷 "wildcard call" 告警）。

用法：
    python tools\\check_palette_studio.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

THEME = ROOT / "app" / "ui" / "theme.py"
MAIN = ROOT / "main.py"
STUDIO = ROOT / "tools" / "palette_studio.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def get_source(path: Path):
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source)


def find_function(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def calls_in(node: ast.AST) -> set:
    """收集一段代码里出现过的调用名（`self._x()` → `_x`，`f()` → `f`）。"""
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def names_in(node: ast.AST) -> set:
    """收集一段代码里引用过的所有名字（含变量、属性基名、函数名）。"""
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def builtin_literals(source: str, name: str):
    """把 theme.py 里某个常量元组取出来（判断工作台有没有自己抄一份）。"""
    tree = ast.parse(source)
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value_node = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target, value_node = node.targets[0].id, node.value
        else:
            continue
        if target == name and value_node is not None:
            try:
                return ast.literal_eval(value_node)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# [1][2] 纯函数：抠出来真跑
# ---------------------------------------------------------------------------

def build_theme_namespace():
    """把 theme.py 里不依赖 Qt 的部分抠出来执行（与 check_branch_qss.py 同一套办法）。

    除了几个纯函数，还要注入 `is_dark`（否则 `ansi_palette` / `log_colors_for`
    没法判断深浅）与 `_CUSTOM`（自定义色板表）。
    """
    source, tree = get_source(THEME)
    wanted = ("parse_palette_text", "is_hex_color", "normalize_hex",
              "relative_luminance", "contrast_ratio",
              "set_custom_colors", "ansi_palette", "log_colors_for", "custom_colors")
    code = (
        "from __future__ import annotations\n"
        "import json\n"
        "import re\n"
        "from typing import Dict, Optional, Tuple\n"
        "_FORCE_DARK = [False]\n"
        "def is_dark(widget=None):\n"
        "    return bool(_FORCE_DARK[0])\n"
        "_CUSTOM = {}\n"
    )
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value_node = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name, value_node = node.targets[0].id, node.value
        else:
            name, value_node = "", None
        if name.startswith(("ANSI_", "LOG_")) and value_node is not None:
            try:
                code += "{} = {!r}\n".format(name, ast.literal_eval(value_node))
            except ValueError:
                continue
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            code += "\n" + (ast.get_source_segment(source, node) or "") + "\n"
    namespace: dict = {}
    exec(code, namespace)  # noqa: S102 - 只执行本项目自己的纯函数
    return namespace


def own_contrast(color_a: str, color_b: str) -> float:
    """本检查器**自己的** WCAG 对比度实现（用来交叉验证 theme 里那份）。"""
    def luminance(color: str) -> float:
        text = color.lstrip("#")
        channels = []
        for index in (0, 2, 4):
            value = int(text[index:index + 2], 16) / 255.0
            channels.append(value / 12.92 if value <= 0.04045
                            else ((value + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    first, second = luminance(color_a), luminance(color_b)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def run_parse_checks() -> None:
    namespace = build_theme_namespace()
    parse = namespace["parse_palette_text"]
    is_hex = namespace["is_hex_color"]
    normalize = namespace["normalize_hex"]
    contrast = namespace["contrast_ratio"]
    light = namespace.get("ANSI_LIGHT_COLORS") or ()
    dark = namespace.get("ANSI_DARK_COLORS") or ()

    print("[1] 色板文本解析（工作台与设置里存的就是它）")
    joined = ",".join(light)
    check("逗号分隔 16 色 → 解析成功", parse(joined) == tuple(light))
    check("JSON 数组 → 解析成功",
          parse("[{}]".format(", ".join('"{}"'.format(c) for c in light))) == tuple(light))
    check("大写 / 省略 # 也认", parse(",".join(c.upper().lstrip("#") for c in light))
          == tuple(light))
    check("QSettings 存成 list 也认", parse(list(light)) == tuple(light))
    check("空字符串 → None（表示没设置）", parse("") is None)
    check("数量不对（15 个）→ None", parse(",".join(light[:15])) is None)
    check("数量不对（17 个）→ None", parse(",".join(light) + ",#000000") is None)
    check("非法颜色 → None", parse(",".join(light[:15] + ("#gggggg",))) is None)
    check("乱码 → None", parse("这不是颜色") is None)
    check("二进制垃圾不抛异常", parse(b"\x00\x01") is None)
    check("is_hex_color / normalize_hex 正常",
          is_hex("#AABBCC") and is_hex("aabbcc") and not is_hex("#abc")
          and normalize("#AABBCC") == "#aabbcc")

    print("\n[2] WCAG 对比度（与检查器自己的实现交叉验证）")
    for first, second in (("#ffffff", "#000000"), ("#095309", "#f0efe9"),
                          ("#13a10e", "#1e1f22"), ("#606060", "#f0efe9")):
        mine = own_contrast(first, second)
        theirs = contrast(first, second)
        check("{} on {} = {:.2f}".format(first, second, theirs),
              abs(mine - theirs) < 0.005, "自身 {:.3f} / theme {:.3f}".format(mine, theirs))
    check("纯白对纯黑 = 21.0（公式没错）", abs(contrast("#ffffff", "#000000") - 21.0) < 0.01)
    check("同色对比度 = 1.0", abs(contrast("#123456", "#123456") - 1.0) < 1e-9)
    check("坏颜色的对比度不炸（返回 1.0 或 0.0 之类）",
          isinstance(contrast("乱码", "#ffffff"), float))

    print("\n[3] 深浅两套内置色板仍然可解析（防止搬进设置时被写坏）")
    check("浅色板 16 色且解析回自己", len(light) == 16 and parse(joined) == tuple(light))
    check("深色板 16 色且解析回自己",
          len(dark) == 16 and parse(",".join(dark)) == tuple(dark))

    print("\n[3b] 自定义色板真跑一遍（set → 生效 → 清空 → 回到内置）")
    set_custom = namespace["set_custom_colors"]
    ansi_palette = namespace["ansi_palette"]
    log_colors_for = namespace["log_colors_for"]
    force_dark = namespace["_FORCE_DARK"]
    light_bg_builtin = namespace.get("LOG_LIGHT_BG", "#f0efe9")

    custom = tuple("#{:02x}{:02x}{:02x}".format(16 * i + 1, 32, 240 - 8 * i) for i in range(16))
    force_dark[0] = False
    set_custom(light_ansi=custom)
    check("设了浅色自定义色板 → ansi_palette 用自定义的",
          ansi_palette(None) == custom, str(ansi_palette(None)[:2]))
    force_dark[0] = True
    check("深色那套没设 → 仍是内置色板", ansi_palette(None) == tuple(dark))
    set_custom(dark_ansi=custom)
    check("深色也设上 → 两套都是自定义", ansi_palette(None) == custom)

    force_dark[0] = False
    set_custom(light_bg="#ffffff", light_fg="#000000")
    background, foreground = log_colors_for(False)[:2]
    check("日志底色 / 文字色可覆盖", (background, foreground) == ("#ffffff", "#000000"),
          "{} / {}".format(background, foreground))
    check("仍是四元组（边框与选中色没被动）", len(log_colors_for(False)) == 4)

    set_custom(light_bg="乱码不是颜色")
    check("非法颜色被忽略（不会把界面弄坏）", log_colors_for(False)[0] == "#ffffff",
          log_colors_for(False)[0])

    set_custom(light_ansi=(), light_bg="", light_fg="")
    check("显式清空 → 回到内置色板", ansi_palette(None) == tuple(light))
    check("显式清空 → 回到内置底色", log_colors_for(False)[0] == light_bg_builtin,
          log_colors_for(False)[0])
    check("只清浅色时，深色的自定义还在", "dark_ansi" in namespace["custom_colors"]())
    set_custom(dark_ansi=(), dark_bg="", dark_fg="")
    check("两套都清空后 custom_colors() 为空", not namespace["custom_colors"]())


# ---------------------------------------------------------------------------
# [3][4][5][6] 接线（AST）
# ---------------------------------------------------------------------------

def run_wiring_checks() -> None:
    theme_source, theme_tree = get_source(THEME)
    main_source, main_tree = get_source(MAIN)
    studio_source, studio_tree = get_source(STUDIO)

    print("\n[4] theme.py：自定义色板被真正使用")
    ansi_palette = find_function(theme_tree, "ansi_palette")
    check("ansi_palette 会查自定义（引用 _CUSTOM）",
          ansi_palette is not None and "_CUSTOM" in names_in(ansi_palette))
    log_colors_for = find_function(theme_tree, "log_colors_for")
    check("log_colors_for 会查自定义（引用 _CUSTOM）",
          log_colors_for is not None and "_CUSTOM" in names_in(log_colors_for))
    log_colors = find_function(theme_tree, "log_colors")
    check("log_colors 转发给 log_colors_for",
          log_colors is not None and "log_colors_for" in calls_in(log_colors))
    apply_palette = find_function(theme_tree, "apply_log_palette")
    check("apply_log_palette 也走 log_colors_for（底色覆盖才生效）",
          apply_palette is not None and "log_colors_for" in calls_in(apply_palette))
    for name in ("load_custom_colors", "save_custom_colors", "clear_custom_colors",
                 "set_custom_colors", "custom_colors", "settings_for_colors"):
        check("theme.py 有 {}".format(name), find_function(theme_tree, name) is not None)

    print("\n[5] main.py：启动时载入自定义色板")
    setup_theme = find_function(main_tree, "setup_theme")
    check("setup_theme 调了 load_custom_colors",
          setup_theme is not None and "load_custom_colors" in calls_in(setup_theme))

    print("\n[6] 工作台：用真控件真色板，不自带一套")
    imported = set()
    for node in ast.walk(studio_tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                imported.add("{}.{}".format(node.module, alias.name))
    check("工作台导入真控件 ProgramWidget",
          any("program_widget" in item and "ProgramWidget" in item for item in imported),
          str(sorted(item for item in imported if "program_widget" in item)))
    check("工作台导入 theme（色板唯一出处）",
          any(item.endswith("theme") or item == "app.ui.theme" for item in imported))
    check("工作台没有自己实现上色（不出现 QTextCharFormat/setForeground）",
          "QTextCharFormat" not in studio_source and "setForeground" not in studio_source)
    check("工作台没有抄一份内置色板",
          builtin_literals(studio_source, "ANSI_LIGHT_COLORS") is None
          and builtin_literals(studio_source, "ANSI_DARK_COLORS") is None)
    refresh = find_function(studio_tree, "_refresh_all")
    check("工作台刷新走 load_raw_text（真渲染路径）",
          refresh is not None and "load_raw_text" in calls_in(refresh))
    save = find_function(studio_tree, "_on_save")
    check("保存按钮接到 save_custom_colors",
          save is not None and "save_custom_colors" in calls_in(save))
    clear = find_function(studio_tree, "_on_clear")
    check("清除按钮接到 clear_custom_colors",
          clear is not None and "clear_custom_colors" in calls_in(clear))
    check("工作台会读回已保存的自定义（load_custom_colors）",
          "load_custom_colors" in calls_in(studio_tree))

    print("\n[7] 全局：不许出现**对象级**的通配 disconnect()（Qt 会刷 wildcard 告警）")
    offenders = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "disconnect"
                    and not node.args):
                continue
            # `<对象>.<信号>.disconnect()` 是正常写法（只清那一条信号）；
            # `<对象>.disconnect()`（通配、无参数）才是要拦的：
            # 它要遍历该对象所有连接，其中一条的发送者可能已销毁，于是刷
            # `QObject::disconnect: wildcard call disconnects from destroyed signal`。
            if isinstance(func.value, ast.Attribute):
                continue
            offenders.append("{}:{}".format(path.relative_to(ROOT), node.lineno))
    check("app/ 下没有对象级通配 disconnect()", not offenders, str(offenders))


def main() -> int:
    run_parse_checks()
    run_wiring_checks()
    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
