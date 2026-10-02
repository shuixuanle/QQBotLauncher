# -*- coding: utf-8 -*-
"""静态检查：@property 有没有被当成方法调用（会抛 TypeError: 'str' object is not callable）。

为什么需要（真实炸过一次）：
    标签栏里写了 `widget.bot_name()`，而 `BotTab.bot_name` 是 `@property` 返回字符串。
    平时编译、check_names、check_method_decorators 全是绿的 —— 因为语法没问题、
    名字也确实存在。只有运行到那一行才炸：

        TypeError: 'str' object is not callable

    结果是"管理器直接打不开日志界面"。这类错误必须用**类型信息**才发现，
    所以这里扫描全部 py 文件：把 `@property` 的方法名收集起来，
    再找 `xxx.<属性名>(` 这种调用形式。

用法：
    python tools\\check_property_calls.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", "build", "dist", ".git", "tools"}
TARGET_DIRS = ["app", "."]

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def collect_properties(tree) -> dict:
    """收集 {类名: {属性名}}（含 @property 与 @cached_property）。"""
    result = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names = set()
        for child in node.body:
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in child.decorator_list:
                text = ast.unparse(deco)
                if text.endswith("property") or text.endswith("cached_property"):
                    names.add(child.name)
        if names:
            result[node.name] = names
    return result


def collect_class_attrs(tree) -> dict:
    """收集 {类名: {普通方法名}}，用于排除"同名但其实是方法"的情况。"""
    result = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {
            c.name for c in node.body
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        result[node.name] = methods
    return result


def iter_files():
    seen = set()
    for base in TARGET_DIRS:
        folder = ROOT if base == "." else ROOT / base
        if not folder.exists():
            continue
        for path in folder.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path in seen:
                continue
            seen.add(path)
            yield path


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    properties = {}   # 属性名 -> [(文件, 行, 类名, 表达式)]
    checked_files = 0

    for path in sorted(iter_files()):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        checked_files += 1

        props = collect_properties(tree)
        methods = collect_class_attrs(tree)
        prop_names = set()
        for names in props.values():
            prop_names |= names
        # 同名既可能是属性（在 A 类）也可能是方法（在 B 类）：只有在**所有**出现的
        # 类里都是属性时，才能断定"调用它一定出错"。
        ambiguous = set()
        for name in prop_names:
            for cls, names in methods.items():
                if name in names and name not in props.get(cls, set()):
                    ambiguous.add(name)
        prop_names -= ambiguous
        if not prop_names:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in prop_names:
                continue
            properties.setdefault(func.attr, []).append(
                (path.relative_to(ROOT), node.lineno, ast.unparse(func))
            )

    print("扫描 {} 个 py 文件".format(checked_files))
    print()
    if properties:
        for name, hits in sorted(properties.items()):
            print("  !! 属性 {} 被当成方法调用：".format(name))
            for rel, line, expr in hits:
                print("       {}:{}  {}".format(rel, line, expr))
            print("     修复：去掉那对括号（{} → {}）".format(name + "()", name))
        failures.append("属性被当方法调用：{}".format(sorted(properties)))
    else:
        check("没有把 @property 当成方法调用", True)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
