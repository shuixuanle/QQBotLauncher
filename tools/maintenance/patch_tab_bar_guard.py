# -*- coding: utf-8 -*-
"""给标签栏的同步调用加保护：它出问题不能连累"打开窗口"本身。

真机事故：`_sync_bot_tab_bar()` 里把 `bot_name`（@property）当方法调用，
抛 TypeError → `open_bot_tab()` 中断 → **管理器窗口打不开、日志界面进不去**。
标签栏只是辅助入口，不该有这个杀伤力。

用法：
    python tools\\patch_tab_bar_guard.py            # 预览
    python tools\\patch_tab_bar_guard.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "ui" / "main_window.py"

#: (旧片段, 新片段, 说明)
REPLACEMENTS = [
    (
        """        self.stack.addWidget(tab)
        self._sync_bot_tab_bar()
""",
        """        self.stack.addWidget(tab)
        # 标签栏只是"辅助入口"：它出问题绝不能连累打开窗口本身
        # （真机事故：bot_name 被当方法调用 → 整个窗口打不开、日志界面进不去）
        self._safe_sync_bot_tab_bar()
""",
        "open_bot_tab 改用 _safe_sync_bot_tab_bar",
    ),
    (
        """        self._refresh_nav()
        self._apply_nav_focus_marker(notify=False)
        self._update_placeholder_visible()
        self._sync_bot_tab_bar()
        self._update_bot_tab_bar_visible()
        return True""",
        """        self._refresh_nav()
        self._apply_nav_focus_marker(notify=False)
        self._update_placeholder_visible()
        self._safe_sync_bot_tab_bar()
        return True""",
        "_remove_tab 改用安全同步",
    ),
    (
        """            self._apply_nav_focus_marker(notify=False)
        self._sync_bot_tab_bar()
        self._update_bot_tab_bar_visible()
        self.refresh_status()""",
        """            self._apply_nav_focus_marker(notify=False)
        self._safe_sync_bot_tab_bar()
        self.refresh_status()""",
        "_on_current_page_changed 改用安全同步",
    ),
]

HELPER = '''    def _safe_sync_bot_tab_bar(self) -> None:
        """同步标签栏 + 可见性，吞掉异常。

        标签栏是"锦上添花"的辅助入口，一旦它抛异常就会顺着调用栈把
        ``open_bot_tab`` 打断 —— 真机后果是**管理器窗口打不开、日志界面进不去**。
        所以这里统一兜底：标签出问题最多是标签不对，窗口必须照常打开。
        """
        try:
            self._sync_bot_tab_bar()
        except (RuntimeError, AttributeError, TypeError, ValueError):
            pass
        try:
            self._update_bot_tab_bar_visible()
        except (RuntimeError, AttributeError, TypeError, ValueError):
            pass

'''


def main() -> int:
    apply = "--apply" in sys.argv
    src = TARGET.read_text(encoding="utf-8")

    if "_safe_sync_bot_tab_bar" in src:
        print("已经打过补丁，跳过。")
        return 0

    changed = []
    for old, new, label in REPLACEMENTS:
        if src.count(old) != 1:
            print("!! 锚点不唯一（{} 次）：{}".format(src.count(old), label))
            return 1
        src = src.replace(old, new, 1)
        changed.append(label)

    anchor = "    def _build_bot_tab_bar(self) -> QTabBar:"
    if src.count(anchor) != 1:
        print("!! 找不到插入 _safe_sync_bot_tab_bar 的位置")
        return 1
    src = src.replace(anchor, HELPER + anchor, 1)
    changed.append("插入 _safe_sync_bot_tab_bar 定义")

    for label in changed:
        print("  - {}".format(label))
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    TARGET.write_text(src, encoding="utf-8", newline="")
    print("已写入 {}".format(TARGET.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
