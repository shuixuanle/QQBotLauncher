# -*- coding: utf-8 -*-
"""静态检查：同一个模块 / 同一个类里，**不要有重名定义**。

为什么要这个检查器（真机事故，2026-10-02）
--------------------------------------------
给日志区加 ANSI 颜色时，我在 `theme.py` 里补了一个

    def log_colors(widget=None) -> Tuple[str, str]:      # 我以为没有这个函数
        return LOG_LIGHT_BG, LOG_LIGHT_TEXT

而文件**下面**早就有一个同名函数（返回四色 `(底色, 文字, 边框, 选中色)`）。
Python 不报错，后定义的把先定义的**静默覆盖**掉 —— 于是我在
`program_widget._format_for()` 里写的

    base_bg, base_fg = theme_tokens.log_colors(self)

拿到 4 个值 → `ValueError: too many values to unpack (expected 2, got 4)`。
异常发生在 **Qt 信号槽**（`output_text` → `append_log`）里，
PyQt6 对槽里的未捕获异常会直接终止进程 —— 用户看到的就是"启动实例后闪退"。

这一类坑的特点：**语法对、名字对、静态检查全绿，只有真跑起来才炸**，
而且破坏力最大（整个程序退出）。所以单独加一条断言。

检查范围（只看**模块体 / 类体的直接子节点**）：
  · 模块级重复的 `def` / `class` / 简单赋值；
  · 类里重复的方法名（后一个覆盖前一个）。
放在 `if` / `try` 分支里的定义**不算重复**（按平台二选一是常见写法）。

用法：
    python tools\\check_duplicate_defs.py
"""

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent

#: 要扫的目录 / 文件（项目自己的代码，不含第三方）
TARGETS = (
    ROOT / "app",
    ROOT / "main.py",
    ROOT / "launcher.py",
)

#: 允许重复的赋值名（这些名字在项目里本来就会分几次拼装）
ASSIGN_WHITELIST = {"__all__"}


def iter_python_files():
    for target in TARGETS:
        if target.is_file() and target.suffix == ".py":
            yield target
        elif target.is_dir():
            for path in sorted(target.rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                yield path


def defined_names(body) -> dict:
    """收集一段 body（模块体或类体）里定义的**直接子节点**名字 -> 行号。"""
    found = {}
    for node in body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [(node.name, node.lineno)]
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in ASSIGN_WHITELIST:
                    names.append((target.id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id not in ASSIGN_WHITELIST:
                names = [(node.target.id, node.lineno)]
        for name, lineno in names:
            found.setdefault(name, []).append(lineno)
    return found

# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    failures = []
    scanned = 0

    for path in iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            print("  [FAIL] {} 语法错误：{}".format(path.name, exc))
            failures.append(str(path))
            continue
        scanned += 1
        rel = path.relative_to(ROOT)
        problems = []

        for name, lines in sorted(defined_names(tree.body).items()):
            if len(lines) > 1:
                problems.append("模块级 `{}` 定义了 {} 次（行 {}）".format(
                    name, len(lines), "、".join(str(n) for n in lines)))

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for name, lines in sorted(defined_names(node.body).items()):
                if len(lines) > 1 and name != "__init__":
                    problems.append("类 {} 的方法 `{}` 定义了 {} 次（行 {}）".format(
                        node.name, name, len(lines), "、".join(str(n) for n in lines)))

        if problems:
            print("  [FAIL] {}".format(rel))
            for item in problems:
                print("         {}".format(item))
            failures.append(str(rel))
        else:
            print("  [OK]   {}（{} 个顶层定义）".format(
                rel, len(defined_names(tree.body))))

    print("\n扫描文件 {} 个".format(scanned))
    print("提示：后定义的会**静默覆盖**先定义的，只有真跑起来才会炸 ——")
    print("      重名请改名（例如 log_colors → log_palette_colors），别指望 Python 提醒你。")
    print("\n结果：", "全部通过" if not failures else "重复定义 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
