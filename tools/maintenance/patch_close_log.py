# -*- coding: utf-8 -*-
"""一次性维护：给 MainWindow.closeEvent 加"关闭流程心跳日志"。

排查真机反馈的"关闭提权管理器时卡顿"。关闭流程里有两次同步等待
（stop_all 3 秒 + cleanup 2 秒），最坏约 5 秒；把每一步带时间戳写进
close_debug.log，就能区分"慢"和"真卡死"。

用法：
    python tools\\patch_close_log.py            # 预览
    python tools\\patch_close_log.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "ui" / "main_window.py"

OLD = '''        self._closing = True
        self._starting_queue = []
        if self._start_timer is not None:
            self._start_timer.stop()

        try:
            self._save_settings()
        except Exception:
            pass

        if self.manager.running_count:
            self.statusBar().showMessage("正在停止所有程序…")
            QApplication.processEvents()
            self.manager.stop_all(timeout_ms=3000, wait=True)

        self.manager.cleanup()
        event.accept()'''

NEW = '''        self._closing = True
        self._starting_queue = []
        if self._start_timer is not None:
            self._start_timer.stop()

        self._close_log("关闭流程开始：running={}".format(running))
        try:
            self._save_settings()
        except Exception:
            pass
        self._close_log("界面状态已保存")

        if self.manager.running_count:
            self.statusBar().showMessage("正在停止所有程序…")
            QApplication.processEvents()
            self.manager.stop_all(timeout_ms=3000, wait=True)
            self._close_log("stop_all 返回，仍在运行={}".format(
                self.manager.running_count))

        self.manager.cleanup()
        self._close_log("cleanup 完成，准备关闭窗口")
        event.accept()

    def _close_log(self, message: str) -> None:
        """记录关闭流程的每一步（排查"关窗口卡住"用）。

        真机反馈过"关闭提权管理器时卡顿"。关闭流程里有两次同步等待
        （stop_all 3 秒 + cleanup 2 秒），最坏约 5 秒；把每一步带上时间戳写进
        close_debug.log，就能区分"只是慢"与"真卡死"。

        默认开启；设环境变量 QQBOT_CLOSE_DEBUG=0 可关闭。
        """
        if os.environ.get("QQBOT_CLOSE_DEBUG", "1") in ("0", "false", "no"):
            return
        try:
            from datetime import datetime

            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with open(Path(PROJECT_ROOT) / "close_debug.log", "a",
                      encoding="utf-8") as handle:
                handle.write("{}  {}\\n".format(stamp, message))
        except (OSError, ValueError, TypeError):
            pass'''


def main() -> int:
    apply = "--apply" in sys.argv
    src = TARGET.read_text(encoding="utf-8")

    if "_close_log" in src:
        print("已经是新版（含 _close_log），无需修改。")
        return 0
    if src.count(OLD) != 1:
        print("!! 找不到唯一匹配的 closeEvent 片段（出现 {} 次）".format(src.count(OLD)))
        print("   可能代码已改动，请手工处理。")
        return 1

    src = src.replace(OLD, NEW)
    print("准备插入：_close_log 方法 + 3 处心跳日志")
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    TARGET.write_text(src, encoding="utf-8", newline="")
    print("已写入 {}".format(TARGET.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
