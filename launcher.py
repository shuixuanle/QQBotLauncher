# -*- coding: utf-8 -*-
"""双击启动器（无控制台）—— 普通模式与管理模式共用这一份实现。

为什么用 .pyw 而不是 .bat / .vbs：
  · `.pyw` 的注册表关联是 ``"C:\\WINDOWS\\pyw.exe" "%L" %*`` —— **pyw.exe 是 GUI 子系统**，
    双击不会出现任何控制台窗口（`.bat` 必然闪一下，`.vbs` 会被安全软件当成脚本拦截）。
  · `py` 启动器会读脚本首行的 ``#!`` 指定版本，所以两个入口文件分别写
    ``#!python3.14`` / ``#!python3.14 --admin``，不依赖 PATH 顺序。
  · `py -0p` 显示 ``-V:3.14 *``（星号＝默认），所以 ``#!python3.14`` 就是装了
    PyQt6 的那个解释器。

三种启动方式的定位（见 README 的「启动方式」一节）：
  · 日常使用      → 双击 exe（最省事，自带依赖）
  · 无 exe 时     → 双击本启动器对应的 .pyw（零黑框）
  · 排障 / 看输出 → 用 .bat（会显示用了哪个解释器、能看到报错）

本模块只在"直接运行"时执行启动逻辑；被 ``--admin`` 拉起的子进程收到
``--no-elevate``，不会再次提权（防无限套娃）。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

#: 与本文件同级（项目根）
PROJECT_ROOT = Path(__file__).resolve().parent
MAIN_SCRIPT = PROJECT_ROOT / "main.py"


# ---------------------------------------------------------------------------
# 提示框（不能依赖 PyQt6 —— 缺依赖时恰恰要用它报错）
# ---------------------------------------------------------------------------

def show_error(title: str, message: str) -> None:
    """弹一个错误框。优先 tkinter（Python 自带），失败则退回 MessageBoxW。"""
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
        return
    except Exception:  # noqa: BLE001 - 提示框本身绝不能把启动器搞崩
        pass
    try:
        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x10)
    except (AttributeError, OSError):
        print("{}: {}".format(title, message), file=sys.stderr)


def show_info(title: str, message: str) -> None:
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(title, message)
        root.destroy()
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x40)
    except (AttributeError, OSError):
        print("{}: {}".format(title, message), file=sys.stderr)


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    """当前进程是否带管理员令牌。"""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch(self_path: Path, extra_args) -> bool:
    """用 ShellExecuteW 的 runas 动作以管理员身份重新运行某个文件。

    对本启动器（.pyw）有效：系统按 .pyw 的关联用 pyw.exe 启动它 —— 依然无控制台。
    对 main.py 也有效（退回 python.exe 时会有一个控制台窗口，属可接受的降级）。
    成功发起返回 True。
    """
    if os.name != "nt":
        return False
    args = " ".join('"{}"'.format(a) for a in extra_args)
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(self_path), args, str(PROJECT_ROOT), 1
        )
    except (AttributeError, OSError):
        return False
    # ShellExecuteW 返回值 > 32 表示成功
    return int(result) > 32


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def find_python() -> str:
    """返回用来运行 main.py 的解释器（优先同目录 pythonw.exe，其次 PATH）。"""
    if not getattr(sys, "frozen", False):
        exe = sys.executable or ""
        candidate = Path(exe).with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
        if exe:
            return exe
    return "pythonw.exe"


def start_manager(extra_args=()) -> bool:
    """启动 main.py（可带附加参数），返回是否成功发起。"""
    if not MAIN_SCRIPT.exists():
        show_error(
            "QQBot启动管理器",
            "找不到 main.py：\n{}\n\n请把本启动器放在 QQBot启动管理器 目录下。".format(
                MAIN_SCRIPT
            ),
        )
        return False

    interpreter = find_python()
    # CREATE_NO_WINDOW：即使只能用 python.exe，也不弹控制台
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        command = [interpreter, str(MAIN_SCRIPT)] + [str(a) for a in extra_args]
        subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            creationflags=flags,
            close_fds=True,
        )
        return True
    except OSError as exc:
        show_error(
            "启动失败",
            "无法启动管理器：\n{}\n\n解释器：{}\n\n"
            "若提示缺少 PyQt6，请先执行：\n"
            "    {}\\python.exe -m pip install -r requirements.txt".format(
                exc, interpreter, str(PROJECT_ROOT)
            ),
        )
        return False


def write_selftest(want_admin: bool) -> Path:
    """把"这条启动路径能不能走通"写进 launcher_selftest.log。

    为什么需要：`.pyw` 由 pyw.exe 执行，**没有控制台**，出错也看不到。
    双击「启动管理器（普通）.pyw」时会顺带写出这份日志，用于确认：
      · .pyw 关联是否真的走 pyw.exe（无控制台）
      · py 启动器选中的是哪个解释器、有没有 PyQt6
      · 能不能成功拉起 main.py
    """
    import platform
    import traceback

    lines = []
    lines.append("=== QQBot 启动器自测 ===")
    lines.append("时间         : {}".format(__import__("datetime").datetime.now()))
    lines.append("模式         : {}".format("管理员" if want_admin else "普通"))
    lines.append("启动器文件   : {}".format(Path(__file__).resolve()))
    lines.append("项目根       : {}".format(PROJECT_ROOT))
    lines.append("main.py 存在 : {}".format(MAIN_SCRIPT.exists()))
    lines.append("sys.executable = {}".format(sys.executable))
    lines.append("是否 frozen  = {}".format(getattr(sys, "frozen", False)))
    lines.append("是否管理员   = {}".format(is_admin()))
    lines.append("Python 版本  = {}".format(platform.python_version()))
    lines.append("项目路径含非 ASCII = {}".format(
        any(ord(ch) > 127 for ch in str(PROJECT_ROOT))))
    try:
        import PyQt6  # noqa: F401

        lines.append("PyQt6        = 有")
    except ImportError as exc:
        lines.append("PyQt6        = 没有（{}）".format(exc))
    lines.append("可用解释器   = {}".format(find_python()))
    try:
        completed = subprocess.run(
            [find_python(), "-c", "import PyQt6"],
            capture_output=True, text=True, timeout=30,
        )
        lines.append("候选解释器 import PyQt6 退出码 = {}".format(completed.returncode))
        if completed.stderr.strip():
            lines.append("  stderr: " + completed.stderr.strip()[:500])
    except (OSError, subprocess.SubprocessError) as exc:
        lines.append("候选解释器测试失败：{}".format(exc))
    lines.append("")
    lines.append("=== 最近一次异常（若有）===")
    lines.append(traceback.format_exc() if sys.exc_info()[0] else "（无）")

    path = PROJECT_ROOT / "launcher_selftest.log"
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return path


def main(want_admin: bool) -> int:
    if os.name != "nt":
        show_error("QQBot启动管理器", "本启动器只支持 Windows。")
        return 2

    if "--selftest" in sys.argv:
        path = write_selftest(want_admin)
        show_info(
            "启动器自测完成",
            "诊断已写入：\n{}\n\n"
            "如果这里能弹出窗口，说明 .pyw 这条路可用（无控制台）。".format(path),
        )
        return 0

    #: 需要提权时给 main.py 传的标志（main.py 会重新以管理员身份拉起自己）
    elevate_flag = "--elevate"

    if want_admin and "--no-elevate" not in sys.argv and not is_admin():
        # 提权：重新运行"本启动器自己"（.pyw 关联 → pyw.exe → 无控制台）
        self_path = Path(__file__).resolve()
        # 再以管理员身份跑一次本启动器，并把 --elevate 传给 main.py ——
        # 这样"用哪个解释器、要不要提权"都收敛在 main.py 一处实现。
        if relaunch(self_path, ["--no-elevate", elevate_flag]):
            return 0
        # 提权失败（用户在 UAC 上点了「否」）：退回普通模式，但明确告知
        show_info(
            "未提权",
            "没有获得管理员权限（UAC 被取消）。\n\n"
            "管理器将以普通权限启动：需要提权的程序（如消防栓）"
            "在启动时会各自弹一次 UAC。",
        )

    extra = (elevate_flag,) if want_admin else ()
    return 0 if start_manager(extra) else 3


if __name__ == "__main__":
    # `--admin` 由「启动管理器（管理员）.pyw」的首行 shebang 传入
    raise SystemExit(main("--admin" in sys.argv))
