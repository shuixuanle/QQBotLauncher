# -*- coding: utf-8 -*-
"""把检查器与自检改成"只有一行工具栏"的语义（真机确认的最终布局）。

演进：
  1. 一条主工具栏挂 11 个动作；
  2. 折叠左栏后按钮没了 → 加一条 Bot 工具条；
  3. 两条各挂一份「新建/编辑/启停/查看/配置」→ 用户："有重复的东西"；
  4. 按职责切开（主=全局，Bot=机器人/窗口）；
  5. 用户："主工具栏那 2 个按钮也可以并进 Bot 工具条（只留一行）" → **可以** → 现在只一条。

用法：
    python tools\\patch_single_toolbar.py            # 预览
    python tools\\patch_single_toolbar.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "app" / "ui" / "main_window.py"
CHECKER = ROOT / "tools" / "check_bot_tab_bar.py"

SELFTEST_OLD = '''        for wanted in ("新建 Bot", "编辑当前 Bot", "启动当前 Bot", "停止当前 Bot",
                       "重启当前 Bot", "打开全部窗口", "查看已有 bot…", "打开配置文件"):
            assert any(wanted in text for text in tool_actions), (
                "Bot 工具条应当包含「{}」，实际 {}".format(wanted, tool_actions)
            )'''

SELFTEST_NEW = '''        for wanted in ("新建 Bot", "编辑当前 Bot", "启动当前 Bot", "停止当前 Bot",
                       "重启当前 Bot", "打开全部窗口", "查看已有 bot…", "打开配置文件",
                       "启动全部", "停止全部"):
            assert any(wanted in text for text in tool_actions), (
                "工具条应当包含「{}」，实际 {}".format(wanted, tool_actions)
            )
        # 只保留**一行**工具栏（真机确认的布局）：顶部不应再有第二条
        bars = window.findChildren(QToolBar)
        print("    窗口里的工具栏数量 = {}（应当只有 1 条）".format(len(bars)))
        assert len(bars) == 1, "应当只有一条工具栏，实际 {}".format(
            [b.windowTitle() for b in bars]
        )
        assert window.toolbar is bot_bar, "self.toolbar 应指向这条唯一的工具条"'''

CHECKER_OLD = '''    check("  主工具栏只放全局动作（启动全部/停止全部）",
          set(main_actions) == {"action_start_all", "action_stop_all"},
          "实际 {}".format(main_actions))
    check("  两条工具栏没有重复项", not (set(main_actions) & set(bot_actions)),
          "重复：{}".format(sorted(set(main_actions) & set(bot_actions))))
    check("  打开全部窗口在 Bot 工具条上",
          "action_open_all_windows" in bot_actions)'''

CHECKER_NEW = '''    # 现在只有**一条**工具栏（真机确认"只留一行"）：_build_toolbar 委托给
    # _build_bot_bar，不再自己挂动作。
    check("  _build_toolbar 不再自己挂动作（只委托）",
          not main_actions, "实际 {}".format(main_actions))
    check("  _build_toolbar 里调用 _build_bot_bar 并复用 self.toolbar",
          "_build_bot_bar()" in mb_src and "self.toolbar = self.bot_bar" in mb_src)
    check("  只保留一行：动作齐全（机器人 + 窗口 + 全局）",
          {"action_start_bot", "action_stop_bot", "action_restart_bot",
           "action_open_all_windows", "action_bot_list", "action_new_bot",
           "action_edit_bot", "action_open_config",
           "action_start_all", "action_stop_all"} <= set(bot_actions),
          "实际 {}".format(bot_actions))
    check("  没有重复动作（同一动作只挂一次）",
          len(bot_actions) == len(set(bot_actions)),
          "重复：{}".format(sorted(
              a for a in set(bot_actions) if bot_actions.count(a) > 1)))
    check("  全局动作排在最后（用得最少、影响面最大）",
          bot_actions[-2:] == ["action_start_all", "action_stop_all"],
          "尾部：{}".format(bot_actions[-3:]))'''

CHECKER_OLD2 = '''    check("  默认可见（那一排操作按钮，不随左栏显隐）",
          "setVisible(True)" in bb_src)'''

CHECKER_NEW2 = '''    check("  默认可见（唯一一条工具条，不随左栏显隐）",
          "setVisible(True)" in bb_src)'''


def patch(path: Path, pairs, apply: bool) -> int:
    src = path.read_text(encoding="utf-8")
    changed = []
    for old, new, label in pairs:
        if new in src:
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
    if changed and apply:
        path.write_text(src, encoding="utf-8", newline="")
        print("  已写入 {}".format(path.relative_to(ROOT)))
    return 0


def main() -> int:
    apply = "--apply" in sys.argv

    print("main_window.py（自检断言）")
    if patch(MAIN, [(SELFTEST_OLD, SELFTEST_NEW, "自检补「只留一行」断言")], apply):
        return 1

    print("check_bot_tab_bar.py（静态检查器）")
    if patch(
        CHECKER,
        [
            (CHECKER_OLD, CHECKER_NEW, "改成单工具条语义"),
            (CHECKER_OLD2, CHECKER_NEW2, "文案同步"),
        ],
        apply,
    ):
        return 1

    if not apply:
        print("（预览）加 --apply 才会写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
