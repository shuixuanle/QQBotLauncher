# -*- coding: utf-8 -*-
"""左栏诊断：树里到底选中哪一行 / 箭头在哪 / 各项实际颜色。

按"用户路径"复现：启动 → 打开 ATRI 窗口 → 点机器人行。
只读（结束时恢复主题）。用法：
    python tools\\diagnostics\\_nav_state_dump.py
"""

import re
import sys

from PyQt6.QtCore import QEventLoop, QSettings, QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.main_window import APP_NAME, ORG_NAME, MainWindow, ROLE_NAV_PROGRAM_KEY  # noqa: E402
from app.config import BotConfig  # noqa: E402


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def dump(window, title: str) -> None:
    tree = window.nav_tree
    print("\n" + "=" * 74)
    print("{}　（主题 mode={} scheme={}）".format(
        title, theme_tokens.current_mode(), theme_tokens.current_scheme()))
    print("=" * 74)

    print("① 树的状态")
    print("    currentItem()  =", repr(tree.currentItem().text(0)) if tree.currentItem() else None)
    print("    selectedItems() =", [item.text(0) for item in tree.selectedItems()])
    print("    _nav_selected_program_key() =", repr(window._nav_selected_program_key()))
    print("    _nav_focus_marker_key()     =", repr(window._nav_focus_marker_key()))

    print("\n② 样式表里写的颜色（nav_tree_qss）")
    qss = tree.parent().styleSheet() if tree.parent() else ""
    # 注意：必须用词边界匹配，否则 "color: " 会命中 "background-color: "，
    # 两行就会显示同一个值（这个脚本自己踩过这个坑）。
    for key in ("background-color", "color", "border"):
        match = re.search(r"(?<![\w-]){}:\s*([^;]+);".format(re.escape(key)), qss)
        print("    {:<18} = {}".format(key, match.group(1).strip() if match else "（未找到）"))
    print("    branch 规则       =", "有" if "::branch" in qss else "无")
    print("    箭头 image 规则   =", "有" if "image: url(" in qss else "无")
    print("    样式表长度        =", len(qss))

    print("\n③ 逐项实际颜色（前景笔刷 / 是否展开 / 是否选中）")
    for index in range(min(tree.topLevelItemCount(), 4)):
        bot_item = tree.topLevelItem(index)
        fg = bot_item.foreground(0).color().name()
        print("    [机器人] {:<26} 前景={} 展开={} 选中={} 子项={}".format(
            bot_item.text(0), fg, bot_item.isExpanded(),
            bot_item.isSelected(), bot_item.childCount()))
        for child_index in range(bot_item.childCount()):
            child = bot_item.child(child_index)
            print("        └ {:<40} 前景={} 选中={}".format(
                child.text(0), child.foreground(0).color().name(), child.isSelected()))

    print("\n④ 调色板（列表用哪个角色画文字）")
    for name, widget in (("nav_tree", tree), ("nav_panel", tree.parent())):
        if widget is None:
            continue
        pal = widget.palette()
        print("    {:<10} WindowText={} Text={} Base={} Window={}".format(
            name,
            pal.color(QPalette.ColorRole.WindowText).name(),
            pal.color(QPalette.ColorRole.Text).name(),
            pal.color(QPalette.ColorRole.Base).name(),
            pal.color(QPalette.ColorRole.Window).name(),
        ))
    app_pal = QApplication.instance().palette()
    print("    app        WindowText={} Text={} Base={} Window={}".format(
        app_pal.color(QPalette.ColorRole.WindowText).name(),
        app_pal.color(QPalette.ColorRole.Text).name(),
        app_pal.color(QPalette.ColorRole.Base).name(),
        app_pal.color(QPalette.ColorRole.Window).name(),
    ))


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = QSettings(ORG_NAME, APP_NAME)
    keep = settings.value(theme_tokens.SETTINGS_THEME_KEY, None)

    config = BotConfig.load("bots_config.json", create_if_missing=False)
    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    wait(400)

    try:
        first = config.bots[0]
        window.set_theme_mode(theme_tokens.MODE_LIGHT)
        wait(150)
        # 完全按用户路径：先关掉该机器人的窗口，再点它的机器人行
        window.close_bot_window(first.id, confirm=False)
        wait(150)
        print("\n########## 用户路径：点机器人行（窗口未打开） ##########")
        item = window._nav_items.get(first.id)
        print("点之前：机器人行子项数 =", item.childCount() if item else "无条目")
        window.open_nav_item(item)
        wait(250)
        dump(window, "浅色 · 点机器人行之后")

        window.set_theme_mode(theme_tokens.MODE_DARK)
        wait(250)
        dump(window, "深色 · 切换之后")
    finally:
        if keep is None:
            settings.remove(theme_tokens.SETTINGS_THEME_KEY)
        else:
            settings.setValue(theme_tokens.SETTINGS_THEME_KEY, keep)
        settings.sync()
        window._closing = True
        window.close()

    print("\n判读：")
    print("  · ② 里 background-color/color 是否随主题变化；branch 规则有没有箭头颜色")
    print("  · ③ 里「前景=」若是 #000000 → 有地方把颜色硬刷成黑（与主题无关）")
    print("  · ① 里 selectedItems 与 ▸ 是否同一行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
