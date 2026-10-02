# -*- coding: utf-8 -*-
"""静态检查：检查器的**控制台输出**在中文 Windows 上也活得下来。

真机事故（2026-10-02）
----------------------
用户跑 `python tools\\run_all_checks.py`，25 个检查器里有两个报
`UnicodeEncodeError: 'gbk' codec can't encode character '\\u25b8'`：

  · `check_bot_tab_bar.py` 的标签里有个 `▸`（U+25B8）；
  · `check_palette_studio.py` 把菜单里的字符串原样打了出来，里面含 `⇄`（U+21C4）。

中文 Windows 的控制台默认是 **cp936**，编不出这些符号 —— 于是"检查器自己崩在 print 上"。

于是补两条防线：
  [1] **每个检查器都要有控制台兜底**（`sys.stdout.reconfigure(errors="replace")`）：
      编不出来的字符退化成 `?`，绝不中断检查；
  [2] **控制台输出的字面量必须是 GBK 能编码的**：`print(...)` / `check(标签...)`
      里写死的字符串不许出现 GBK 编不了的字符（变量、源码片段不在静态检查范围内，
      那由第 [1] 条兜住）。

用法：
    python tools\\check_console_output.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

#: 打印函数名：这些调用里写死的字符串会直接进控制台
PRINT_CALLS = {"print", "check", "log", "warn"}

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def gbk_safe(text: str) -> bool:
    """这个字符串能不能按 cp936 输出。"""
    try:
        text.encode("gbk")
        return True
    except UnicodeEncodeError:
        return False


def printed_literals(tree: ast.AST):
    """产出 (行号, 字符串)：`print(...)` / `check(...)` 里**写死**的字符串。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name not in PRINT_CALLS:
            continue
        for piece in ast.walk(node):
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                yield piece.lineno, piece.value


def has_console_guard(source: str) -> bool:
    """有没有 stdout 兜底（允许写成 sys.stdout.reconfigure / reconfigure）。"""
    return "stdout.reconfigure" in source


def main() -> int:
    print("[1] 每个检查器都要有控制台兜底（编不出来的字符退化成 ?）")
    scripts = sorted(TOOLS.glob("check_*.py"))
    missing = [path.name for path in scripts
               if not has_console_guard(path.read_text(encoding="utf-8"))]
    check("{} 个检查器都带 stdout 兜底".format(len(scripts)), not missing, str(missing))

    print("\n[2] 控制台输出的字面量必须是 cp936 编得出来的")
    offenders = []
    for path in scripts:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for lineno, text in printed_literals(tree):
            if not gbk_safe(text):
                bad = "".join(ch for ch in text if not gbk_safe(ch))
                offenders.append("{}:{} 含 {}".format(
                    path.name, lineno,
                    " ".join("U+{:04X}".format(ord(ch)) for ch in set(bad))))
    check("没有编不出来的字面量", not offenders, "；".join(offenders[:3]))

    print("\n[3] run_all_checks 给子进程统一了编码")
    runner = (TOOLS / "run_all_checks.py").read_text(encoding="utf-8")
    check("子进程环境设了 PYTHONIOENCODING", "PYTHONIOENCODING" in runner)
    check("按 UTF-8 解码子进程输出", 'encoding="utf-8"' in runner)

    print("\n[3b] run_all_checks 要有实时进度（真机反馈：静默期像跑完了，被提前关掉）")
    check("有进度条函数", "def progress_bar(" in runner)
    check("有原地刷新的状态行（\\r 覆盖）", "def update(" in runner and '\\r' in runner)
    check("结果行带序号（[ 7/27] 这种）", "[{:>2}/{}]" in runner)
    check("开始前打印总数与超时", "共 {} 个检查器" in runner)
    check("结束后打印明确的一行「全部跑完」", "全部跑完" in runner)
    check("汇总里有总耗时与最慢几项", "总耗时" in runner and "最慢的几项" in runner)
    check("用后台线程读子进程输出（否则管道写满会把检查器卡死）",
          "threading" in runner and "read_stream" in runner)
    check("非终端（重定向/CI）时不刷动画", "isatty" in runner)

    print("\n[4] .bat 入口是纯 ASCII（cmd 按 ANSI 读它）")
    bats = sorted(TOOLS.glob("*.bat")) + sorted(ROOT.glob("*.bat"))
    bad_bats = []
    for path in bats:
        try:
            path.read_text(encoding="ascii")
        except (UnicodeDecodeError, OSError):
            bad_bats.append(path.name)
    check("{} 个 .bat 都是 ASCII".format(len(bats)), not bad_bats, str(bad_bats))

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
