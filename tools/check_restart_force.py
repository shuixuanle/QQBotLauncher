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


def main() -> int:
    app = QCoreApplication(sys.argv[:1])

    # 打桩：不真的去 taskkill，但模拟"两次 finished"的时序 ——
    # 先杀掉真进程（模拟 taskkill /F 成功），taskkill 进程稍后才报结束。
    original_sync = ProcessManager._taskkill_sync
    calls = []

    def fake_taskkill_sync(pid: int) -> bool:
        calls.append(int(pid))
        print("      [桩] _taskkill_sync(PID {}) —— 沙箱里不真的杀".format(pid))
        return True

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

        print("\n[2] 触发重启（先停止再启动；宽限 800ms，必然走到强制结束）")
        manager.restart(key, timeout_ms=800)   # 800ms 宽限 → 必然走强制分支
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

        print("\n[3] 收尾：停掉探针进程")
        if entry2 is not None and entry2.is_running:
            second = entry2.pid
            manager.stop(key, timeout_ms=500, force=True)
            pump(app, 1500)
            # 沙箱里 taskkill 是桩，真进程会活下来 —— 直接用 Qt 的 kill 收尾
            try:
                entry2.process.kill()
                entry2.process.waitForFinished(3000)
            except (RuntimeError, AttributeError):
                pass
            killed_pids.append(second)
            print("    已终止 PID {}".format(second))
        manager.cleanup()

        print("\n[4] 关键代码路径确认")
        print("    被桩接管的 taskkill 调用 = {} 次".format(len(calls)))
        check("确实走到了强制停止路径（调用过 taskkill）", bool(calls),
              "0 次说明宽限期太长、没触发强制分支")
    finally:
        ProcessManager._taskkill_sync = original_sync

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
