# -*- coding: utf-8 -*-
"""脱离启动探针（check_alloc_console_live.py 的固定装置，请勿删除）。

在子进程里调用 ``main.relaunch_detached`` / ``main.relaunch_with_own_console``，
验证两条"离开原 cmd"的路径：

  · relaunch_detached        → 用 pythonw.exe + DETACHED_PROCESS 启动，**零控制台**
  · relaunch_with_own_console → 用 python.exe + CREATE_NEW_CONSOLE，会多一个控制台窗口

结果写到 ``sys.argv[2]`` 指定的文件 —— 不能靠 stdout：脱离/换控制台之后
stdout 会指向新的控制台（或根本没有），父进程的管道收不到。

用法（一般由 check_alloc_console_live.py 调用）：
    python tools\\_detach_probe.py <项目根> <结果文件> [detached|console]
"""

import ast
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if len(sys.argv) < 3:
    print("用法：python _detach_probe.py <项目根> <结果文件> [detached|console]")
    raise SystemExit(2)

root = Path(sys.argv[1]).resolve()
result_file = Path(sys.argv[2])
mode = sys.argv[3] if len(sys.argv) > 3 else "detached"

src = (root / "main.py").read_text(encoding="utf-8")
tree = ast.parse(src)

# 从 main.py 里抽出真实的实现来跑（避免复制一份导致"测的不是真代码"）
ns = {
    "os": os,
    "sys": sys,
    "shutil": shutil,
    "ctypes": ctypes,
    "subprocess": subprocess,
    "__file__": str(root / "main.py"),
    "GUI_CHILD_ENV": "QQBOT_GUI_CHILD",
}
for name in ("relaunch_detached", "relaunch_with_own_console"):
    fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    exec(ast.get_source_segment(src, fn), ns)

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32


def owner_pid(hwnd):
    """返回某个窗口的宿主进程 ID。"""
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


# GetConsoleWindow 在 kernel32（不在 user32）
before_hwnd = kernel32.GetConsoleWindow()
before_pid = owner_pid(before_hwnd) if before_hwnd else 0

if mode == "console":
    sys.argv = [str(root / "main.py"), "--console"]
    started = ns["relaunch_with_own_console"]()
else:
    sys.argv = [str(root / "main.py"), "--gui"]
    started = ns["relaunch_detached"]()

# 给子进程一点时间真正起来
time.sleep(2)

# 记下**被拉起的那个子进程**的 PID：它是故意脱离父进程独立活的
# （关掉启动它的 cmd 也不受影响），所以必须由调用方精确地把它关掉 ——
# 真机踩过：靠 `taskkill /IM python.exe` 连坐清理时，--gui 拉起的 pythonw.exe
# 活了下来，屏幕上一个管理器窗口一直开着。
child_pid = int(ns.get("LAST_RELAUNCH_PID") or 0)

result_file.write_text(
    json.dumps({
        "self_pid": os.getpid(),
        "before_hwnd": before_hwnd,
        "before_pid": before_pid,
        "started": bool(started),
        "mode": mode,
        "child_pid": child_pid,
    }),
    encoding="utf-8",
)
