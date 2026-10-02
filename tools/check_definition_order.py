# -*- coding: utf-8 -*-
"""静态检查：模块级代码不许"先用后定义"（Python 的经典 import 期 NameError）。

真机事故（2026-10-02）
----------------------
给"整个界面可自定义"加颜色角色表时，`LIGHT_ROLES` 里写了 `"muted": MUTED_LIGHT`，
而 `MUTED_LIGHT` 定义在文件**下面**：

    NameError: name 'MUTED_LIGHT' is not defined. Did you mean: 'MODE_LIGHT'?

`python -m py_compile` 只查语法，全程绿灯；而 `tools/_theme_probe.py`（检查器用来抠
theme.py 源码的工具）当时是**两趟收集常量**的，等于替我们"重排"了定义顺序，
把这个错误悄悄盖住了 —— 于是 main.py / exe 一启动就崩，检查器却全绿。

所以这里补两条防线：

  [1] **真·导入测试**：造一个 PyQt6 替身，把 `theme.py` 之类的模块**按真实顺序**
      跑一遍 import。求值顺序、注解、装饰器全都按真的来，导入期炸了就报出来。
  [2] **静态顺序检查**：模块级语句引用了"后面才定义"的名字就报（函数体内的引用不算，
      因为它们要等调用时才求值）。

用法：
    python tools\\check_definition_order.py
"""

import ast
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 要真导入的模块（导入期用到 PyQt6 的，用替身顶上）。
#: 覆盖"启动即崩"的全部路径：main → main_window → bot_tab → program_widget → theme …
IMPORT_TARGETS = (
    "app/ui/theme.py",
    "app/ansi.py",
    "app/config.py",
    "app/layout.py",
    "app/process_manager.py",
    "app/ui/program_widget.py",
    "app/ui/bot_tab.py",
    "app/ui/main_window.py",
    "app/ui/edit_bot_dialog.py",
    "app/ui/bot_list_dialog.py",
    "app/ui/palette_dialog.py",
    "main.py",
)

#: 要静态查顺序的目录 / 文件
SCAN_TARGETS = (
    ROOT / "app",
    ROOT / "main.py",
    ROOT / "launcher.py",
    ROOT / "tools",
)

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


# ---------------------------------------------------------------------------
# [1] 真·导入测试（PyQt6 替身）
# ---------------------------------------------------------------------------

class _StubMeta(type):
    """替身类的元类：类属性访问也返回替身（`Qt.Orientation.Horizontal` 这种）。

    注意**不能**接管 dunder（`__mro_entries__` 之类）—— 那会让 Python 在
    `class X(QWidget)` 时报 "TypeError: __mro_entries__ must return a tuple"。
    但**算术/比较**得管：Qt 枚举经常参与 `+`、`|`、`==`（真机上就有
    `Qt.AlignmentFlag.X | Qt.AlignmentFlag.Y` 这种），不管就会误报。
    """

    def __getattr__(cls, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _make_stub(name)

    def __add__(cls, other):
        return _make_stub("add")

    def __radd__(cls, other):
        return _make_stub("radd")

    def __sub__(cls, other):
        return _make_stub("sub")

    def __rsub__(cls, other):
        return _make_stub("rsub")

    def __mul__(cls, other):
        return _make_stub("mul")

    def __rmul__(cls, other):
        return _make_stub("rmul")

    def __or__(cls, other):
        return _make_stub("or")

    def __ror__(cls, other):
        return _make_stub("ror")

    def __and__(cls, other):
        return _make_stub("and")

    def __getitem__(cls, item):
        return _make_stub("item")

    def __lt__(cls, other):
        return False

    def __le__(cls, other):
        return True

    def __gt__(cls, other):
        return False

    def __ge__(cls, other):
        return True


class _Anything(metaclass=_StubMeta):
    """什么都能当：属性、调用、比较、四则运算 —— 只为让模块跑完 import。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _make_stub(name)

    def __call__(self, *args, **kwargs):
        return _make_stub("call")

    def __or__(self, other):
        return _make_stub("or")

    def __add__(self, other):
        return _make_stub("add")

    def __radd__(self, other):
        return _make_stub("radd")

    def __sub__(self, other):
        return _make_stub("sub")

    def __rsub__(self, other):
        return _make_stub("rsub")

    def __mul__(self, other):
        return _make_stub("mul")

    def __rmul__(self, other):
        return _make_stub("rmul")

    def __getitem__(self, item):
        return _make_stub("item")

    def __eq__(self, other):
        return True

    def __hash__(self):
        return 0

    def __iter__(self):
        return iter(())

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return True

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return True

    def __int__(self):
        return 0

    def __index__(self):
        return 0


def _make_stub(name: str):
    """造一个可以当基类、可以实例化、可以继续取属性的替身类。"""
    return _StubMeta(name or "Stub", (_Anything,), {})


def _fake_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)

    def __getattr__(attr):
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        return _make_stub(attr)

    module.__getattr__ = __getattr__
    return module


def install_pyqt_stubs() -> bool:
    """没装 PyQt6 时，装一套替身（装了就用真的，更可信）。"""
    try:
        import PyQt6  # noqa: F401

        return False
    except ImportError:
        pass
    for name in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
                 "PyQt6.QtSvg", "PyQt6.sip"):
        sys.modules.setdefault(name, _fake_module(name))
    return True


def run_import_check() -> None:
    stubbed = install_pyqt_stubs()
    print("[1] 真·导入测试（{}）".format("用 PyQt6 替身" if stubbed else "用真 PyQt6"))
    for rel in IMPORT_TARGETS:
        path = ROOT / rel
        if not path.is_file():
            check("{} 存在".format(rel), False, "文件不存在")
            continue
        module_name = "check_import_" + path.stem
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        # 必须先登记进 sys.modules：`@dataclass` 之类会回头查
        # `sys.modules[cls.__module__]`，查不到就报
        # "AttributeError: 'NoneType' object has no attribute '__dict__'"
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            ok, detail = True, ""
        except Exception as exc:  # noqa: BLE001 - 导入期任何异常都要报出来
            ok, detail = False, "{}: {}".format(type(exc).__name__, exc)
        finally:
            sys.modules.pop(module_name, None)
        check("{} 能按真实顺序导入".format(rel), ok, detail)
        if ok and path.name == "theme.py":            # 顺手确认颜色角色表真的存在（这轮加的）
            roles = getattr(module, "UI_ROLES", ())
            check("theme.UI_ROLES 有 {} 个角色".format(len(roles)), len(roles) >= 10,
                  str(len(roles)))
            check("深浅角色表都能取到底色",
                  bool(module.LIGHT_ROLES.get("base")) and bool(module.DARK_ROLES.get("base")))


# ---------------------------------------------------------------------------
# [2] 静态：模块级"先用后定义"
# ---------------------------------------------------------------------------

def first_definitions(tree: ast.AST) -> dict:
    """名字 → 首次出现的行号（赋值、函数、类、import、with/for/except 的目标）。"""
    found = {}

    def note(name: str, line: int) -> None:
        if name and name not in found:
            found[name] = line

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            note(node.name, node.lineno)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            note(node.id, node.lineno)
        elif isinstance(node, ast.alias):
            note((node.asname or node.name).split(".")[0], getattr(node, "lineno", 0))
        elif isinstance(node, ast.arg):
            note(node.arg, node.lineno)
    return found


def module_level_loads(tree: ast.AST):
    """产出 (名字, 行号)：**模块级会执行到**的读取。

    函数体不算（要等调用），但装饰器 / 默认参数 / 类体 / if-else 分支算 ——
    它们在 import 时就求值。
    """
    for node in tree.body:
        stack = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 只保留装饰器与默认参数（这些 import 期就求值），函数体跳过
                for decorator in current.decorator_list:
                    stack.append(decorator)
                for default in list(current.args.defaults) + list(current.args.kw_defaults):
                    if default is not None:
                        stack.append(default)
                continue
            if isinstance(current, ast.Name) and isinstance(current.ctx, ast.Load):
                yield current.id, current.lineno
            stack.extend(ast.iter_child_nodes(current))


def run_order_check() -> None:
    print("\n[2] 静态顺序：模块级语句不许引用「后面才定义」的名字")
    scanned, problems = 0, []
    for target in SCAN_TARGETS:
        paths = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            scanned += 1
            first = first_definitions(tree)
            for name, line in module_level_loads(tree):
                defined_at = first.get(name)
                if defined_at and defined_at > line:
                    problems.append("{}:{} 用了 {}（它定义在第 {} 行）".format(
                        path.relative_to(ROOT), line, name, defined_at))

    check("{} 个文件没有「先用后定义」".format(scanned), not problems,
          "；".join(problems[:4]) + ("…" if len(problems) > 4 else ""))


def main() -> int:
    run_import_check()
    run_order_check()
    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
