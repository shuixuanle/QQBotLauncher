# -*- coding: utf-8 -*-
"""静态检查：「启动全部 ▾」的树形勾选菜单（app/ui/start_menu.py）。

真机需求（2026-10-05）
----------------------
> 在「启动全部」那里加一个像「布局 ▾」的下拉，里面浅色字写"选择启动的程序"，
> 保留完整的实例框架（像 cmd 的 tree、跟左侧栏差不多），每个选项**右侧**有勾选框；
> 勾选 ATRI bot 时，它下面三个小程序默认一起勾上。

这里盯住的就是上面这几条 —— 以后谁改菜单，别把这些悄悄改没了：
  [1] 结构：QMenu + 缩进层级 + 右侧勾选框（不能退回 QAction 的左侧勾选）
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


def runtime_strings(source: str) -> list:
    """源码里**真正会执行到**的字符串常量（文档字符串/注释不算）。

    为什么需要：解释性注释里往往写着"以前那样是错的""不再写 xxx"，
    直接 `"xxx" not in source` 会被自己的注释绊倒（写这版时就绊了两次）。
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def uses_attribute(source: str, name: str) -> bool:
    """代码里有没有用到某个属性/枚举名（例如 MenuButtonPopup）。"""
    return any(isinstance(node, ast.Attribute) and node.attr == name
               for node in ast.walk(ast.parse(source)))


def glyph_literals(source: str) -> list:
    """界面代码里出现的"制表符号 / 前缀标记"字面量（文档字符串不算）。

    真机反馈（2026-10-05）：`├──` `└──` `▍` 这些都不该出现在菜单里 ——
    层级只用缩进 + 机器人名加粗表达。文档字符串里解释"为什么不用"是允许的。
    """
    glyphs = ("\u251c", "\u2514", "\u2502", "\u258d", "\u2500")
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            if any(glyph in node.value for glyph in glyphs):
                found.append(node.value[:24])
    return found


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

    print("\n[1] 风格：和「布局」下拉一样是**纯原生 QAction** 菜单")
    check("有 StartSelectionMenu(QMenu)", "class StartSelectionMenu(QMenu)" in menu_src)
    check("有信号 startRequested(list)", "startRequested = pyqtSignal(list)" in menu_src)
    # 真机二次反馈（2026-10-05）：第一版用自绘控件（右侧勾选框）"很违和"，
    # 要求跟其它下拉统一 —— 所以这里反过来盯着：不许再引入自绘控件。
    check("没有自绘控件（QCheckBox / QWidgetAction / QLabel / QScrollArea）",
          not any(token in menu_src for token in
                  ("QCheckBox(", "QWidgetAction(", "QLabel(", "QScrollArea(")))
    check("勾选列用菜单原生的（没有 AlignRight 之类的自绘对齐）",
          "AlignRight" not in menu_src)
    check("每一项都是原生 QAction", menu_src.count("QAction(") >= 4)
    #  真机三次反馈："√ 太突兀" → 不用菜单原生的勾选列，改成文字方块 ■ / □
    check("不用 setCheckable（免得出现菜单原生的对勾）", "setCheckable" not in menu_src)
    check("勾选框是文字方块 ■ / □（等宽、GBK 安全）",
          'BOX_ON = "\\u25a0"' in menu_src and 'BOX_OFF = "\\u25a1"' in menu_src)
    check("方块真的写进了每一行文字",
          'action.setText("{} {}".format(' in menu_src
          and "BOX_ON if" in menu_src)
    check("勾选状态自己记着（_checked）", "_checked" in menu_src and
          "self._checked[key] = not self._checked.get(key, False)" in menu_src)
    check("小标题是 disabled 的 QAction（和「布局」下拉的“右侧分屏方式”一样）",
          'header = QAction("选择启动的程序"' in menu_src
          and "header.setEnabled(False)" in menu_src)
    check("有分隔线（和别的菜单一致的分段）", menu_src.count("addSeparator()") >= 3)

    print("\n[1b] 层级：只用缩进（不允许制表符号）")
    check("缩进常量是全角空格（比例字体下宽度稳定）",
          'INDENT = "\\u3000\\u3000"' in menu_src)
    check("程序行用 INDENT + 名称", "INDENT + program.name" in menu_src)
    check("程序行标出主程序", "（主程序）" in menu_src)
    check("界面代码里没有制表符号/前缀标记（├ └ │ ▍）",
          not glyph_literals(menu_src), str(glyph_literals(menu_src)[:3]))
    check("没有 BRANCH_* 分支符号常量",
          "BRANCH_MIDDLE" not in menu_src and "BRANCH_LAST" not in menu_src)

    print("\n[2] 联动：勾机器人 → 整组切换；父项方块随子项变化")
    check("有 _toggle_group（整组切换）", "_toggle_group" in menu_methods)
    check("机器人行真接上了整组切换（按 ROW_BOT 属性认行）",
          "ROW_BOT" in menu_src and "self._toggle_group(bot_id)" in menu_src)
    check("有 _refresh_rows（统一重写文字）", "_refresh_rows" in menu_methods)
    check("父项方块 = 子项全勾才 ■",
          "BOX_ON if all(states) else BOX_OFF" in menu_src)
    check("每次改动后都会刷新文字",
          menu_src.count("self._refresh_rows()") >= 4)
    check("机器人名单独记着（不靠拆文本反解析）",
          "self._bot_rows[bot.id] = (bot_action, bot_keys, bot_name)" in menu_src)
    check("程序名/主程序也单独记着", "_names" in menu_src and "_primary" in menu_src)

    print("\n[2b] 点勾选行不关菜单（真机：点一下菜单就消失 / 还穿透到按钮上）")
    handler = menu_methods.get("mouseReleaseEvent", "")
    check("拦下了 mouseReleaseEvent", bool(handler))
    check("程序行：只翻状态 + 刷新，不调 super", "self._checked[key] = not" in handler
          and "self._refresh_rows()" in handler)
    check("机器人行：整组切换，也不关菜单", "self._toggle_group(bot_id)" in handler)
    check("全选/清空：标了 KEEP_OPEN_FLAG，也留在菜单里",
          "KEEP_OPEN_FLAG" in handler and "action.property(KEEP_OPEN_FLAG)" in handler)
    check("其余项走默认行为（点完关菜单）", "super().mouseReleaseEvent(event)" in handler)
    check("「启动勾选的」没标 keep_open",
          "KEEP_OPEN_FLAG" not in menu_methods.get("_build_footer", "").split("start = QAction")[1])

    print("\n[3] 默认：按当前机器人预勾选")
    check("有 focus_on()", "focus_on" in menu_methods)
    check("菜单标出「（当前）」", "（当前）" in menu_src)
    check("只勾当前机器人的程序（按 bot_id 前缀）",
          "key.startswith(prefix)" in menu_src)
    check("勾选列表只收启用中的程序", "program.enabled" in menu_src)
    check("主程序排最前（与界面一致）", "ROLE_PRIMARY" in menu_src)

    print("\n[4] 接线：入口都在**顶部工具栏**，启动路径只有一条")
    window_methods = methods(window_src, "MainWindow")
    s_pick_hint = window_methods.get("_on_start_pick_button", "")
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
          'self.current_bot_button = QPushButton("当前 Bot' in window_src
          and "self.current_bot_button.clicked.connect(self._on_current_bot_button)"
          in window_src)
    check("点一下就弹菜单（手动 exec，不靠 InstantPopup）",
          "self.build_current_bot_menu().exec(" in window_src)
    check("原来的三个按钮**不再**单独占工具栏位",
          "bar.addAction(self.action_start_bot)" not in window_src
          and "bar.addAction(self.action_stop_bot)" not in window_src
          and "bar.addAction(self.action_restart_bot)" not in window_src)
    check("三个 QAction 仍然存在（快捷键与机器人菜单还指着它们）",
          'self.action_start_bot = QAction("启动当前 Bot"' in window_src
          and "bot_menu.addAction(self.action_start_bot)" in window_src)

    #  真机事故（2026-10-05）：MenuButtonPopup 分裂按钮在菜单关掉的那一下会被
    #  "穿透"点击，默认动作 = 启动全部 —— 用户只勾了一下，程序却起来了。
    #  所以改成「启动全部」按钮 + 紧跟一个独立的小箭头按钮。
    check("顶部「启动全部」按钮仍在（一键全启动）",
          'self.start_all_button = QPushButton("启动全部"' in window_src
          and "self.action_start_all.trigger" in window_src)
    check("**不用** MenuButtonPopup 分裂按钮（会被穿透点击）",
          not uses_attribute(window_src, "MenuButtonPopup"))
    check("旁边是独立的小箭头按钮，点开弹勾选菜单",
          "self.start_pick_button.clicked.connect(self._on_start_pick_button)"
          in window_src
          and "self.build_start_selection_menu()" in s_pick_hint)
    #  真机三次反馈：箭头要么两个、要么展开时多冒一个 —— 现在只留**一个我们画的**：
    #  自己写 "▾"，并把 Qt 自带的 menu-indicator 关掉。
    check("小箭头按钮自己写箭头（只留一个，我们自己控制位置）",
          'self.start_pick_button = QPushButton("' in window_src
          and "\u25be" in window_src)
    #  根治方案：这两个按钮**不是 QToolButton、也不 setMenu** ——
    #  Qt 只有在 QToolButton 带菜单时才画那个 menu-indicator（真机那三个"多余的勾"）。
    check("两个按钮都不是 QToolButton（Qt 才不会画 menu-indicator）",
          "self.start_all_button = QToolButton" not in window_src
          and "self.start_pick_button = QToolButton" not in window_src
          and "self.current_bot_button = QToolButton" not in window_src)
    check("不给按钮 setMenu（改成手动 exec）",
          "start_pick_button.setMenu(" not in window_src
          and "current_bot_button.setMenu(" not in window_src)
    check("点一下就弹菜单（手动 exec，和「布局」按钮同一套做法）",
          "menu.exec(button.mapToGlobal(button.rect().bottomLeft()))" in
          methods(window_src, "MainWindow").get("_on_start_pick_button", ""))
    check("菜单每次点开时现建（默认勾选跟着当前机器人走）",
          "menu = self.build_start_selection_menu()" in s_pick_hint
          and "menu.exec(button.mapToGlobal(" in s_pick_hint)
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
