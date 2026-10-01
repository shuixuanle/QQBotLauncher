# -*- coding: utf-8 -*-
"""N5 复验：折叠/展开状态跨"重启"还原（含延迟复算后的最终状态）。

会临时改 nav/* 两个键，跑完自动恢复原值。用法：
    python _nav_diag2.py
"""

import sys

from PyQt6.QtCore import QEventLoop, QSettings, QTimer
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.config import BotConfig  # noqa: E402
from app.ui.main_window import (  # noqa: E402
    APP_NAME,
    ORG_NAME,
    SETTINGS_NAV_COLLAPSED,
    SETTINGS_NAV_SPLIT,
    MainWindow,
)


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def state(window) -> str:
    return "collapsed={!r} hidden={!r} sizes={}".format(
        window._nav_collapsed,
        window.nav_panel.isHidden(),
        list(window.central_splitter.sizes()),
    )


def boot(config, label: str):
    """启动一个窗口：构造 → resize → show → 等 400ms（让延迟复算跑完）。"""
    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    wait(400)
    print("    {:<14} {}".format(label, state(window)))
    return window


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = QSettings(ORG_NAME, APP_NAME)

    keep_collapsed = settings.value(SETTINGS_NAV_COLLAPSED, None)
    keep_split = settings.value(SETTINGS_NAV_SPLIT, None)
    print("原有记录：collapsed =", keep_collapsed, "| split =", keep_split)

    config = BotConfig.load("bots_config.json", create_if_missing=False)
    results = {}

    try:
        print("\n=== 场景 1：注册表 collapsed=true → 重开应【折叠】===")
        settings.setValue(SETTINGS_NAV_COLLAPSED, True)
        settings.setValue(SETTINGS_NAV_SPLIT, ["268", "766"])
        settings.sync()
        w1 = boot(config, "启动后")
        results["折叠还原"] = w1._nav_collapsed is True and w1.nav_panel.isHidden() is True
        w1._closing = True
        w1.close()
        wait(100)

        print("\n=== 场景 2：注册表 collapsed=false → 重开应【展开】===")
        settings.setValue(SETTINGS_NAV_COLLAPSED, False)
        settings.sync()
        w2 = boot(config, "启动后")
        sizes2 = list(w2.central_splitter.sizes())
        ok2 = (
            w2._nav_collapsed is False
            and w2.nav_panel.isHidden() is False
            and sizes2
            and sizes2[0] >= 180
        )
        results["展开还原"] = ok2
        print("        期望：collapsed=False hidden=False sizes[0]>=180")
        w2._closing = True
        w2.close()
        wait(100)

        print("\n=== 场景 3：没有记录 → 重开应【默认展开】===")
        settings.remove(SETTINGS_NAV_COLLAPSED)
        settings.remove(SETTINGS_NAV_SPLIT)
        settings.sync()
        w3 = boot(config, "启动后")
        sizes3 = list(w3.central_splitter.sizes())
        results["默认展开"] = (
            w3._nav_collapsed is False
            and w3.nav_panel.isHidden() is False
            and bool(sizes3)
            and sizes3[0] >= 180
        )
        print("        期望：collapsed=False hidden=False sizes[0]>=180")
        w3._closing = True
        w3.close()
        wait(100)

        print("\n=== 场景 4：折叠 → 保存 → 展开 → 保存 → 再重开（往返稳定）===")
        w4 = boot(config, "启动后")
        w4.toggle_nav(False)
        w4._save_settings()
        collapsed_reg = settings.value(SETTINGS_NAV_COLLAPSED, None)
        w4.toggle_nav(True)
        w4._save_settings()
        expanded_reg = settings.value(SETTINGS_NAV_COLLAPSED, None)
        print("    折叠保存后 registry =", repr(collapsed_reg),
              "| 展开保存后 registry =", repr(expanded_reg))
        w4._closing = True
        w4.close()
        wait(100)
        w5 = boot(config, "重开后")
        sizes5 = list(w5.central_splitter.sizes())
        results["往返稳定"] = (
            w5._nav_collapsed is False
            and w5.nav_panel.isHidden() is False
            and bool(sizes5)
            and sizes5[0] >= 180
        )
        w5._closing = True
        w5.close()

        print("\n================ 结果 ================")
        for name, ok in results.items():
            print("    {:<10} {}".format(name, "通过" if ok else "失败"))
        print("总判定：", "全部通过" if all(results.values()) else "有失败项")
    finally:
        if keep_collapsed is None:
            settings.remove(SETTINGS_NAV_COLLAPSED)
        else:
            settings.setValue(SETTINGS_NAV_COLLAPSED, keep_collapsed)
        if keep_split is None:
            settings.remove(SETTINGS_NAV_SPLIT)
        else:
            settings.setValue(SETTINGS_NAV_SPLIT, keep_split)
        settings.sync()
        print("\n（已恢复原有记录：collapsed =",
              settings.value(SETTINGS_NAV_COLLAPSED, None),
              "| split =", settings.value(SETTINGS_NAV_SPLIT, None), "）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
