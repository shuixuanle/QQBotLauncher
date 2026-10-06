# -*- coding: utf-8 -*-
"""静态检查：左侧列表的宽度与"长名字不许被裁"（真机 2026-10-05）。

真机现象
--------
> "左侧栏无法调整宽度，然后出现显示错误" —— 截图里左栏被拖窄之后，列表条目**横向滚动了**，
> 每行只剩尾巴（"……序 · 未运行"），底部三个按钮也被切掉一截。

两个原因，各配一条断言：
  1. 左栏的树没关横向滚动条 → 条目超过宽度就横向滚，看着像"显示错误"；
  2. 底部按钮并排 + "关闭窗口"四个字，把左栏的**最小宽度**撑到 230px 左右 →
     左栏拖不窄，被切时按钮也跟着缺一截。

用法：
    python tools\\check_nav_panel.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOW = ROOT / "app" / "ui" / "main_window.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError。这里统一退化成 ?。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    print("[0] 文件")
    check("app/ui/main_window.py 存在", WINDOW.exists())
    if not WINDOW.exists():
        print("\n结果：失败项 = {}".format(failures))
        return 1
    src = WINDOW.read_text(encoding="utf-8")

    print("\n[1] 左栏树：长名字要省略，不许横向滚动")
    check("关掉了横向滚动条（ScrollBarAlwaysOff）",
          "setHorizontalScrollBarPolicy(" in src
          and "ScrollBarAlwaysOff" in src)
    check("超长名字用右侧省略号", "setTextElideMode(Qt.TextElideMode.ElideRight)" in src)
    check("树是单列（没有多余的列把宽度撑开）", "setColumnCount(1)" in src)

    print("\n[2] 底部按钮：标签要短，且允许被压窄（否则左栏拖不窄）")
    check("关闭按钮标签 <= 3 个字（完整含义在 tooltip 里）",
          'self.nav_close_button = QPushButton("关闭"' in src)
    check("三个按钮都设了最小宽度 0", "button.setMinimumWidth(0)" in src
          and "for button in (self.nav_open_button, self.nav_stop_button, "
              "self.nav_close_button):" in src)
    check("三个按钮都有 tooltip 说明", src.count("self.nav_open_button.setToolTip(") == 1
          and src.count("self.nav_stop_button.setToolTip(") == 1
          and src.count("self.nav_close_button.setToolTip(") == 1)

    print("\n[3] 面板本身：不许把最大宽度写死（写死就真的拖不动了）")
    panel_part = src[src.find("def _build_nav("):src.find("def _build_placeholder_page(")]
    check("navPanel 没有 setMaximumWidth", "setMaximumWidth" not in panel_part)
    #  真机 2026-10-06：左栏的最小宽度改成"至少放得下顶部那两个按钮"
    #  （按按钮实际宽度算，和 MIN_NAV_WIDTH 取较大者）
    check("navPanel 的最小宽度按顶部按钮算（至少放得下两个按钮）",
          "self._nav_min_width()" in src and "navButtonsWidth" in src)
    check("最小宽度仍是 MIN_NAV_WIDTH 与按钮需求取较大者",
          "need = max(need, int(panel.property" in src
          and "MIN_NAV_WIDTH" in src)
    check("分隔条不是 0 宽（0 宽拖不到）",
          "setHandleWidth(5)" in src or "setHandleWidth(4)" in src
          or "setHandleWidth(6)" in src)
    check("不允许把左栏拖成 0（childrenCollapsible False）",
          "setChildrenCollapsible(False)" in src)

    print("\n[4] 宽度出问题时的出口：视图 → 左栏宽度：恢复默认")
    check("有 action_reset_nav_width", "action_reset_nav_width" in src)
    check("挂进了视图菜单", "self.view_menu.addAction(self.action_reset_nav_width)" in src)
    check("有 reset_nav_width() 实现", "def reset_nav_width(" in src)
    handler = src[src.find("def reset_nav_width("):]
    handler = handler[:handler.find("\n    def ", 1)]
    check("恢复的是 DEFAULT_NAV_WIDTH",
          "setSizes([DEFAULT_NAV_WIDTH" in handler)
    check("顺带写回设置（下次启动也是这个宽度）",
          "SETTINGS_NAV_SPLIT" in handler)

    print("\n[5] 记录里的宽度会被夹在 [MIN, MAX] 之间")
    saved_fn = src[src.find("def _saved_nav_width("):]
    saved_fn = saved_fn[:saved_fn.find("\n    def ", 1)]
    check("小于 MIN 的按 MIN 处理", "MIN_NAV_WIDTH" in saved_fn)
    check("大于 MAX 的按 MAX 处理", "MAX_NAV_WIDTH" in saved_fn)
    check("MIN < MAX（上下限没写反）",
          "MIN_NAV_WIDTH = 180" in src and "MAX_NAV_WIDTH = 640" in src)

    print("\n[6] 窗口尺寸：拖小窗口不许裁内容（真机 2026-10-06）")
    check("  主窗口有 resizeEvent（把最小宽度对齐到内容需要）",
          "def resizeEvent(self, event)" in src and "_window_min_width" in src)
    check("  最小宽度取 max(下限, 内容需要)",
          "max(MIN_WINDOW_WIDTH, self.minimumSizeHint().width())" in src)
    check("  左栏按钮的最小宽度把布局边距也算进去了",
          "margins.left() + margins.right()" in src)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
