# -*- coding: utf-8 -*-
"""左栏诊断 2：用真实鼠标事件点机器人行，逐步打印状态变化（v2）。

v1 的两个问题已修正：
  · 用替换方法的方式统计"处理函数是否被调用"无效 —— 信号是用 lambda 连的，
    lambda 里已绑定原函数。现在改为**直接观察状态变化**（currentItem 是否变）。
  · 增加"直接调用处理函数"这一步，用来区分
    「信号没触发」与「处理函数跑错了」。

只读。用法：
    python tools\\diagnostics\\_nav_click_probe.py
"""

import sys

from PyQt6.QtCore import QEventLoop, QPoint, QSettings, Qt, QTimer
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.config import BotConfig  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.main_window import APP_NAME, ORG_NAME, MainWindow  # noqa: E402

try:
    from PyQt6.QtTest import QTest

    HAS_QTEST = True
except ImportError:
    HAS_QTEST = False


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def snapshot(window) -> dict:
    tree = window.nav_tree
    current = tree.currentItem()
    return {
        "current": current.text(0) if current is not None else None,
        "selected": [item.text(0) for item in tree.selectedItems()],
        "program_key": window._nav_selected_program_key(),
        "guard": window._nav_guard,
    }


def show(label: str, snap: dict) -> None:
    print("  {:<22} current = {!r}".format(label, snap["current"]))
    print("  {:<22} selected = {}".format("", snap["selected"]))
    print("  {:<22} program_key = {!r} | guard = {}".format(
        "", snap["program_key"], snap["guard"]))


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = QSettings(ORG_NAME, APP_NAME)
    keep = settings.value(theme_tokens.SETTINGS_THEME_KEY, None)
    config = BotConfig.load("bots_config.json", create_if_missing=False)

    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    wait(400)
    window.toggle_nav(True)
    wait(150)
    for bot in config.bots:
        window.open_bot_tab(bot.id, focus=False)
    wait(200)

    tree = window.nav_tree
    first = config.bots[0]
    bot_item = window._nav_items[first.id]
    primary_key = window._primary_key_for_bot(first.id)

    try:
        print("=" * 74)
        print("① 起始状态（全部机器人窗口已打开、焦点未切换）")
        print("=" * 74)
        show("start", snapshot(window))
        print("  期望主程序 key =", repr(primary_key))
        print("  树里能找到该行 =",
              window._nav_child_item(bot_item, primary_key) is not None)

        print("\n" + "=" * 74)
        print("② 真实鼠标点击机器人行 —— 分两段观察")
        print("=" * 74)
        rect = tree.visualItemRect(bot_item)
        point = QPoint(rect.center().x(), rect.center().y())
        print("  点击位置 =", point, "（行的矩形 {})".format(rect))
        if HAS_QTEST:
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
        else:
            print("  !! 没有 QtTest，退化为 setCurrentItem")
            tree.setCurrentItem(bot_item)

        print("  --- 立即（还没跑事件循环）---")
        show("点击后立刻", snapshot(window))
        wait(250)
        print("  --- 等 250ms 事件循环之后 ---")
        show("稳定后", snapshot(window))

        print("\n" + "=" * 74)
        print("③ 直接调用 _on_nav_current_changed(机器人行) —— 绕开信号")
        print("=" * 74)
        window._on_nav_current_changed(bot_item, None)
        # 注意：处理函数现在把落位动作**延后到事件循环**（避开 Qt 鼠标事件收尾的覆盖），
        # 所以这里必须跑一次事件循环，否则看到的是"还没执行"的中间态。
        wait(200)
        show("直接调用后", snapshot(window))

        print("\n" + "=" * 74)
        print("④ 再直接调用 _select_nav_bot(机器人, 主程序) —— 验证定位函数")
        print("=" * 74)
        ok = window._select_nav_bot(first.id, program_key=primary_key)
        wait(60)
        print("  返回值 =", ok)
        show("定位函数后", snapshot(window))
    finally:
        if keep is None:
            settings.remove(theme_tokens.SETTINGS_THEME_KEY)
        else:
            settings.setValue(theme_tokens.SETTINGS_THEME_KEY, keep)
        settings.sync()
        window._closing = True
        window.close()

    print("\n判读：")
    print("  · ② 「点击后立刻」若 current 仍是程序行 → 点击没改变 currentItem（信号不会发）")
    print("  · ③ 若直接调用能落位 → 处理函数本身没问题，问题在信号/事件；")
    print("    若直接调用也不落位 → 处理函数内部有问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
