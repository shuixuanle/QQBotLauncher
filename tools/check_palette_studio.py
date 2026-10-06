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
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
from _theme_probe import load_theme_namespace  # noqa: E402
_NS = load_theme_namespace(functions=("contrast_ratio",))
contrast_ratio = _NS["contrast_ratio"]
DARK_BORDER_VALUE = _NS["DARK_BORDER"]
LIGHT_BORDER_VALUE = _NS["LIGHT_BORDER"]
sys.path.insert(0, str(ROOT / "tools"))

from _theme_probe import load_theme_namespace                 # noqa: E402

THEME = ROOT / "app" / "ui" / "theme.py"
MAIN = ROOT / "main.py"
STUDIO = ROOT / "tools" / "palette_studio.py"
DIALOG = ROOT / "app" / "ui" / "palette_dialog.py"

#: 这些函数要抠出来真跑（依赖会被自动带上）
NEEDED_FUNCTIONS = (
    "parse_palette_text", "is_hex_color", "normalize_hex",
    "relative_luminance", "contrast_ratio",
    "set_custom_colors", "ansi_palette", "ansi_palette_for", "log_colors_for",
    "custom_colors", "role_color", "resolve_roles", "builtin_role_color",
    "compose_role_colors", "diff_role_colors", "color_snapshot",
    "encode_history_entry", "decode_history_entry",
)

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

def build_theme_namespace(report: dict = None):
    """抠出 theme.py 里不依赖 Qt 的部分（公共工具，见 tools/_theme_probe.py）。

    以前这里手写"要哪几个常量、要哪几个函数"，结果每加一个角色表就 NameError 一次
    （`UI_ROLES`、`ROLE_TABLES`…）—— 现在交给公共工具自动收依赖。
    `report["skipped"]` 会列出"因为缺依赖而没抠出来"的常量：**必须为空**，
    否则说明真模块里存在"先用后定义"（真机就是 main.py 一启动就 NameError）。
    """
    return load_theme_namespace(NEEDED_FUNCTIONS, report=report)


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
    report: dict = {}
    namespace = build_theme_namespace(report)
    print("[0] theme.py 的常量都能按真实顺序抠出来")
    check("没有「先用后定义」的常量（skipped 为空）", not report.get("skipped"),
          "抠不出来 = {}".format(report.get("skipped")))
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

    print("\n[3c] 界面角色（整个界面可自定义的基础）")
    role_color = namespace["role_color"]
    resolve_roles = namespace["resolve_roles"]
    ui_roles = namespace["UI_ROLES"]
    labels = namespace["ROLE_LABELS"]
    set_custom = namespace["set_custom_colors"]

    check("角色数量 ≥ 15（窗体/列表/文字/按钮/边框/选中/日志…）", len(ui_roles) >= 15,
          str(len(ui_roles)))
    check("每个角色都有中文名（工作台要用）",
          all(role in labels for role in ui_roles),
          str([role for role in ui_roles if role not in labels]))
    for dark_mode in (False, True):
        resolved = resolve_roles(dark=dark_mode)
        bad = [role for role, color in resolved.items()
               if not (len(color) == 7 and color.startswith("#"))]
        check("{}内置角色全是 #rrggbb".format("深色" if dark_mode else "浅色"), not bad, str(bad))
    check("浅色列表底色 = 淡灰 #f4f4f4（真机要求）",
          role_color("base", dark=False) == "#f4f4f4", role_color("base", dark=False))
    check("深色列表底色不受影响", role_color("base", dark=True) == "#1e1f22",
          role_color("base", dark=True))

    # 逐个角色都能被覆盖（这就是"整个界面可自定义"的保证）
    overridden = {}
    for index, role in enumerate(ui_roles):
        value = "#{:02x}{:02x}{:02x}".format(10 + index, 20 + index, 30 + index)
        set_custom(**{"light_" + role: value})
        overridden[role] = value
    wrong = [role for role in ui_roles if role_color(role, dark=False) != overridden[role]]
    check("15 个角色都能被自定义覆盖", not wrong, str(wrong))
    wrong_dark = [role for role in ui_roles
                  if role_color(role, dark=True) != namespace["DARK_ROLES"][role]]
    check("只改浅色时深色不受影响", not wrong_dark, str(wrong_dark))
    set_custom(**{"light_" + role: "" for role in ui_roles})
    check("显式清空后全部回到内置",
          all(role_color(role, dark=False) == namespace["LIGHT_ROLES"][role]
              for role in ui_roles))

    print("\n[3d] 配色记录（历史）：只存差异、坏数据不许炸")
    compose = namespace["compose_role_colors"]
    diff = namespace["diff_role_colors"]
    encode = namespace["encode_history_entry"]
    decode = namespace["decode_history_entry"]

    composed = compose("light", {"base": "#123456", "不存在": "#ffffff"})
    check("compose：覆盖生效、未知角色忽略",
          composed["base"] == "#123456" and len(composed) == len(ui_roles))
    check("diff：只留与内置不同的",
          diff("light", {"base": "#123456", "window": namespace["LIGHT_ROLES"]["window"]})
          == {"base": "#123456"})
    check("diff：非法颜色被丢掉", diff("light", {"base": "乱码"}) == {})

    entry = {"time": "2026-10-02 16:00:00", "mode": "light",
             "note": "浅色模式：改 1 项", "roles": {"base": "#123456"},
             "ansi": []}
    text = encode(entry)
    back = decode(text)
    check("encode → decode 往返一致",
          back is not None and back["roles"] == {"base": "#123456"}
          and back["mode"] == "light" and back["note"] == entry["note"], str(back))
    check("decode 坏数据返回 None（不炸）",
          decode("这不是 JSON") is None and decode("{}") is not None)
    check("decode 会过滤非法角色与颜色",
          decode('{"mode":"dark","roles":{"base":"乱码","bad":"#112233"}}')["roles"] == {})
    check("ansi 数量不对就丢弃（不够 16 个不算一套）",
          decode('{"mode":"light","ansi":["#112233"]}')["ansi"] == [])
    check("ansi 正好 16 个才保留",
          len(decode('{"mode":"light","ansi":[%s]}'
                     % ",".join('"#112233"' for _ in range(16)))["ansi"]) == 16)

    snapshot = namespace["color_snapshot"]("light", note="测试")
    check("snapshot 带时间 / 模式 / 备注",
          bool(snapshot["time"]) and snapshot["mode"] == "light" and snapshot["note"] == "测试")
    check("snapshot 的 roles 是差异（默认应当为空）", snapshot["roles"] == {},
          str(snapshot["roles"]))

    print("\n[3e] 内置角色的可读性（文字类不能糊成一团）")
    light_roles = namespace["LIGHT_ROLES"]
    dark_roles = namespace["DARK_ROLES"]
    for name, table in (("浅色", light_roles), ("深色", dark_roles)):
        main_ratio = contrast(table["text"], table["window"])
        log_ratio = contrast(table["fg"], table["bg"])
        muted_ratio = contrast(table["muted"], table["window"])
        sel_ratio = contrast(table["selection_text"], table["selection_bg"])
        check("{}主文字 / 窗体 ≥ 7:1".format(name), main_ratio >= 7.0, "{:.2f}".format(main_ratio))
        check("{}日志文字 / 日志底 ≥ 7:1".format(name), log_ratio >= 7.0, "{:.2f}".format(log_ratio))
        check("{}次要文字 / 窗体 ≥ 3:1".format(name), muted_ratio >= 3.0, "{:.2f}".format(muted_ratio))
        check("{}选中行文字 / 选中底色 ≥ 4.5:1".format(name), sel_ratio >= 4.5,
              "{:.2f}".format(sel_ratio))


# ---------------------------------------------------------------------------
# [3][4][5][6] 接线（AST）
# ---------------------------------------------------------------------------

def run_wiring_checks() -> None:
    theme_source, theme_tree = get_source(THEME)
    main_source, main_tree = get_source(MAIN)
    studio_source, studio_tree = get_source(STUDIO)

    theme_src = (ROOT / "app" / "ui" / "theme.py").read_text(encoding="utf-8")
    print("\n[3f] 分割线：深浅两套都要看得见（真机 2026-10-05：深色下看不见）")
    check("  QToolBar::separator 用主题色画（不是 Qt 默认的近黑色）",
          "QToolBar::separator" in theme_src)
    check("  QMainWindow::separator 也画了", "QMainWindow::separator" in theme_src)
    check("  QMenu::separator 也画了", "QMenu::separator" in theme_src)
    check("  QSplitter::handle（窗格分割条）也画了", "QSplitter::handle" in theme_src)
    #  真机 2026-10-06："各程序日志区域的边框还是会与分隔线融合，我希望不要融合"
    #  —— 所以分割条只画中间 1px 细线，其余透明（两侧留出空隙）
    check("  分割条透明且无边框（窗格自带边框已足够区分区域）",
          "QSplitter::handle {{ background-color: transparent; border: 0; }}"
          in theme_src)
    check("  没给分割条画任何线（整条或细线都不行）",
          "QSplitter::handle {{ background-color: {border}; }}" not in theme_src
          and "QSplitter::handle:horizontal" not in theme_src)
    check("  深色调色板的 Dark / Shadow 不是近黑色（改用边框色）",
          "QPalette.ColorRole.Dark, border" in theme_src
          and "QPalette.ColorRole.Shadow, border" in theme_src)
    ratio_dark = contrast_ratio("#2b2b2b", DARK_BORDER_VALUE)
    ratio_light = contrast_ratio("#f0efe9", LIGHT_BORDER_VALUE)
    check("  深色 分割线/背景 对比度 >= 1.4（实际 {:.2f}）".format(ratio_dark),
          ratio_dark >= 1.4)
    check("  浅色 分割线/背景 对比度 >= 1.4（实际 {:.2f}）".format(ratio_light),
          ratio_light >= 1.4)

    print("\n[4] theme.py：自定义颜色被真正使用（整个界面，不只是日志区）")
    ansi_palette = find_function(theme_tree, "ansi_palette")
    check("ansi_palette 会查自定义（引用 _CUSTOM）",
          ansi_palette is not None and "_CUSTOM" in names_in(ansi_palette))
    for name in ("nav_palette", "chrome_palette", "log_colors_for", "muted_text_color",
                 "focus_border_color", "pane_qss", "_build_light_palette",
                 "_build_dark_palette"):
        node = find_function(theme_tree, name)
        calls = calls_in(node) if node is not None else set()
        check("{} 走颜色角色（role_color）".format(name), "role_color" in calls,
              str(sorted(calls))[:80])
    nav = find_function(theme_tree, "nav_palette")
    check("nav_palette 不再直接读系统调色板（否则淡灰不生效）",
          nav is not None and "palette_color" not in calls_in(nav))
    check("nav_palette 的底色用 base 角色",
          nav is not None and "base" in {c.value for c in ast.walk(nav)
                                         if isinstance(c, ast.Constant)})
    for name in ("load_custom_colors", "save_custom_colors", "clear_custom_colors",
                 "set_custom_colors", "custom_colors", "settings_for_colors",
                 "role_color", "resolve_roles", "custom_color_keys"):
        check("theme.py 有 {}".format(name), find_function(theme_tree, name) is not None)
    check("自定义颜色的设置键收敛成一个前缀（colors/…）",
          "SETTINGS_CUSTOM_PREFIX" in names_in(find_function(theme_tree, "color_setting_key")))

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
    check("命令行入口复用应用内对话框（PaletteDialog）",
          any("palette_dialog" in item for item in imported),
          str(sorted(item for item in imported if "palette" in item)))
    check("命令行入口不再自己写界面（不出现 Swatch / QGridLayout）",
          "class Swatch" not in studio_source and "QGridLayout" not in studio_source)
    check("工作台没有自己实现上色（不出现 QTextCharFormat/setForeground）",
          "QTextCharFormat" not in studio_source and "setForeground" not in studio_source)
    check("工作台没有抄一份内置色板",
          builtin_literals(studio_source, "ANSI_LIGHT_COLORS") is None
          and builtin_literals(studio_source, "ANSI_DARK_COLORS") is None)
    check("工作台会读回已保存的自定义（load_custom_colors）",
          "load_custom_colors" in calls_in(studio_tree))

    print("\n[7] 应用内对话框：菜单入口 + 立即生效 + 配色记录")
    dialog_source, dialog_tree = get_source(DIALOG)
    check("palette_dialog.py 有 PaletteDialog", find_function(dialog_tree, "__init__") is not None
          and any(isinstance(node, ast.ClassDef) and node.name == "PaletteDialog"
                  for node in ast.walk(dialog_tree)))
    check("对话框发出 colorsChanged（供立即生效）",
          any(isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "colorsChanged"
                      for t in node.targets)
              for node in ast.walk(dialog_tree)))
    refresh = find_function(dialog_tree, "_refresh_all")
    check("_refresh_all 会发 colorsChanged",
          refresh is not None and "emit" in calls_in(refresh))
    check("对话框复用真控件与真色板",
          any("ProgramWidget" in item for item in {
              "{}.{}".format(node.module, alias.name)
              for node in ast.walk(dialog_tree)
              if isinstance(node, ast.ImportFrom) and node.module
              for alias in node.names}))
    for name in ("load_color_history", "push_color_history", "apply_history_entry",
                 "clear_color_history"):
        check("对话框接了 {}".format(name),
              any(name in calls_in(node) for node in ast.walk(dialog_tree)
                  if isinstance(node, ast.FunctionDef)))

    window_tree = ast.parse((ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8"))
    builder = find_function(window_tree, "_build_theme_actions")
    menu_texts = {node.value for node in ast.walk(builder or ast.Module(body=[], type_ignores=[]))
                  if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    check("视图 → 外观 里加了「配色工作台…」",
          any("配色工作台" in text for text in menu_texts), str(sorted(menu_texts))[:80])
    check("菜单动作接到 open_palette_studio",
          builder is not None and "open_palette_studio" in names_in(builder))
    opener = find_function(window_tree, "open_palette_studio")
    check("open_palette_studio 延迟导入对话框",
          opener is not None and "PaletteDialog" in names_in(opener))
    check("open_palette_studio 把 colorsChanged 接上",
          opener is not None and "colorsChanged" in names_in(opener))
    live = find_function(window_tree, "_on_palette_colors_changed")
    check("改了以后立刻 _apply_theme()（不用重启）",
          live is not None and "_apply_theme" in calls_in(live))

    print("\n[8] 全局：不许出现**对象级**的通配 disconnect()（Qt 会刷 wildcard 告警）")
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


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    run_parse_checks()
    run_wiring_checks()
    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
