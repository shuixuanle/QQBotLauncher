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

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

#: 需要 PyQt6 的检查器：没装就跳过（本机没装时不该算失败）
NEEDS_PYQT6 = {"check_restart_force.py", "check_alloc_console_live.py"}


def has_pyqt6() -> bool:
    try:
        import PyQt6  # noqa: F401

        return True
    except ImportError:
        return False


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

        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=300,
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        tail = [ln for ln in output.splitlines() if ln.strip()]
        summary = tail[-1] if tail else "(无输出)"

        if proc.returncode == 0:
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
