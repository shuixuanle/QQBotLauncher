# -*- coding: utf-8 -*-
"""静态检查：日志视图的头部行与窗格标题栏**不能重复显示同一程序的名字与状态**。

真机反馈（附截图，框住两行）："图片中框住的这两栏功能上是有重复的，建议合并"

合并后的约定：
  · ProgramWidget 只有**一行头部**（标题 + 状态 + 按钮组 + 行数），不再是"标题行 + 工具栏行"两行；
  · 嵌在窗格里时（show_toolbar=False）：标题与状态**隐藏**（窗格标题栏负责显示），
    行数标签被窗格标题栏 `take_count_label()` **接管**；
  · 于是屏幕上只剩一行：窗格标题栏（程序名 + 状态 + PID + 行数 + 启停/清空按钮）。

用法：
    python tools\\check_pane_header.py
"""

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
WIDGET = ROOT / "app" / "ui" / "program_widget.py"
TAB = ROOT / "app" / "ui" / "bot_tab.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def method_src(src_text, tree, cls_name, method):
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name), None
    )
    if cls is None:
        return ""
    fn = next(
        (c for c in cls.body if isinstance(c, ast.FunctionDef) and c.name == method), None
    )
    return ast.get_source_segment(src_text, fn) or "" if fn else ""

# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    src = WIDGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    build = method_src(src, tree, "ProgramWidget", "_build_ui")

    print("[1] ProgramWidget 只有一行头部")
    check("  _build_ui 里创建了 self.header", "self.header" in build)
    check("  标题 / 状态 / 行数都在同一行（header_row）",
          all(k in build for k in ("header_row.addWidget(self.title_label)",
                                   "header_row.addWidget(self.status_label)",
                                   "header_row.addWidget(self.count_label)")))
    check("  按钮组也在同一行", "header_row.addWidget(self.toolbar)" in build)
    # 不应再出现"把标题放进 layout、再把工具栏放进 layout"的两行结构
    check("  没有把 toolbar 直接加到 layout（那是旧的第二行）",
          "layout.addWidget(self.toolbar)" not in build)
    check("  也没有把标题行加到 layout", "layout.addLayout(header)" not in build)

    print("\n[2] 嵌在窗格时不重复显示名字/状态")
    check("  标题随 show_toolbar 隐藏",
          "self.title_label.setVisible(bool(show_toolbar))" in build)
    check("  状态随 show_toolbar 隐藏",
          "self.status_label.setVisible(bool(show_toolbar))" in build)
    check("  行数标签**始终保留**（窗格需要它）",
          "self.count_label.setVisible(False)" not in build)
    check("  有 take_count_label 供窗格接管", "def take_count_label" in src)
    check("  有 _sync_header_visible（空行时整行收起）",
          "def _sync_header_visible" in src)
    check("  接管后重算了头部可见性",
          "_sync_header_visible()" in method_src(src, tree, "ProgramWidget",
                                                 "take_count_label"))

    tab_src = TAB.read_text(encoding="utf-8")
    tab_tree = ast.parse(tab_src)
    print("\n[3] 窗格标题栏接管行数标签")
    bar = method_src(tab_src, tab_tree, "PaneWidget", "_build_title_bar")
    check("  标题栏里有 count_label 占位", "self.count_label = QLabel" in bar)
    check("  占位被放进布局", "row.addWidget(self.count_label)" in bar)
    check("  记住了标题栏布局", "_title_bar_layout" in bar)
    adopt = method_src(tab_src, tab_tree, "PaneWidget", "_adopt_count_label")
    check("  存在 _adopt_count_label", bool(adopt))
    check("  创建视图时调用它",
          "_adopt_count_label(view)" in method_src(tab_src, tab_tree, "PaneWidget",
                                                    "_create_view"))
    check("  用 take_count_label() 拿到标签", "take_count_label()" in adopt)
    check("  插到占位原来位置并摘掉占位",
          "layout.indexOf(self.count_label)" in adopt
          and "layout.removeWidget(self.count_label)" in adopt)

    print("\n[4] 长标签不许顶住最小宽度（真机 2026-10-05）")
    #  现象："左栏最大宽度被限制得太窄"，而且**只在名字很长的测试实例打开时**出现。
    #  原因不是左栏 —— 是右侧的两个标题行（机器人名 / 程序名都很长）把右侧的
    #  minimumSizeHint 撑大了，主分隔条于是没法把更多宽度给左栏。
    #  QLabel 默认拿"文字宽度"当最小宽度，所以这些标签必须设成 Ignored。
    tab_src = (ROOT / "app" / "ui" / "bot_tab.py").read_text(encoding="utf-8")
    widget_src = (ROOT / "app" / "ui" / "program_widget.py").read_text(encoding="utf-8")
    check("  机器人标题行的标签设成了 Ignored（宽随布局）",
          "_flexible_labels = (title, self.layout_label, self.status_label)" in tab_src
          and "QSizePolicy.Policy.Ignored" in tab_src)
    check("  窗格标题行的标签也设成了 Ignored",
          "for _label in (self.title_label, self.status_label, self.count_label):"
          in widget_src)
    check("  两处都顺手把最小宽度清零",
          tab_src.count("label.setMinimumWidth(0)") >= 1
          and widget_src.count("_label.setMinimumWidth(0)") >= 1)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
