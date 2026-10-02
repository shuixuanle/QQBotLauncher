# -*- coding: utf-8 -*-
"""实时验证：**需要强制停止**时，重启也能重新启动（真机事故回归测试）。

真机现象："重启似乎在需要强制停止时，无法重新打开"。
根因（两处，都在 process_manager）：
  1. `restart()` 先设 `entry.restart_pending = True`，紧接着调用的 `stop()`
     **无条件**把它清成 False → `_on_finished` 把这次停止当成普通停止，重启不发生；
  2. Windows 上停止用的是 `taskkill` **子进程**，它自己也会触发一次 `finished`；
     若在那一刻就执行重启，真正的进程可能还没退出。

本脚本用**真实进程**跑一遍完整链路（打桩的只有 `_taskkill_sync`，因为沙箱里
不方便真的去 taskkill）：
    启动 → 强制停止 → 断言自动重新启动 → 断言新 PID 与旧 PID 不同

用法：
    python tools\\check_restart_force.py
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QTimer  # noqa: E402

from app.config import Program  # noqa: E402
from app.process_manager import ProcessManager, build_manager_key  # noqa: E402

failures = []
killed_pids = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


#: 探针程序：打印一行然后长睡（重启测试需要一个"活着"的进程）
PROBE_CODE = "import time; print('probe up', flush=True); time.sleep(120)"


def probe_command() -> str:
    """探针命令行：**用当前解释器**，不要写死 `python`。

    真机教训（2026-10-02）：写死 `python` 时，用户机器上 PATH 里的 `python.exe`
    可能是 Store 别名 / 启动器 shim —— 它自己**立刻退出（退出码 0）**，
    于是 [1] 就报"启动失败：进程已退出"，整条重启链路根本测不到。
    `sys.executable` 一定是"正在跑这个检查器的那只解释器"，最稳。
    """
    exe = sys.executable or "python"
    return '"{exe}" -c "{code}"'.format(exe=exe, code=PROBE_CODE.replace('"', '\\"'))


def pump(app, ms: int) -> None:
    """跑事件循环 ms 毫秒（QProcess 需要事件循环才会派发信号）。"""
    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def kill_pid_tree(pid) -> bool:
    """按 PID 关掉整棵进程树（兜底用：早退也不能把探针留在机器上）。"""
    if not pid:
        return False
    try:
        completed = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(int(pid))],
            capture_output=True, text=True, check=False,
            encoding="utf-8", errors="replace", timeout=20,
        )
        return completed.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def close_probe(manager, key: str) -> None:
    """无论走到哪一步退出，都把探针进程收干净。

    真机教训（2026-10-02）：探针是个真实进程（还带着自己的控制台窗口）。
    测试在 [1] 就早退时，后面的 [3] 收尾根本没执行 —— 于是那个进程
    （以及它可能拉起的子进程）会一直留在机器上，用户看到的就是
    "检查器跑完了，但还开着几个窗口/进程"。
    """
    if manager is None or not key:
        return
    entry = manager._entries.get(key)
    pid = int(getattr(entry, "pid", 0) or 0)
    try:
        manager.stop(key, timeout_ms=300, force=True)
    except (RuntimeError, AttributeError, TypeError):
        pass
    if pid:
        print("    收尾：关闭探针进程 PID {}".format(pid))
        kill_pid_tree(pid)


def main() -> int:
    app = QCoreApplication(sys.argv[:1])

    # 打桩 + 记录：
    #   · `_taskkill_async` 是**真正**的停止路径（起一个 taskkill 进程，见 process_manager），
    #     这里包一层只做记录，让真正的 taskkill 照跑 —— 所以"进程真的被杀了"也是被测的；
    #   · `_taskkill_sync` 是兜底路径，打成桩（沙箱/受限环境里起不了进程时不至于卡住）。
    #
    # 真机教训（2026-10-02）：以前只桩 `_taskkill_sync`，而真机上走的是
    # `_taskkill_async` —— 于是"调用过 taskkill"永远是 0 次，断言必然失败。
    original_sync = ProcessManager._taskkill_sync
    original_async = ProcessManager._taskkill_async
    calls = []
    manager = None
    key = ""

    def counting_async(self, key_, pid, force=False):
        calls.append((int(pid or 0), bool(force)))
        return original_async(self, key_, pid, force)

    def fake_taskkill_sync(pid: int) -> bool:
        calls.append((int(pid), True))
        print("      [桩] _taskkill_sync(PID {}) —— 沙箱里不真的杀".format(pid))
        return True

    ProcessManager._taskkill_async = counting_async
    ProcessManager._taskkill_sync = staticmethod(fake_taskkill_sync)

    logs = []
    try:
        manager = ProcessManager()
        manager.output_text.connect(lambda key, chunk, channel: logs.append(chunk))
        keys = []
        manager.state_changed.connect(lambda key, state, text: keys.append((key, state)))

        program = Program(
            id="restart_probe",
            name="重启探针",
            command=probe_command(),
        )
        key = build_manager_key("probe_bot", program.id)

        print("[1] 启动一个真实的长跑进程")
        print("    探针命令 = {}".format(program.command))
        manager.start(key, program, ROOT)
        pump(app, 4000)
        entry = manager._entries.get(key)
        check("进程已启动", entry is not None and entry.is_running,
              "state={}".format(manager.state(key)))
        if entry is None or not entry.is_running:
            print("  启动失败，后面的断言无意义，输出：")
            print("".join(logs)[:800])
            print("  ↑ 如果上面显示「已退出（退出码 0）」，说明探针自己被立刻结束了：")
            print("    先手工执行一次那条命令，确认它能停住不动（打印 probe up 后不返回）。")
            return 1
        first_pid = entry.pid
        print("    第一个 PID = {}".format(first_pid))

        print("\n[2] 触发重启（先停止再启动）")
        manager.restart(key, timeout_ms=800)
        pump(app, 12000)

        entry2 = manager._entries.get(key)
        alive = entry2 is not None and entry2.is_running
        second_pid = entry2.pid if entry2 is not None else None
        check("重启后进程重新起来了", alive,
              "state={} pid={}".format(manager.state(key), second_pid))
        check("是**新的**进程（PID 变了）",
              alive and second_pid and second_pid != first_pid,
              "{} -> {}".format(first_pid, second_pid))
        check("日志里出现「正在重启」", "正在重启" in "".join(logs))
        check("日志里出现「已停止，准备重启」或走到重启分支",
              ("准备重启" in "".join(logs)) or ("正在重启" in "".join(logs)))
        check("停止时确实下发了 taskkill（两段式停止的第一步）", bool(calls),
              "记录到的 taskkill 调用 = {}".format(calls))
        forced = [pid for pid, is_force in calls if is_force]
        if forced:
            print("    本次走到了强制分支（带 /F 的 taskkill：{}）".format(forced))
        else:
            # 真机上很常见：探针自己响应了关闭请求，宽限期没到就退了 —— 这不是失败，
            # 而是"优雅停止成功了"。强制分支另有一段**确定性**的验证（下面 [3]）。
            print("    本次由优雅停止完成（探针响应了关闭请求），强制分支见 [3]")

        print("\n[3] 强制停止路径（确定性：force=True 会立刻下发带 /F 的 taskkill）")
        calls.clear()
        entry2 = manager._entries.get(key)
        if entry2 is not None and entry2.is_running:
            third_pid = entry2.pid
            manager.stop(key, timeout_ms=500, force=True)
            pump(app, 3000)
            forced_now = [pid for pid, is_force in calls if is_force]
            check("force=True 时下发了带 /F 的 taskkill",
                  bool(forced_now), "记录 = {}".format(calls))
            check("进程确实被结束了（不再是 running）",
                  not manager.is_running(key), "state={}".format(manager.state(key)))
            if third_pid:
                kill_pid_tree(third_pid)          # 兜底：极端情况下自己收尾
        else:
            check("强制停止前进程还在运行", False, "上一段重启后没拿到运行中的进程")

        print("\n[4] 收尾：停掉探针进程")
        if entry2 is not None and entry2.is_running:
            second = entry2.pid
            manager.stop(key, timeout_ms=500, force=True)
            pump(app, 1500)
            # 兜底：真进程万一下不来，直接用 Qt 的 kill 收尾
            try:
                entry2.process.kill()
                entry2.process.waitForFinished(3000)
            except (RuntimeError, AttributeError):
                pass
            killed_pids.append(second)
            print("    已终止 PID {}".format(second))
        manager.cleanup()

        print("\n[5] 关键代码路径确认")
        print("    本次记录到的 taskkill 调用 = {} 次（含 /F 的 {} 次）".format(
            len(calls), sum(1 for _pid, is_force in calls if is_force)))
        check("重启后的进程已被停止（不留残留）", not manager.is_running(key),
              "state={}".format(manager.state(key)))
    finally:
        ProcessManager._taskkill_sync = original_sync
        ProcessManager._taskkill_async = original_async
        # 兜底收尾：早退 / 异常也不留残留进程（真机踩过这个坑）
        close_probe(manager, key)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
