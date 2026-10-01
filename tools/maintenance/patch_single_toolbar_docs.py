# -*- coding: utf-8 -*-
"""把检查器段标题与 README 文案同步为"顶部只有一行工具栏"。

用法：
    python tools\\patch_single_toolbar_docs.py            # 预览
    python tools\\patch_single_toolbar_docs.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "tools" / "check_bot_tab_bar.py"
README = ROOT / "README.md"

CHECKER_OLD = 'print("\\n[4.6] 两条工具栏职责不重叠（真机反馈过重复）")'
CHECKER_NEW = 'print("\\n[4.6] 只保留一行工具栏（真机确认的布局）")'

README_OLD = """- **两条工具栏，职责不重叠**（同一排按钮不再出现两次）：
  - **主工具栏**（全局）：启动全部 · 停止全部；
  - **Bot 工具条**（机器人/窗口）：启动 · 停止 · 重启当前 Bot ▏
    **打开全部窗口** · 查看已有 bot…（=按需挑一个打开）▏
    新建 Bot · 编辑当前 Bot · 打开配置文件。
    始终可用，左栏折叠后就是它顶上来。"""

README_NEW = """- **顶部只有一行工具栏**（顺序按"用得多 → 全局"，同一动作只挂一次）：
  启动当前 Bot · 停止当前 Bot · 重启当前 Bot ▏
  **打开全部窗口** · 查看已有 bot…（=按需挑一个打开）▏
  新建 Bot · 编辑当前 Bot · 打开配置文件 ▏启动全部 · 停止全部。
  它始终可用 —— 左栏折叠后就是它顶上来。"""


def patch(path: Path, old: str, new: str, label: str, apply: bool) -> bool:
    src = path.read_text(encoding="utf-8")
    if new in src:
        print("  跳过（已是新版）：{}".format(label))
        return True
    if src.count(old) != 1:
        print("!! 锚点出现 {} 次：{}".format(src.count(old), label))
        return False
    src = src.replace(old, new, 1)
    print("  - {}".format(label))
    if apply:
        path.write_text(src, encoding="utf-8", newline="")
    return True


def main() -> int:
    apply = "--apply" in sys.argv
    ok = True
    print("check_bot_tab_bar.py")
    ok &= patch(CHECKER, CHECKER_OLD, CHECKER_NEW, "段标题改为「只保留一行工具栏」", apply)
    print("README.md")
    ok &= patch(README, README_OLD, README_NEW, "工具栏说明改为一行", apply)
    if not ok:
        return 1
    if not apply:
        print("（预览）加 --apply 才会写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
