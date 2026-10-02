# -*- coding: utf-8 -*-
"""给检查器用的公共工具：把 `theme.py` 里**不依赖 Qt** 的部分抠出来执行。

为什么需要它
------------
`theme.py` 顶部 import PyQt6，而检查器要能在"只有标准库"的环境里跑，
所以只能 AST 取源码 + 注入常量，再 exec（`check_branch_qss.py` 最早用的办法）。

这个办法有个反复出现的坑：**函数用到哪个常量，就得一起抠出来**——
一开始少了个 `Optional`（注解求值）→ NameError；
后来又少了个 `UI_ROLES`（颜色角色表）→ NameError。
与其在三个检查器里各写一份、各踩一次，不如集中到这里：

  · 常量：两趟收集。先收字面量（`#rrggbb`、元组…），
    再收"引用了别的常量"的那些（例如 `LIGHT_ROLES = {"window": LIGHT_WINDOW, …}`）；
  · 函数：按名字抠源码，按依赖顺序拼在一起；
  · 桩：`is_dark()` 可切换（`force_dark`）、`_CUSTOM` 空表、
    `QWidget` 之类的类型名（`from __future__ import annotations` 之后不求值）。

用法：
    from _theme_probe import load_theme_namespace
    ns = load_theme_namespace(("parse_palette_text", "role_color"))
    ns["role_color"]("base", dark=False)
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "app" / "ui" / "theme.py"

#: 模块级常量名（全大写）
CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: 由 PREAMBLE 提供的桩：这些名字**不要**从 theme.py 里抠（真实现依赖 PyQt6）
PROVIDED = {"is_dark", "_CUSTOM"}

#: 注入的桩：这些名字在真模块里由 PyQt6 或模块状态提供
PREAMBLE = '''\
from __future__ import annotations
import json
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple


class QWidget:            # 只用于注解（future import 之后其实不求值）
    pass


_FORCE_DARK = [False]


def is_dark(widget=None):
    """真模块里读调色板/显式模式；这里由 force_dark 控制。"""
    return bool(_FORCE_DARK[0])


_CUSTOM = {}
'''


def load_theme_namespace(functions=(), theme_path=None) -> dict:
    """返回一个可以直接调用这些函数的命名空间。

    `functions` 只需要写"你想直接用的那几个"：它会顺着调用关系把**同模块里
    被用到的函数**一起抠出来（`role_color` → `builtin_role_color` 这种），
    免得再出现"少抠一个就 NameError"的老问题。
    预置桩（`is_dark` 等）不参与自动收集，避免把真实现拉进来。
    """
    path = Path(theme_path) if theme_path else THEME
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_functions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    wanted = {name for name in (functions or ()) if name not in PROVIDED}

    # 顺着调用把依赖的函数收全（只收同模块的、且不是预置桩提供的那几个）
    while True:
        added = set()
        for name in wanted:
            node = module_functions.get(name)
            if node is None:
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id in module_functions \
                        and child.id not in wanted and child.id not in PROVIDED:
                    added.add(child.id)
        if not added:
            break
        wanted |= added

    code = PREAMBLE
    # —— 第一趟：能直接字面量取值的常量 ——
    pending = []
    for node in tree.body:
        name, value_node = _assignment(node)
        if not name or value_node is None or not CONSTANT_NAME.match(name):
            continue
        try:
            code += "{} = {!r}\n".format(name, ast.literal_eval(value_node))
        except ValueError:
            pending.append((name, value_node))

    # —— 第二趟：引用了别的常量的那些（例如 LIGHT_ROLES 引用 LIGHT_WINDOW）——
    # 注意要在**已经收好的常量环境里**试，否则 `{False: LIGHT_ROLES, …}` 这种
    # 会因为"单独一个空命名空间里没有 LIGHT_ROLES"被误判成抠不出来。
    for name, value_node in pending:
        segment = ast.get_source_segment(source, value_node) or ""
        candidate = "{} = {}\n".format(name, segment)
        trial: dict = {}
        try:
            exec(code + candidate, trial)
        except Exception:                # noqa: BLE001 - 抠不出来就算了
            continue
        code += candidate

    # —— 函数（按源码顺序，保证定义在前、调用在后）——
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            code += "\n" + (ast.get_source_segment(source, node) or "") + "\n"

    namespace: dict = {}
    exec(code, namespace)                # noqa: S102 - 只执行本项目自己的代码
    return namespace


def _assignment(node):
    """从一条语句里取出 `名字, 值节点`（普通赋值与带注解赋值都算）。"""
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id, node.value
    return "", None


if __name__ == "__main__":
    # 自检：本工具自己也得能用
    ns = load_theme_namespace(("parse_palette_text", "role_color", "ansi_palette",
                               "log_colors_for", "set_custom_colors"))
    print("常量数 =", sum(1 for key in ns if CONSTANT_NAME.match(key)))
    print("浅色 base =", ns["role_color"]("base", dark=False))
    print("深色 base =", ns["role_color"]("base", dark=True))
    ns["set_custom_colors"](light_base="#123456")
    print("设了自定义后 =", ns["role_color"]("base", dark=False))
    assert ns["role_color"]("base", dark=False) == "#123456"
    print("日志四色 =", ns["log_colors_for"](False))
    print("自检通过")
