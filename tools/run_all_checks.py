# -*- coding: utf-8 -*-
"""一键跑完 tools/ 下所有检查器，**带实时进度**，最后汇总结果。

这些检查器是项目的"回归网"：每条断言都对应一个真机踩过的坑
（窗格作用范围、重启链路、属性误用、QSS 花括号、启动脚本编码……）。
改完代码跑一遍，能挡住大部分低级错误。

为什么会带进度条（真机反馈）
----------------------------
"有几个检查器要跑十几秒，中途屏幕上没动静，看着像跑完了，顺手就把窗口关了 ——
实际还有一堆没跑"。所以：
  · 跑之前先打印"共 N 个 · 单个超时 S 秒"；
  · 正在跑的那个显示 **进度条 + 序号 + 百分比 + 已用时间**，每 0.2 秒原地刷新，
    一眼能看出"还在跑"；
  · 每跑完一个打出 `[ 3/27]` 这样的序号与耗时；
  · 全部跑完后有总耗时、最慢的几项、以及明确的一行"全部跑完"。

用法：
    python tools\\run_all_checks.py            # 跑全部（带进度条）
    python tools\\run_all_checks.py -v         # 额外实时打印每个检查器的完整输出
    python tools\\run_all_checks.py --list     # 只列出会跑哪些
    python tools\\run_all_checks.py --timeout 300   # 改单个检查器的超时（秒）
"""

import os
import subprocess
import sys
import threading
import time
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

#: 进度条宽度（字符）
BAR_WIDTH = 20

#: 状态行最长打印多少字符（超了截断，免得换行把界面搅乱）
STATUS_WIDTH = 78


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


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


def progress_bar(done: int, total: int) -> str:
    """`[####........]` 形式的进度条。"""
    total = max(1, total)
    filled = int(round(BAR_WIDTH * min(done, total) / total))
    return "[" + "#" * filled + "." * (BAR_WIDTH - filled) + "]"


def format_seconds(value: float) -> str:
    """秒数：小于 60 秒显示 12.3s，更大显示 1m23s。"""
    if value < 60:
        return "{:.1f}s".format(value)
    minutes, seconds = divmod(int(round(value)), 60)
    return "{}m{:02d}s".format(minutes, seconds)


class LiveStatus:
    """原地刷新的"正在跑"状态行（不是终端时自动退化成不刷新）。"""

    def __init__(self) -> None:
        self.enabled = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self.last_length = 0

    def update(self, text: str) -> None:
        if not self.enabled:
            return
        line = text[:STATUS_WIDTH].ljust(self.last_length)[:max(STATUS_WIDTH,
                                                                self.last_length)]
        sys.stdout.write("\r" + line)
        sys.stdout.flush()
        self.last_length = len(line)

    def clear(self) -> None:
        """把状态行擦掉（打印正式结果前调用）。"""
        if not self.enabled or not self.last_length:
            return
        sys.stdout.write("\r" + " " * self.last_length + "\r")
        sys.stdout.flush()
        self.last_length = 0


def read_stream(stream, sink: list, verbose: bool) -> None:
    """后台线程：一直读到 EOF，避免管道写满把子进程卡死。"""
    try:
        for line in iter(stream.readline, ""):
            sink.append(line)
            if verbose:
                sys.stdout.write("         | " + line.rstrip("\n") + "\n")
                sys.stdout.flush()
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def run_one(path: Path, index: int, total: int, timeout: int, verbose: bool,
            status: LiveStatus):
    """跑一个检查器，返回 (returncode, 输出, 耗时)。跑的时候有进度显示。"""
    name = path.name
    child = subprocess.Popen(
        [sys.executable, str(path)],
        cwd=str(ROOT), text=True,
        encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=child_env(),
    )
    chunks: list = []
    reader = threading.Thread(
        target=read_stream, args=(child.stdout, chunks, verbose), daemon=True)
    reader.start()

    started = time.time()
    timed_out = False
    while True:
        if child.poll() is not None:
            break
        elapsed = time.time() - started
        if elapsed > timeout:
            timed_out = True
            kill_tree(child.pid)
            break
        percent = int(round(100.0 * (index - 1) / max(1, total)))
        status.update("  {} {:>3}%  [{:>2}/{}] {:<30} 已跑 {:>6}".format(
            progress_bar(index - 1, total), percent, index, total, name,
            format_seconds(elapsed)))
        time.sleep(0.2)

    reader.join(timeout=5)
    elapsed = time.time() - started
    status.clear()
    output = "".join(chunks).strip()
    if timed_out:
        output += "\n!! 超时（{} 秒），已结束该检查器的进程树".format(timeout)
        return -1, output.strip(), elapsed
    return child.returncode, output, elapsed


def main() -> int:
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    timeout = CHECK_TIMEOUT
    if "--timeout" in argv:
        try:
            timeout = max(10, int(argv[argv.index("--timeout") + 1]))
        except (IndexError, ValueError):
            print("!! --timeout 后面要跟秒数，例如 --timeout 300")
            return 2

    scripts = sorted(TOOLS.glob("check_*.py"))
    if not scripts:
        print("!! tools/ 下没有 check_*.py")
        return 1

    if "--list" in argv:
        print("将会运行 {} 个检查器：".format(len(scripts)))
        for path in scripts:
            print("    " + path.name)
        return 0

    pyqt6 = has_pyqt6()
    skipped = [path.name for path in scripts
               if path.name in NEEDS_PYQT6 and not pyqt6]
    total = len(scripts)
    status = LiveStatus()

    print("Python {}".format(sys.version.split()[0]))
    print("PyQt6 可用：{}".format("是" if pyqt6 else "否（相关检查器会跳过）"))
    print("共 {} 个检查器{}，单个超时 {} 秒".format(
        total,
        "" if not skipped else "（其中 {} 个因缺 PyQt6 跳过）".format(len(skipped)),
        timeout))
    print("提示：跑的过程中下面会有一行实时进度（进度条 + 序号 + 已用时间），"
          "全部跑完会打印『全部跑完』。")
    print("=" * 72)

    passed, failed, skipped_list = [], [], []
    durations = []
    for index, path in enumerate(scripts, start=1):
        name = path.name
        if name in NEEDS_PYQT6 and not pyqt6:
            skipped_list.append((name, "需要 PyQt6"))
            print("  [SKIP] [{:>2}/{}] {:<30} 需要 PyQt6".format(index, total, name))
            continue

        returncode, output, elapsed = run_one(
            path, index, total, timeout, verbose, status)
        durations.append((name, elapsed))

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
            print("  [ OK ] [{:>2}/{}] {:<30} {:>7}  {}".format(
                index, total, name, format_seconds(elapsed), summary[:44]))
        else:
            failed.append((name, output))
            print("  [FAIL] [{:>2}/{}] {:<30} {:>7}  {}".format(
                index, total, name, format_seconds(elapsed), summary[:44]))

    print("=" * 72)
    print("通过 {} · 失败 {} · 跳过 {}（共 {} 个，总耗时 {}）".format(
        len(passed), len(failed), len(skipped_list), total,
        format_seconds(sum(value for _name, value in durations))))

    if durations:
        slowest = sorted(durations, key=lambda item: item[1], reverse=True)[:5]
        print("最慢的几项：" + "　".join(
            "{} {}".format(name, format_seconds(value)) for name, value in slowest))

    if failed:
        print("\n失败明细：")
        for name, output in failed:
            print("\n---- {} ----".format(name))
            for line in output.splitlines()[-25:]:
                print("  " + line)

    print("\n全部跑完。" + ("有失败项，见上面明细。" if failed else "可以放心关窗口了。"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
