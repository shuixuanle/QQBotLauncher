# -*- coding: utf-8 -*-
"""静态检查：类方法的装饰器与参数是否自相矛盾。

能抓到的真实事故：
    @staticmethod
    def _primary_key_for_bot(self, bot_id):   # ← self 被吃掉
        bot = self.config.get_bot(bot_id)     # ← AttributeError，被 except 吞掉
        ...

这种写法**语法合法、编译通过、也不报错**，只是函数永远返回兜底值
（真机上表现为"点机器人行时高亮不落到主程序行"）。普通检查根本发现不了。

检查规则：
  · 带 `self` 参数的方法**不能**再装饰 staticmethod / classmethod；
  · 反之，staticmethod 不应出现 `self`，classmethod 第一个参数应为 `cls`；
  · `property` 及其 setter/deleter 只能出现在带 `self` 的方法上。

用法：
    python tools\\check_method_decorators.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def first_arg(node: ast.FunctionDef) -> str:
    args = node.args.args
    return args[0].arg if args else ""


def decorator_names(node: ast.FunctionDef) -> list:
    return [ast.unparse(item) for item in node.decorator_list]


def main() -> int:
    targets = sorted(Path(ROOT, "app").rglob("*.py")) + [Path(ROOT, "main.py")]
    total = 0
    problems = []

    for path in targets:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                total += 1
                decs = decorator_names(item)
                arg0 = first_arg(item)
                where = "{}:{} {}.{}".format(
                    path.relative_to(ROOT), item.lineno, node.name, item.name)

                if "staticmethod" in decs and arg0 == "self":
                    problems.append(
                        "{}：@staticmethod 却带 self（第一个参数会被吃掉，"
                        "self 变成调用方传入的值）".format(where))
                if "classmethod" in decs and arg0 == "self":
                    problems.append("{}：@classmethod 却用 self（应为 cls）".format(where))
                if "staticmethod" in decs and arg0 and arg0 != "self":
                    # staticmethod 不该有 self/cls；有别的参数名是正常的
                    pass
                if any(d == "property" for d in decs) and not arg0:
                    problems.append("{}：@property 必须带 self".format(where))
                if any(d.endswith(".setter") or d.endswith(".deleter") for d in decs):
                    if arg0 not in ("self", "cls"):
                        problems.append(
                            "{}：setter/deleter 第一个参数应为 self".format(where))

    check("扫描到类方法 {} 个".format(total), total > 0)
    check("没有装饰器与参数自相矛盾的方法", not problems,
          "" if not problems else "{} 处".format(len(problems)))
    for line in problems:
        print("         · " + line)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
