# -*- coding: utf-8 -*-
"""重排两条工具栏（去掉重复项）之后的一致性修正。

背景（真机反馈）：
    "那一栏有一些重复的东西，建议根据需要展示的归纳整理，重新布局"

重排后两条工具栏**不再有重复项**：
    主工具栏  （全局）启动全部 · 停止全部
    Bot 工具条（机器人/窗口）启动 · 停止 · 重启 ▏打开全部窗口 · 查看已有 bot… ▏
                              新建 · 编辑当前 Bot · 打开配置文件

因此需要跟着改：
  1. 菜单项文案（原来叫"显示标签栏与工具条"，现在只管标签栏了）；
  2. 自检断言（Bot 工具条改成常显，不再随左栏折叠而显隐）。

用法：
    python tools\\patch_toolbar_layout.py            # 预览
    python tools\\patch_toolbar_layout.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "ui" / "main_window.py"

REPLACEMENTS = [
    (
        'self.action_toggle_bot_tab_bar = QAction("显示标签栏与工具条", self)',
        'self.action_toggle_bot_tab_bar = QAction("显示窗口标签栏", self)',
        "菜单项文案（工具条已常显，此项只管标签栏）",
    ),
    (
        '''        self.action_toggle_bot_tab_bar.setToolTip(
            "在顶部显示「Bot 工具条」与「浏览器式标签栏」\\n"
            "（折叠左侧列表时会自动出现，无需手动勾选）"
        )''',
        '''        self.action_toggle_bot_tab_bar.setToolTip(
            "在实例区顶部显示浏览器式窗口标签栏\\n"
            "（折叠左侧列表、或已有打开的窗口时会自动出现）"
        )''',
        "菜单项提示文案",
    ),
    (
        '''        assert bot_bar.isVisible(), "折叠左栏后 Bot 工具条必须自动出现（替代左栏按钮）"''',
        '''        assert bot_bar.isVisible(), "Bot 工具条应当始终可用（它就是那一排操作按钮）"''',
        "自检：折叠后工具条可见",
    ),
    (
        '''        assert not window.bot_tab_bar.isVisible(), "展开后未勾选时应隐藏标签栏"
        assert not bot_bar.isVisible(), "展开后未勾选时应隐藏 Bot 工具条"''',
        '''        assert not window.bot_tab_bar.isVisible(), "展开后未勾选时应隐藏标签栏"
        assert bot_bar.isVisible(), "Bot 工具条应当始终可用（与左栏折叠无关）"''',
        "自检：展开后工具条仍可见",
    ),
]


def main() -> int:
    apply = "--apply" in sys.argv
    src = TARGET.read_text(encoding="utf-8")

    changed = []
    for old, new, label in REPLACEMENTS:
        if old in src and new in src:
            print("  跳过（已是新版）：{}".format(label))
            continue
        count = src.count(old)
        if count != 1:
            print("!! 锚点出现 {} 次：{}".format(count, label))
            return 1
        src = src.replace(old, new, 1)
        changed.append(label)

    for label in changed:
        print("  - {}".format(label))
    if not changed:
        print("无需修改。")
        return 0
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    TARGET.write_text(src, encoding="utf-8", newline="")
    print("已写入 {}".format(TARGET.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
