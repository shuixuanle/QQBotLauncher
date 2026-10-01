# -*- coding: utf-8 -*-
"""静态检查：日志区的两个真机问题修复是否还在。

对应真机反馈：
  1. **深色模式下日志文字是黑的** —— `apply_log_palette` 必须把颜色**同时**写进
     样式表与调色板。只写调色板时，Qt 可能因为该控件已被 setStyleSheet 过而
     优先用样式表的默认色（我们此前只写了 border），文字就回退成系统默认黑。
  2. **切换日志区布局时日志内容消失** —— `_rebuild_panes` 会 `self._views = {}`
     并 deleteLater() 旧控件，日志文本随控件一起没了。必须在重建前抓快照
     （`_capture_log_texts`）、建完新窗格后灌回（`_restore_log_texts`）。

用法：
    python tools\\check_log_pane.py
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "app" / "ui" / "theme.py"
BOT_TAB = ROOT / "app" / "ui" / "bot_tab.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def find_func(tree, name, cls=None):
    if cls is not None:
        node = next(
            (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls), None
        )
        if node is None:
            return None
        return next(
            (c for c in node.body if isinstance(c, ast.FunctionDef) and c.name == name),
            None,
        )
    return next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None
    )


def main() -> int:
    theme_src = THEME.read_text(encoding="utf-8")
    theme_tree = ast.parse(theme_src)
    tab_src = BOT_TAB.read_text(encoding="utf-8")
    tab_tree = ast.parse(tab_src)

    print("[1] 日志区配色（深色下不能是黑字）")
    palette_fn = find_func(theme_tree, "apply_log_palette")
    check("存在 apply_log_palette", palette_fn is not None)
    pal_src = ast.get_source_segment(theme_src, palette_fn) or "" if palette_fn else ""
    check("  样式表里写了 color（不只是 border）",
          "color: {fg}" in pal_src or "color: " in pal_src)
    check("  样式表里写了 background-color", "background-color: {bg}" in pal_src)
    check("  样式表里写了选中色", "selection-background-color" in pal_src)
    check("  仍然设置控件调色板（两条路都指向同一颜色）",
          "setPalette" in pal_src)
    check("  同时设置 viewport 的调色板", "viewport.setPalette" in pal_src)
    check("  用 #programLog 选择器（与 objectName 对应）",
          "QPlainTextEdit#programLog" in pal_src)

    print("\n[2] 深/浅两套日志色常量对比度")
    ns = {}
    for node in theme_tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.startswith("LOG_") or name in ("HIGHLIGHTED_TEXT",):
                try:
                    ns[name] = ast.literal_eval(node.value)
                except ValueError:
                    pass

    def lightness(hex_color: str) -> float:
        text = hex_color.lstrip("#")
        r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
        return 0.299 * r + 0.587 * g + 0.114 * b

    for prefix, want_light_text in (("LOG_DARK", True), ("LOG_LIGHT", False)):
        bg = ns.get("{}_BG".format(prefix))
        fg = ns.get("{}_TEXT".format(prefix))
        if not bg or not fg:
            check("  {} 常量齐全".format(prefix), False,
                  "bg={} fg={}".format(bg, fg))
            continue
        delta = abs(lightness(bg) - lightness(fg))
        brighter = lightness(fg) > lightness(bg)
        check("  {} 文字与底色亮度差 >= 60".format(prefix), delta >= 60,
              "{} vs {} -> {:.0f}".format(fg, bg, delta))
        check("  {} 文字比底色{}".format(prefix, "亮" if want_light_text else "暗"),
              brighter == want_light_text,
              "字 {:.0f} / 底 {:.0f}".format(lightness(fg), lightness(bg)))

    print("\n[3] 切换布局保留日志")
    capture = find_func(tab_tree, "_capture_log_texts", cls="BotTab")
    restore = find_func(tab_tree, "_restore_log_texts", cls="BotTab")
    check("存在 _capture_log_texts", capture is not None)
    check("存在 _restore_log_texts", restore is not None)
    cap_src = ast.get_source_segment(tab_src, capture) or "" if capture else ""
    res_src = ast.get_source_segment(tab_src, restore) or "" if restore else ""
    check("  快照读的是 editor.toPlainText()", "toPlainText()" in cap_src)
    check("  快照按 manager_key 存", "_views" in cap_src)
    check("  恢复用 setPlainText（不是逐行 append）", "setPlainText" in res_src)
    check("  恢复时恢复行数计数", "_line_count" in res_src)
    check("  恢复后滚到底部", "scroll_to_bottom" in res_src)

    rebuild = find_func(tab_tree, "_rebuild_panes", cls="BotTab")
    check("存在 _rebuild_panes", rebuild is not None)
    reb_src = ast.get_source_segment(tab_src, rebuild) or "" if rebuild else ""
    check("  重建里调用了 _capture_log_texts", "_capture_log_texts()" in reb_src)
    check("  重建里调用了 _restore_log_texts", "_restore_log_texts(" in reb_src)
    # 注意：_capture_log_texts 内部也出现 self._views（遍历它取文本），
    # 所以要匹配"赋值为空字典"这一句本身，并取**重建函数里最后一次**出现的位置
    # —— 那句才是"丢日志"的元凶。
    clear_marker = "self._views = {}"
    if "_capture_log_texts()" in reb_src and clear_marker in reb_src:
        check("  抓快照在清空 _views 之前",
              reb_src.index("_capture_log_texts()") < reb_src.rindex(clear_marker))
    else:
        check("  顺序可判定", False, "缺 capture 或 _views 清空")
    if "_restore_log_texts(" in reb_src and "_create_node_widget(" in reb_src:
        check("  恢复在建好新窗格之后",
              reb_src.index("_create_node_widget(") < reb_src.index("_restore_log_texts("))

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
