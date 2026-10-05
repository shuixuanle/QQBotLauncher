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

    print("\n[4] 接线：入口都在**顶部工具栏**，启动路径只有一条")
    window_methods = methods(window_src, "MainWindow")
    check("MainWindow 有 build_current_bot_menu（当前 Bot / 当前程序 两组）",
          "build_current_bot_menu" in window_methods)
    merged = window_methods.get("build_current_bot_menu", "")
    check("合并菜单里同时有「对当前 Bot」与「对当前程序」两组",
          "对当前 Bot" in merged and "对当前程序" in merged)
    check("当前程序那三个操作复用 _on_tab_program_action（没写第二份）",
          merged.count("self._on_tab_program_action") == 1
          and "for label, name in" in merged)
    check("两组各三个动作（启动/停止/重启）",
          merged.count('("启动", "start")') == 1 and "self.action_start_bot" in merged
          and "self.action_stop_bot" in merged and "self.action_restart_bot" in merged)
    check("标题带当前机器人名 / 当前程序名",
          "对当前 Bot：" in merged and "对当前程序：" in merged)
    check("没有窗口/程序时动作会置灰", "setEnabled(bool(key))" in merged
          and "setEnabled(bot is not None)" in merged)
    check("顶部工具栏有「当前 Bot」下拉按钮",
          "self.current_bot_button = QToolButton(bar)" in window_src
          and "self.current_bot_button.clicked.connect(self._on_current_bot_button)"
          in window_src)
    check("下拉按钮是 InstantPopup（点一下就弹菜单）",
          "InstantPopup" in window_src)
    check("原来的三个按钮**不再**单独占工具栏位",
          "bar.addAction(self.action_start_bot)" not in window_src
          and "bar.addAction(self.action_stop_bot)" not in window_src
          and "bar.addAction(self.action_restart_bot)" not in window_src)
    check("三个 QAction 仍然存在（快捷键与机器人菜单还指着它们）",
          'self.action_start_bot = QAction("启动当前 Bot"' in window_src
          and "bot_menu.addAction(self.action_start_bot)" in window_src)

    check("顶部「启动全部」是分裂按钮（点文字=全启动，点箭头=挑着启动）",
          "self.start_all_button = QToolButton(bar)" in window_src
          and "MenuButtonPopup" in window_src
          and "setDefaultAction(self.action_start_all)" in window_src)
    check("分裂按钮挂的是勾选菜单，并且每次展开前重建",
          "self.start_all_button.setMenu(self.build_start_selection_menu())" in window_src
          and "aboutToShow.connect(self._refresh_start_selection_menu)" in window_src)
    check("勾选菜单复用 _on_tab_start_requested（启动路径只有一条）",
          "menu.startRequested.connect(self._on_tab_start_requested)" in window_src)
    check("菜单按当前机器人预勾选", "focus_on" in window_src
          or "current_bot_id=self._current_bot_id()" in window_src)
    check("窗格标题栏上不再有第二个入口（入口只有一处）",
          "startMenuRequested" not in tab_src and "start_menu_button" not in tab_src)

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
