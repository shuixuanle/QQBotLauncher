# -*- coding: utf-8 -*-
"""跨模块属性引用检查：确认 ``模块.属性`` 在被引用模块里真的存在。

为什么需要它
------------
``py_compile`` 只查语法；``check_names.py`` 只查名字是否可解析。
但 ``theme_tokens.lod_mode(...)`` 这种"名字能解析、属性拼错"的问题，
要到运行时才 AttributeError。本脚本把项目模块的顶层定义（赋值/类/函数/
导入）收集起来，再核对别处对它们的属性访问。

用法
----
    python tools\\check_module_attrs.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parent.parent

#: 本地模块别名 -> 模块文件（相对项目根）
ALIASES: Dict[str, str] = {
    "theme_tokens": "app/ui/theme.py",
    "layout_model": "app/layout.py",
}

failures: List[str] = []


def top_level_names(path: Path) -> Set[str]:
    """模块级定义的名字：赋值 / 注解赋值 / 类 / 函数 / 导入。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for item in target.elts:
                        if isinstance(item, ast.Name):
                            names.add(item.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.If):
            # if TYPE_CHECKING: / try: import ... 里的定义
            for sub in node.body + node.orelse:
                if isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    names.add(sub.target.id)
        elif isinstance(node, ast.Try):
            for sub in node.body + node.orelse + node.finalbody:
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add(alias.asname or alias.name.split(".")[0])
    return names


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    defined = {alias: top_level_names(ROOT / rel) for alias, rel in ALIASES.items()}
    for alias, names in defined.items():
        print("  {} -> {} 个顶层名字（{}）".format(
            alias, len(names), ALIASES[alias]))

    print("")
    referenced: Dict[str, Dict[str, List[int]]] = {alias: {} for alias in ALIASES}
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "check_module_attrs.py":
            continue
        rel = path.relative_to(ROOT)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append("{} 语法错误：{}".format(rel, exc))
            print("  [FAIL] {} 语法错误 {}".format(rel, exc))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                alias = node.value.id
                if alias in ALIASES:
                    referenced[alias].setdefault(node.attr, []).append(node.lineno)

    for alias, used in referenced.items():
        known = defined[alias]
        bad = {name: lines for name, lines in used.items() if name not in known}
        detail = "; ".join(
            "{}@{}".format(name, lines[0]) for name, lines in sorted(bad.items())
        )
        ok = not bad
        print(("  [OK]   " if ok else "  [FAIL] ")
              + "{} 的 {} 个属性引用全部存在".format(alias, len(used))
              + (("  " + detail) if detail else ""))
        if not ok:
            failures.append("{} 属性引用".format(alias))

    print("\n结果：{}".format("全部通过" if not failures else "失败项 = {}".format(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
