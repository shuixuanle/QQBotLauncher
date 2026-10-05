# -*- coding: utf-8 -*-
"""静态检查：「启动全部 ▾」的树形勾选菜单（app/ui/start_menu.py）。

真机需求（2026-10-05）
----------------------
> 在「启动全部」那里加一个像「布局 ▾」的下拉，里面浅色字写"选择启动的程序"，
> 保留完整的实例框架（像 cmd 的 tree、跟左侧栏差不多），每个选项**右侧**有勾选框；
> 勾选 ATRI bot 时，它下面三个小程序默认一起勾上。

这里盯住的就是上面这几条 —— 以后谁改菜单，别把这些悄悄改没了：
  [1] 结构：QMenu + 树形行 + 右侧勾选框（不能退回 QAction 的左侧勾选）
  [2] 联动：勾机器人 → 子项全跟着；子项部分勾 → 父项半选
  [3] 默认：菜单按当前机器人预勾选（点开直接确定 = 启动这个机器人）
  [4] 接线：只有一条启动路径（复用 _on_tab_start_requested），没写第二份
  [5] 原行为不变：「启动全部」按钮仍然一键启动全部

用法：
    python tools\\check_start_menu.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MENU = ROOT / "app" / "ui" / "start_menu.py"
TAB = ROOT / "app" / "ui" / "bot_tab.py"
WINDOW = ROOT / "app" / "ui" / "main_window.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过）。这里统一退化成 ?。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def methods(source: str, cls: str = "") -> dict:
    """类里的方法名 → 源码片段（cls 为空时取模块级函数）。"""
    tree = ast.parse(source)
    found = {}
    scope = tree.body
    if cls:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == cls:
                scope = node.body
    for node in scope:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = ast.get_source_segment(source, node) or ""
    return found


def main() -> int:
    print("[0] 文件在不在")
    check("app/ui/start_menu.py 存在", MENU.exists())
    if not MENU.exists():
        print("\n结果：失败项 = {}".format(failures))
        return 1
    menu_src = MENU.read_text(encoding="utf-8")
    tab_src = TAB.read_text(encoding="utf-8")
    window_src = WINDOW.read_text(encoding="utf-8")
    menu_methods = methods(menu_src, "StartSelectionMenu")

    print("\n[1] 结构：QMenu + 树形分支 + **右侧**勾选框")
    check("有 StartSelectionMenu(QMenu)", "class StartSelectionMenu(QMenu)" in menu_src)
    check("有信号 startRequested(list)", "startRequested = pyqtSignal(list)" in menu_src)
    check("分支符号 ├── / └── 都在", "├──" in menu_src and "└──" in menu_src)
    check("程序行用分支符号（不是随便缩进一下）",
          "BRANCH_LAST" in menu_src and "BRANCH_MIDDLE" in menu_src)
    check("勾选框靠右（AlignRight）", "AlignRight" in menu_src)
    check("勾选框不带文字（文字在左边单独一个 QLabel）",
          not any(token in menu_src for token in ('QCheckBox("', "QCheckBox('")))
    check("菜单标题「选择启动的程序」", "选择启动的程序" in menu_src)
    check("标题/分支用主题的次要文字色（浅色字）", "muted_text_color" in menu_src)
    check("点整行也能勾（不用瞄准小方框）", "def mousePressEvent" in menu_src)
    check("机器人多时列表可滚动", "QScrollArea" in menu_src)

    print("\n[2] 联动：勾机器人 → 子项一起；子项不齐 → 父项半选")
    check("父项三态", "setTristate(True)" in menu_src)
    check("有 _set_group（成组勾选）", "_set_group" in menu_methods)
    check("机器人行真接上了 _set_group",
          "self._set_group(" in menu_src and "bot_box.toggled.connect" in menu_src)
    check("有 _sync_bot_rows（父项半选刷新）", "_sync_bot_rows" in menu_methods)
    check("半选用了 PartiallyChecked", "PartiallyChecked" in menu_src)
    check("子项变化会刷新父项",
          "self._sync_bot_rows" in menu_methods.get("_build_tree", ""))

    print("\n[3] 默认：按当前机器人预勾选")
    check("有 focus_on()", "focus_on" in menu_methods)
    check("菜单标出「（当前）」", "（当前）" in menu_src)
    check("只勾当前机器人的程序（按 bot_id 前缀）",
          "key.startswith(prefix)" in menu_src)
    check("勾选列表只收启用中的程序", "program.enabled" in menu_src)
    check("主程序排最前（与界面一致）", "ROLE_PRIMARY" in menu_src)

    print("\n[4] 接线：启动路径只有一条")
    check("BotTab 有 startMenuRequested 信号",
          "startMenuRequested = pyqtSignal(str, object)" in tab_src)
    check("BotTab 有下拉小按钮", 'start_menu_button = QPushButton("▾"' in tab_src)
    check("下拉按钮接上 _on_start_menu_button", "_on_start_menu_button" in tab_src)
    check("点击时带出全局坐标（菜单贴着按钮弹）",
          "mapToGlobal" in tab_src and "startMenuRequested.emit" in tab_src)
    check("MainWindow 接了 startMenuRequested",
          "tab.startMenuRequested.connect(self._on_start_menu_requested)" in window_src)
    handler = methods(window_src, "MainWindow").get("_on_start_menu_requested", "")
    check("弹菜单的槽存在", bool(handler))
    check("菜单的 startRequested 复用 _on_tab_start_requested（没写第二份启动逻辑）",
          "menu.startRequested.connect(self._on_tab_start_requested)" in handler)
    check("弹菜单前先 focus_on(当前机器人)", "focus_on(bot_id)" in handler)

    print("\n[5] 原行为不变：「启动全部」仍然一键启动全部")
    check("_on_start_all 还在", "_on_start_all" in methods(tab_src, "BotTab"))
    check("它发的仍然是全部 key",
          "self.all_keys()" in methods(tab_src, "BotTab").get("_on_start_all", ""))
    check("「启动全部」按钮没有被换成菜单按钮",
          'self.start_button = QPushButton("启动全部"' in tab_src)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
