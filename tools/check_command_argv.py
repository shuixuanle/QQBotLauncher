# -*- coding: utf-8 -*-
"""静态 + 运行检查：命令行拆分后的参数**不该带着引号**进子进程。

真机事故（2026-10-02）：一条命令解释全部现象
--------------------------------------------
`check_restart_force.py` 在用户机器上卡在第一步：探针进程"已启动 → 立刻正常退出
（退出码 0）→ 零输出"。根因不在检查器，而在**管理器自己**：

    split_command_line() 是**故意**保留引号的（`cmd /c "a && b"` 必须把引号
    一起交给 cmd）；而 via_shell=False 时，参数直接交给 QProcess/CreateProcess，
    引号就**原样进到子进程的 argv 里**：

        python -c "import time; print('probe up'); time.sleep(120)"
        → 子进程收到 '"import time; print(\'probe up\'); time.sleep(120)"'
        → Python 把它当成一个**字符串字面量**：语法合法、什么都不做、退出码 0

含空格的路径同理（`java -jar "C:\\Program Files\\x.jar"` 会变成带引号的文件名）。
shell 路径（via_shell=True）不受影响 —— 那条路本来就该把引号交给 cmd。

本检查器干两件事：
  [1] 真跑 `split_command_line` / `strip_arg_quotes` / `Program.command_argv`：
      · 非 shell：每个参数都不带外层引号（回归断言：不许再出现"首尾都是引号"的参数）；
      · shell：整条命令保持原样（引号、&& 、| 都留给 cmd）；
      · 引号里的空格仍然是一个参数（不能被拆开）。
  [2] AST 断言：`command_argv()` 的非 shell 分支确实调用了 `strip_arg_quotes`。

用法：
    python tools\\check_command_argv.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Program, split_command_line, strip_arg_quotes  # noqa: E402

CONFIG = ROOT / "app" / "config.py"

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


def quoted(token: str) -> bool:
    """这个参数是不是"首尾都带引号"（= 引号漏进子进程的典型特征）。"""
    text = str(token or "")
    return len(text) >= 2 and text.startswith('"') and text.endswith('"')


def run_argv_checks() -> None:
    print("[1] split_command_line + strip_arg_quotes")
    probe = 'python -c "import time; print(\'probe up\', flush=True); time.sleep(120)"'
    tokens = split_command_line(probe)
    clean = strip_arg_quotes(tokens)
    check("拆分后 3 段（引号里的空格不拆）", len(tokens) == 3, str(tokens))
    check("清理后代码段不带引号", clean[2].startswith("import time"), repr(clean[2]))
    check("【回归】没有参数首尾都是引号", not any(quoted(t) for t in clean), str(clean))

    jar = strip_arg_quotes(split_command_line('java -jar "C:\\Program Files\\x\\app.jar"'))
    check("含空格路径：仍是一个参数", len(jar) == 3, str(jar))
    check("含空格路径：引号已去掉", jar[2] == "C:\\Program Files\\x\\app.jar", repr(jar[2]))

    exe = strip_arg_quotes(split_command_line('"C:\\Program Files\\Py\\python.exe" a.py'))
    check("可执行文件带引号也处理", exe[0].endswith("python.exe") and not quoted(exe[0]),
          repr(exe[0]))

    check("内部引号按 Windows 规则还原（\"\" -> \"）",
          strip_arg_quotes(['"a""b"']) == ['a"b'], str(strip_arg_quotes(['"a""b"'])))
    check("空参数保留为空串", strip_arg_quotes(['""']) == [""])
    check("没引号的参数原样", strip_arg_quotes(["python", "main.py"]) == ["python", "main.py"])
    check("空输入不炸", strip_arg_quotes([]) == [] and strip_arg_quotes(None) == [])

    print("\n[2] Program.command_argv（非 shell：给 QProcess 的最终 argv）")
    program = Program(id="p1", name="探针", command=probe)
    argv = program.command_argv(ROOT)
    check("三段（python / -c / 代码）", len(argv) == 3, str(argv))
    check("【回归】argv 里没有带外层引号的参数",
          not any(quoted(t) for t in argv), str(argv))
    check("代码段原样保留（含分号、单引号）",
          argv[2] == "import time; print('probe up', flush=True); time.sleep(120)",
          repr(argv[2]))

    spaced = Program(id="p2", name="jar", command='java -jar "C:\\Program Files\\a b\\x.jar"')
    argv2 = spaced.command_argv(ROOT)
    check("含空格路径仍是单个参数且无引号",
          argv2[2] == "C:\\Program Files\\a b\\x.jar", repr(argv2[2]))

    plain = Program(id="p3", name="plain", command="python main.py")
    check("普通命令不受影响", plain.command_argv(ROOT) == ["python", "main.py"],
          str(plain.command_argv(ROOT)))

    print("\n[3] via_shell=True：整条命令交给 cmd，**必须保留引号**")
    shell = Program(id="p4", name="shell",
                    command='cmd.exe /c "call venv\\Scripts\\activate.bat && nb run"',
                    via_shell=True)
    shell_argv = shell.command_argv(ROOT)
    check("走 shell 时 argv = [cmd, /c, 原命令]", len(shell_argv) == 3, str(shell_argv))
    check("原命令里的引号 / && 原样保留（由 cmd 解释）",
          shell_argv[2] == 'cmd.exe /c "call venv\\Scripts\\activate.bat && nb run"',
          repr(shell_argv[2]))


def run_source_checks() -> None:
    print("\n[4] 源码级：非 shell 分支必须调用 strip_arg_quotes")
    source = CONFIG.read_text(encoding="utf-8")
    tree = ast.parse(source)
    command_argv = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.FunctionDef) and node.name == "command_argv"), None)
    check("找得到 Program.command_argv", command_argv is not None)
    if command_argv is not None:
        called = {node.func.id for node in ast.walk(command_argv)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        check("调用了 strip_arg_quotes", "strip_arg_quotes" in called, str(sorted(called)))
        returned = [node for node in ast.walk(command_argv)
                    if isinstance(node, ast.Return) and node.value is not None]
        wrapped = any(isinstance(node.value, ast.Call)
                      and isinstance(node.value.func, ast.Name)
                      and node.value.func.id == "strip_arg_quotes"
                      for node in returned)
        check("返回值经过了 strip_arg_quotes（不是只在中间调一下）", wrapped)
    check("有 strip_arg_quotes 函数本身", "def strip_arg_quotes" in source)


def main() -> int:
    run_argv_checks()
    run_source_checks()
    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
