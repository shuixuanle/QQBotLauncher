# -*- coding: utf-8 -*-
"""N5 终极追踪：谁在什么时候把左栏折叠/展开。

只读（会临时改 nav/collapsed，跑完恢复）。用法：
    python _nav_trace.py
"""

import sys
import traceback

from PyQt6.QtCore import QEvent, QEventLoop, QObject, QSettings, QTimer
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

T0 = [0]


def stamp() -> str:
    return "+{:>6.1f}ms".format(T0[0])


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class VisibilitySpy(QObject):
    """监听 nav_panel 的显示/隐藏事件。"""

    def __init__(self, panel, name):
        super().__init__(panel)
        self.panel = panel
        self.name = name

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show:
            print("    {} [事件] {} 被 Show  | isHidden={} | 调用栈:".format(
                stamp(), self.name, self.panel.isHidden()))
            for line in traceback.format_stack()[-6:-1]:
                print("            " + line.strip().splitlines()[0])
        elif event.type() == QEvent.Type.Hide:
            print("    {} [事件] {} 被 Hide  | 调用栈:".format(stamp(), self.name))
            for line in traceback.format_stack()[-6:-1]:
                print("            " + line.strip().splitlines()[0])
        return False


def boot_traced(config, label, settings):
    print("\n--- {} ---".format(label))
    print("    {} 注册表 collapsed = {!r}".format(
        stamp(), settings.value(SETTINGS_NAV_COLLAPSED, "<空>")))

    original = MainWindow._restore_nav_state

    def traced(self, visible=None):
        raw = self._settings.value(SETTINGS_NAV_COLLAPSED, "<空>")
        hidden_before = self.nav_panel.isHidden() if hasattr(self, "nav_panel") else None
        result = original(self, visible)
        print("    {} _restore_nav_state(传参={!r}) 读到的注册表={!r} → 返回={!r} "
              "| hidden: {}→{} | _nav_collapsed={!r}".format(
                  stamp(), visible, raw, result, hidden_before,
                  self.nav_panel.isHidden(), self._nav_collapsed))
        return result

    MainWindow._restore_nav_state = traced
    try:
        window = MainWindow(config)
        spy = VisibilitySpy(window.nav_panel, "nav_panel")
        window.nav_panel.installEventFilter(spy)
        window.resize(1180, 760)
        window.show()
        wait(500)
        print("    {} 最终：collapsed={!r} hidden={!r} sizes={}".format(
            stamp(), window._nav_collapsed, window.nav_panel.isHidden(),
            list(window.central_splitter.sizes())))
    finally:
        MainWindow._restore_nav_state = original
    return window


def main() -> int:
    app = QApplication(sys.argv[:1])
    settings = QSettings(ORG_NAME, APP_NAME)
    keep = settings.value(SETTINGS_NAV_COLLAPSED, None)
    keep_split = settings.value(SETTINGS_NAV_SPLIT, None)
    config = BotConfig.load("bots_config.json", create_if_missing=False)

    timer = QTimer()
    timer.timeout.connect(lambda: T0.__setitem__(0, T0[0] + 20))
    timer.start(20)

    windows = []
    try:
        settings.setValue(SETTINGS_NAV_COLLAPSED, False)
        settings.sync()
        print("=== A：注册表 collapsed=false，期望展开 ===")
        windows.append(boot_traced(config, "启动（collapsed=false）", settings))
    finally:
        if keep is None:
            settings.remove(SETTINGS_NAV_COLLAPSED)
        else:
            settings.setValue(SETTINGS_NAV_COLLAPSED, keep)
        if keep_split is not None:
            settings.setValue(SETTINGS_NAV_SPLIT, keep_split)
        settings.sync()
        for w in windows:
            w._closing = True
            w.close()
        print("\n（已恢复原记录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
