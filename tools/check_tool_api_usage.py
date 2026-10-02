# -*- coding: utf-8 -*-
"""静态核对：实时测试脚本用到的 ProcessManager / RunningProcess 成员是否真的存在。

为什么需要（真实踩过）：
    `tools/check_restart_force.py` 里写了 `manager.build_manager_key(...)`，
    而 `build_manager_key` 其实是**模块级函数**，不是 ProcessManager 的方法 ——
    脚本一跑就 `AttributeError`。这类错误静态检查一眼就能发现。

用法：
    python tools/check_tool_api_usage.py
"""

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "app" / "process_manager.py"
#: 要核对的脚本 -> 哪些变量名代表哪个类
TARGETS = [
    ("tools/check_restart_force.py", {"manager": "ProcessManager", "entry": "RunningProcess",
                                      "entry2": "RunningProcess"}),
]

failures = []


def class_members(tree, class_name: str):
    """收集某个类的成员名：类级定义 + **方法体里 self.xxx = ... 创建的实例属性**。

    为什么必须包含后者（真实踩过）：`ProcessManager._entries` 是在 `__init__`
    里用 `self._entries = {}` 建的，类体里没有它。只看类体就会把
    `manager._entries` 误判成"不存在的成员"（检查器第一版就误报了）。
    """
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name), None
    )
    if cls is None:
        return set()
    names = set()

    # ① 类体：方法名、注解赋值
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign):
            names.add(ast.unparse(node.target))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)

    # ② 方法体（含 __init__）：self.xxx 的赋值与注解
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self":
                names.add(node.attr)
    return names


def module_functions(tree):
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}

# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    src = MANAGER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    pool = {
        "ProcessManager": class_members(tree, "ProcessManager"),
        "RunningProcess": class_members(tree, "RunningProcess"),
    }
    module_funcs = module_functions(tree)
    print("ProcessManager 成员 {} 个；RunningProcess 成员 {} 个；模块级函数 {} 个".format(
        len(pool["ProcessManager"]), len(pool["RunningProcess"]), len(module_funcs)))
    print()

    for relative, mapping in TARGETS:
        path = ROOT / relative
        if not path.exists():
            print("跳过（不存在）：{}".format(relative))
            continue
        text = path.read_text(encoding="utf-8")
        script = ast.parse(text)
        print("=== {} ===".format(relative))

        used = {}
        for node in ast.walk(script):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in mapping:
                    used.setdefault(mapping[node.value.id], set()).add(node.attr)

        for class_name, attrs in sorted(used.items()):
            missing = sorted(a for a in attrs if a not in pool.get(class_name, set()))
            print("  {} 用到 {} 个成员".format(class_name, len(attrs)))
            for attr in sorted(attrs):
                ok = attr in pool.get(class_name, set())
                print("    .{:<26} {}".format(attr, "OK" if ok else "!! 不存在"))
            if missing:
                failures.append("{}: {}.{}".format(relative, class_name, missing))
        print()

    print("结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
