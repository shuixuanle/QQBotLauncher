# -*- coding: utf-8 -*-
"""一键跑完 tools/ 下所有静态检查器，并汇总结果。

这些检查器是项目的"回归网"：每条断言都对应一个真机踩过的坑
（窗格作用范围、重启链路、属性误用、QSS 花括号、启动脚本编码……）。
改完代码跑一遍，能挡住大部分低级错误。

用法：
    python tools\\run_all_checks.py            # 跑全部
    python tools\\run_all_checks.py -v         # 额外打印每个检查器的完整输出
    python tools\\run_all_checks.py --list     # 只列出会跑哪些
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

#: 需要 PyQt6 的检查器：没装就跳过（本机没装时不该算失败）
#:   · check_ansi_live.py —— 真建一个日志控件喂 ANSI 日志（2026-10-02 的"闪退"就是它挡的那类）
NEEDS_PYQT6 = {"check_restart_force.py", "check_alloc_console_live.py", "check_ansi_live.py"}

#: 单个检查器的超时（秒）。真机上有几个检查器会真的起进程、开窗口
#: （check_alloc_console_live 会拉起两个真管理器窗口），卡住时不能一直等；
#: 超时后连它拉起的**整棵进程树**一起收掉，免得在桌面上留窗口。
CHECK_TIMEOUT = 180


def kill_tree(pid) -> bool:
    """按 PID 结束整棵进程树（Windows：taskkill /T /F）。"""
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


def has_pyqt6() -> bool:
    try:
        import PyQt6  # noqa: F401

        return True
    except ImportError:
        return False


def child_env() -> dict:
    """给子进程的环境：**强制 UTF-8 输出**。

    为什么必须写明（真机踩过 2026-10-02）：中文 Windows 的控制台是 cp936，
    子进程默认按 cp936 输出，而这里按 UTF-8 解码 —— 汇总行全是乱码；
    更糟的是子进程只要打印一个 cp936 编不了的符号（▸ / ⇄ / ✓）就直接崩，
    报出来还是 UnicodeEncodeError，看着像"检查器坏了"。
    统一成 UTF-8 + errors=replace：两边永远对得上，也不会再崩在输出上。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8:replace"
    env["PYTHONUTF8"] = "1"
    return env


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    scripts = sorted(TOOLS.glob("check_*.py"))
    if not scripts:
        print("!! tools/ 下没有 check_*.py")
        return 1

    if "--list" in sys.argv:
        print("将会运行 {} 个检查器：".format(len(scripts)))
        for path in scripts:
            print("    " + path.name)
        return 0

    pyqt6 = has_pyqt6()
    print("Python {}".format(sys.version.split()[0]))
    print("PyQt6 可用：{}".format("是" if pyqt6 else "否（相关检查器会跳过）"))
    print("=" * 72)

    passed, failed, skipped = [], [], []
    for path in scripts:
        name = path.name
        if name in NEEDS_PYQT6 and not pyqt6:
            skipped.append((name, "需要 PyQt6"))
            print("  [SKIP] {:<34} 需要 PyQt6".format(name))
            continue

        child = subprocess.Popen(
            [sys.executable, str(path)],
            cwd=str(ROOT), text=True,
            encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=child_env(),
        )
        try:
            output = (child.communicate(timeout=CHECK_TIMEOUT)[0] or "").strip()
            returncode = child.returncode
        except subprocess.TimeoutExpired:
            # 超时：把这只检查器**整棵进程树**收掉。
            # 真机反馈过"检查器跑完还开着几个窗口" —— 根因之一就是超时/早退时
            # 子进程（以及它拉起的独立窗口进程）没人收。
            kill_tree(child.pid)
            output = (child.communicate()[0] or "").strip()
            output += "\n!! 超时（{} 秒），已结束该检查器的进程树".format(CHECK_TIMEOUT)
            returncode = -1
        output = output.strip()
        tail = [ln for ln in output.splitlines() if ln.strip()]
        # 汇总行取"最后一行不是进度提示的"那一行：有些检查器（如 check_ansi_live）
        # 末尾会带一句 Qt 的字体告警，直接取最后一行会显示成告警。
        summary = "(无输出)"
        for line in reversed(tail):
            if "qt." in line.lower() or "QFontDatabase" in line or "Note that Qt" in line:
                continue
            summary = line
            break

        if returncode == 0:
            passed.append(name)
            print("  [ OK ] {:<34} {}".format(name, summary[:60]))
        else:
            failed.append((name, output))
            print("  [FAIL] {:<34} {}".format(name, summary[:60]))

        if verbose and output:
            for line in output.splitlines():
                print("         | " + line)

    print("=" * 72)
    print("通过 {} · 失败 {} · 跳过 {}".format(len(passed), len(failed), len(skipped)))
    if failed:
        print("\n失败明细：")
        for name, output in failed:
            print("\n---- {} ----".format(name))
            for line in output.splitlines()[-25:]:
                print("  " + line)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
