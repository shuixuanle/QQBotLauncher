#!python3.14
"""双击启动 QQBot 启动管理器（无控制台）。

首行的 `#!python3.14` 是给 Windows `py`/`pyw` 启动器看的：
  · `pyw.exe` 负责执行本文件（无控制台窗口）；
  · `#!python3.14` 指定用 Python 3.14（这台机器上装了 PyQt6 的那个）；
  · 不带 `--admin`：以普通权限启动（需要提权的程序会各自弹 UAC）。
"""


# ---------------------------------------------------------------------------
# 入口：把 launcher.py 以"当前解释器"重新执行（保持 shebang 选定的解释器）
# ---------------------------------------------------------------------------
import runpy, sys as _sys
from pathlib import Path as _Path

_root = _Path(__file__).resolve().parent
_sys.argv = [str(_root / "launcher.py")] + _sys.argv[1:]
runpy.run_path(str(_root / "launcher.py"), run_name="__main__")
