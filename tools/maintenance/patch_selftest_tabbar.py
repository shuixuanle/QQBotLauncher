# -*- coding: utf-8 -*-
"""一次性维护：给 MainWindow._selftest 插入 [12.z]「折叠左栏后要有可点的标签栏」。

需求原话：
    "关闭左侧栏时，切换 bot 实例似乎只能使用 ctrl+tab，
     建议在取消左侧栏时还原添加左侧栏前上方的类似于浏览器的标签页"

用法：
    python tools\\patch_selftest_tabbar.py            # 预览
    python tools\\patch_selftest_tabbar.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "ui" / "main_window.py"

ANCHOR = "    theme_dialog.close()\n"

BLOCK = '''    print("\\n[12.z] 折叠左栏后要有可点的标签栏（真机需求）")
    # 需求原话："关闭左侧栏时，切换 bot 实例似乎只能使用 ctrl+tab，
    #           建议在取消左侧栏时还原添加左侧栏前上方的类似于浏览器的标签页"
    keep_collapsed = window._settings.value(SETTINGS_NAV_COLLAPSED, None)
    keep_forced = window._settings.value(SETTINGS_BOT_TAB_BAR, None)
    keep_current = window._settings.value(SETTINGS_CURRENT_BOT, None)
    try:
        bar = window.bot_tab_bar
        assert bar is not None, "标签栏应当存在"

        # 打开至少两个机器人窗口，标签栏才有意义
        opened = []
        for bot in config.bots[:2]:
            window.open_bot_tab(bot.id, focus=False)
            opened.append(bot.id)
        window.toggle_nav(True)
        app.processEvents()
        print("    已打开窗口 {} 个，标签数 {}，标签栏可见 = {}".format(
            len(window._tabs), bar.count(), bar.isVisible()))
        assert bar.count() == len(window._tabs), "标签数应等于已打开窗口数"
        assert not bar.isVisible(), "左栏展开且未手动勾选时，标签栏应当隐藏"
        texts = [bar.tabText(i) for i in range(bar.count())]
        data = [bar.tabData(i) for i in range(bar.count())]
        assert all(data), "每个标签都要带上 bot_id（用于切换）"
        print("    标签文字 = {}　| 每个标签都带 bot_id = True".format(texts))

        # ① 折叠左栏 → 标签栏自动出现
        window.toggle_nav(False)
        app.processEvents()
        print("    折叠左栏后：标签栏可见 = {}".format(bar.isVisible()))
        assert bar.isVisible(), "折叠左栏后标签栏必须自动出现"

        # ② 点标签 → 切到对应机器人（等价于左栏点它）
        target_bot = opened[-1]
        target_tab = window._tabs[target_bot]
        index = [i for i in range(bar.count()) if bar.tabData(i) == target_bot][0]
        window.show_bot_view(opened[0], focus=True)
        app.processEvents()
        assert window.stack.currentWidget() is window._tabs[opened[0]]
        bar.setCurrentIndex(index)
        app.processEvents()
        current = window.stack.currentWidget()
        print("    点标签 #{} → 当前页面 = {}（期望 {}）".format(
            index, getattr(current, "bot_id", "?"), target_bot))
        assert current is target_tab, "点标签应当切到那个机器人的窗口"
        # 高亮也要跟着落到那个机器人的焦点程序行（与左栏单击一致）
        expected_key = (
            target_tab.focused_program() or window._primary_key_for_bot(target_bot)
        )
        marker_now = window.selected_nav_key()
        print("    高亮标记 = {!r}（期望 {!r}）".format(marker_now, expected_key))
        assert marker_now == expected_key, "点标签后 ▸ 应落在该机器人的程序行"

        # ③ 展开左栏 → 标签栏按"是否手动勾选"决定
        window.toggle_nav(True)
        app.processEvents()
        assert not window.bot_tab_bar.isVisible(), "展开后未勾选时应隐藏"
        forced = window.toggle_bot_tab_bar(True)   # 手动显示
        app.processEvents()
        print("    手动勾选「显示标签栏」→ 返回 {}，可见 = {}".format(
            forced, bar.isVisible()))
        assert forced is True and bar.isVisible(), "手动勾选后应显示"
        assert settings_bool(
            window._settings.value(SETTINGS_BOT_TAB_BAR, False), False
        ), "偏好应当写进注册表"
        window.toggle_bot_tab_bar(False)
        app.processEvents()
        assert not bar.isVisible(), "取消勾选后应隐藏"

        # ④ 关闭一个窗口 → 标签同步减少
        before_count = bar.count()
        window.close_bot_window(opened[-1], confirm=False)
        app.processEvents()
        print("    关闭一个窗口：标签 {} → {}".format(before_count, bar.count()))
        assert bar.count() == before_count - 1, "关窗后标签数量要跟着减少"
    finally:
        for key, value in (
            (SETTINGS_NAV_COLLAPSED, keep_collapsed),
            (SETTINGS_BOT_TAB_BAR, keep_forced),
            (SETTINGS_CURRENT_BOT, keep_current),
        ):
            if value is None:
                window._settings.remove(key)
            else:
                window._settings.setValue(key, value)
        window._settings.sync()
        window.toggle_bot_tab_bar(False)
        window.toggle_nav(True)
        app.processEvents()

'''


def main() -> int:
    apply = "--apply" in sys.argv
    src = TARGET.read_text(encoding="utf-8")

    if "[12.z] 折叠左栏后要有可点的标签栏" in src:
        print("已经插入过 [12.z]，跳过。")
        return 0
    if src.count(ANCHOR) != 1:
        print("!! 锚点不唯一（出现 {} 次）：{!r}".format(src.count(ANCHOR), ANCHOR))
        return 1

    src = src.replace(ANCHOR, BLOCK + ANCHOR, 1)
    print("将插入 [12.z]（{} 行）".format(len(BLOCK.splitlines())))
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    TARGET.write_text(src, encoding="utf-8", newline="")
    print("已写入 {}".format(TARGET.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
