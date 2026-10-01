# -*- coding: utf-8 -*-
"""静态检查：窗格按钮的作用范围必须与标题一致（只作用于当前程序）。

真机事故（危险）：
    `PaneWidget._emit_action()` 里写的是

        for key in self.keys():
            self.programAction.emit(key, action)

    于是窗格标题写着「Ollama 服务（副程序）」，点它的「停止」却把**整个实例下的
    所有程序**一起停掉（用户实测："这一栏的按钮会导致整个 bot 实例下的所有程序
    共同启动停止"）。

    正确语义：标题显示哪个程序，按钮就只作用在哪个程序上（`self.current_key()`）。
    要操作全部，用窗格空白处右键菜单（那里明确写着"（全部程序）"）或顶部「xx 全部」。

用法：
    python tools\\check_pane_scope.py
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT_TAB = ROOT / "app" / "ui" / "bot_tab.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def main() -> int:
    src = BOT_TAB.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PaneWidget"),
        None,
    )
    check("找到 PaneWidget", cls is not None)
    methods = {c.name: c for c in cls.body if isinstance(c, ast.FunctionDef)} if cls else {}

    print("\n[1] 窗格按钮只作用于当前程序")
    emit = methods.get("_emit_action")
    check("存在 _emit_action", emit is not None)
    emit_src = ast.get_source_segment(src, emit) or "" if emit else ""
    check("  用 current_key()（当前显示的那个程序）", "current_key()" in emit_src)
    # 必须用 AST 判断：文档串里就写着"原来写的是 for key in self.keys()"，
    # 纯字符串匹配会把注释当成代码（检查器第一版就误报了）。
    iterated_self_keys = False
    if emit is not None:
        for node in ast.walk(emit):
            if not isinstance(node, (ast.For, ast.AsyncFor)):
                continue
            target = ast.unparse(node.iter)
            if "keys()" in target:
                iterated_self_keys = True
    check("  没有遍历整个窗格（for ... in self.keys()）", not iterated_self_keys,
          "出现了 for key in self.keys() 就又变成「启停全部」了")
    check("  发出请求时只用单个 key", emit_src.count("programAction.emit") == 1)
    if emit is not None:
        loops = [n for n in ast.walk(emit) if isinstance(n, (ast.For, ast.While))]
        check("  函数里没有任何循环（单程序操作不该循环）", not loops,
              "发现 {} 个循环".format(len(loops)))

    print("\n[2] 三个按钮都走 _emit_action（不会各自另写一套）")
    for name in ("start_button", "stop_button", "restart_button"):
        hits = [
            line.strip() for line in src.splitlines()
            if name in line and "_emit_action" in line
        ]
        check("  {} 接的是 _emit_action".format(name), bool(hits), hits[0] if hits else "")

    print("\n[3] 「全部程序」的入口要对得上（作用范围写在文案里）")
    # 注意：show_context_menu / 控制条在 **BotTab** 上，不在 PaneWidget 上
    tab_cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BotTab"), None
    )
    tab_methods = (
        {c.name: c for c in tab_cls.body if isinstance(c, ast.FunctionDef)}
        if tab_cls is not None else {}
    )
    menu = tab_methods.get("show_context_menu")
    check("存在 show_context_menu", menu is not None)
    menu_src = ast.get_source_segment(src, menu) or "" if menu else ""
    check("  无 key 时作用于全部程序（all_keys）", "all_keys()" in menu_src)
    check("  菜单项带作用范围后缀（scope_name / 全部程序）",
          "scope_name" in menu_src and "全部程序" in menu_src)

    bar = tab_methods.get("_build_control_bar")
    bar_src = ast.get_source_segment(src, bar) or "" if bar else ""
    for text in ("启动全部", "停止全部", "重启全部"):
        check("  控制条上有「{}」".format(text), text in bar_src)

    print("\n[4] tooltip 说清作用范围（避免误以为操作全部）")
    for keyword in ("当前显示", "要启停全部程序"):
        check("  tooltip 含「{}」".format(keyword), keyword in src)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
