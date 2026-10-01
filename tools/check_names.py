# -*- coding: utf-8 -*-
"""全项目"未定义全局名"静态检查。

背景
----
之前只检查 ``self.xxx()`` 这种属性调用，抓不到裸名字的问题，
例如 ``pyqtSignal(str)`` 忘了 import —— py_compile 也发现不了（只查语法），
只有运行到那一行才报 NameError。这个脚本用 AST 收集每个模块的
"定义 + 导入"集合，再逐一看每个 Name 是否可解析。

用法
----
    python tools\\check_names.py            # 检查整个项目
    python tools\\check_names.py app\\ui\\main_window.py
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

#: 项目根目录（tools/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent

#: 允许使用但不属于 builtins 的魔术名
MAGIC_NAMES = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__debug__", "__annotations__", "__class__", "__dict__",
    "__module__", "__qualname__", "__path__", "__all__", "self", "cls",
    "WindowsError",  # py3 里不存在，但有些兼容代码会 try/except 它
}

#: Python 3.12+ 才有的 builtins（老版本上不该直接用）
SOFT_BUILTINS = {"aiter", "anext", "ExceptionGroup", "BaseExceptionGroup"}

failures: List[str] = []
warnings: List[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not ok:
        failures.append(label)


def warn(label: str, detail: str = "") -> None:
    print("  [WARN] " + label + (("  " + detail) if detail else ""))
    warnings.append(label)


def module_definitions(tree: ast.Module) -> Set[str]:
    """收集模块级"定义 + 导入"的名字（含函数/类内部的 import 与 def）。"""
    names: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    names.add("*")  # 星号导入：放弃精确判断
                else:
                    names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
    return names


def used_names(tree: ast.Module) -> List[Tuple[str, int]]:
    """收集所有"读取"位置的名字（Load 上下文）。"""
    used: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.append((node.id, node.lineno))
    return used


def check_file(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = module_definitions(tree)
    star_import = "*" in defined
    builtin_names = set(dir(builtins)) - SOFT_BUILTINS
    known = defined | builtin_names | MAGIC_NAMES

    missing: Dict[str, List[int]] = {}
    for name, lineno in used_names(tree):
        if name in known:
            continue
        missing.setdefault(name, []).append(lineno)

    rel = path.relative_to(ROOT)
    detail = ""
    if missing:
        detail = "; ".join(
            "{}@{}".format(name, lines[0]) for name, lines in sorted(missing.items())
        )
    check("{} 名字可解析{}".format(rel, "（有星号导入，结果可能不精确）" if star_import else ""),
          not missing, detail)

    # 额外提示：input() 会卡住事件循环
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "input":
            warn("{}:{} 使用了 input()（GUI 程序里会卡住）".format(rel, node.lineno))


def check_encoding(path: Path) -> None:
    """所有 open() 都应显式指定编码（Windows 默认 GBK，中文日志会乱码）。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(ROOT)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        keywords = {kw.arg for kw in node.keywords}
        if "encoding" not in keywords and "b" not in str(
                ast.get_source_segment(source, node) or ""
        ):
            warn("{}:{} open() 未指定 encoding".format(rel, node.lineno))


def main(argv: List[str]) -> int:
    if argv:
        targets = [Path(item) for item in argv]
    else:
        targets = sorted(
            p for p in ROOT.rglob("*.py")
            if "__pycache__" not in p.parts and "build" not in p.parts
            and "dist" not in p.parts
        )

    print("检查 {} 个文件：\n".format(len(targets)))
    for path in targets:
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            check("{} 存在".format(path), False, "文件不存在")
            continue
        check_file(path)
        check_encoding(path)

    print("\n结果：{}".format("全部通过" if not failures else "失败项 = {}".format(failures)))
    if warnings:
        print("提示（非失败）：")
        for item in warnings:
            print("  - " + item)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
