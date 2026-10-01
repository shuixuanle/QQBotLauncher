# -*- coding: utf-8 -*-
"""静态检查：main.py 的 --console（独立控制台）与 --elevate 接线是否正确。

为什么需要这个检查：`main.py` 依赖 PyQt6，在没有 PyQt6 的机器上无法 import 它，
所以用 AST 直接核对结构（这正是 check_names / check_module_attrs 的做法）。

背景：在 cmd 里敲 `python main.py` 时，python.exe 会把那个 cmd 当作自己的控制台，
**运行期间不能关闭**（一关管理器就被带走）。`--console` 让程序自己 AllocConsole，
开一个属于管理器的窗口，于是原来的 cmd 可以随手关掉。

用法：
    python tools\\check_console_flag.py
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def find_class(tree, name):
    return next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name), None
    )


def find_func(tree, name):
    return next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None
    )


def main() -> int:
    src = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)

    print("[1] 命令行参数")
    args_cls = find_class(tree, "StartupArgs")
    check("存在 StartupArgs", args_cls is not None)
    fields = []
    if args_cls is not None:
        for node in args_cls.body:
            if isinstance(node, ast.AnnAssign):
                fields.append(ast.unparse(node.target))
    for name in ("elevate", "no_elevate", "console"):
        check("  字段 {}".format(name), name in fields, "字段={}".format(fields))

    parser = find_func(tree, "parse_args")
    parser_src = ast.get_source_segment(src, parser) or "" if parser else ""
    check("  parse_args 识别 --console", '"--console"' in parser_src)
    check("  parse_args 识别 --elevate", '"--elevate"' in parser_src)
    check("  parse_args 识别 --no-elevate", '"--no-elevate"' in parser_src)
    check("  StartupArgs(...) 构造里带上了 console",
          "no_elevate, console" in parser_src or "console," in parser_src)

    print("\n[2] alloc_console 实现")
    alloc = find_func(tree, "alloc_console")
    check("存在 alloc_console", alloc is not None)
    alloc_src = ast.get_source_segment(src, alloc) or "" if alloc else ""
    check("  用 kernel32.AllocConsole 开新控制台", "AllocConsole" in alloc_src)
    # 关键：只调 AllocConsole 是没用的 —— 进程本来就有控制台（那个 cmd），
    # 必须先 FreeConsole 脱离，否则关掉 cmd 仍然会把管理器带走（第一版就错在这）。
    check("  先 FreeConsole 脱离原控制台（关键，缺了它等于没做）",
          "FreeConsole" in alloc_src)
    check("  用 GetConsoleWindow 判断原本有没有控制台",
          "GetConsoleWindow" in alloc_src)
    if "FreeConsole" in alloc_src and "AllocConsole" in alloc_src:
        check("  FreeConsole 在 AllocConsole 之前",
              alloc_src.index("FreeConsole") < alloc_src.index("AllocConsole"))
    check("  用 SetStdHandle 把 OS 层标准句柄指向新控制台",
          "SetStdHandle" in alloc_src)
    check("  重新绑定 sys.stdout/stderr 到 CONOUT$",
          "CONOUT$" in alloc_src and "sys.stdout" in alloc_src)
    check("  非 Windows 直接返回 False", 'os.name != "nt"' in alloc_src)
    # 真实踩过的坑：GetConsoleWindow / FreeConsole / AllocConsole 都在 **kernel32**，
    # 写成 user32 会抛 AttributeError，被 except 吞掉 → 静默返回 False。
    check("  没有把 GetConsoleWindow 写到 user32 上（必须在 kernel32）",
          "user32.GetConsoleWindow" not in alloc_src)
    check("  FreeConsole 来自 kernel32", "kernel32.FreeConsole" in alloc_src)

    # main() 源码在 [2.5] 与 [3] 都要用，先算出来
    main_fn = find_func(tree, "main")
    main_src = ast.get_source_segment(src, main_fn) or "" if main_fn else ""

    print("\n[2.4] relaunch_detached（--gui：pythonw 脱离，零控制台）")
    det = find_func(tree, "relaunch_detached")
    check("存在 relaunch_detached", det is not None)
    det_src = ast.get_source_segment(src, det) or "" if det else ""
    check("  用 DETACHED_PROCESS", "DETACHED_PROCESS" in det_src)
    check("  优先同目录 pythonw.exe", "pythonw.exe" in det_src)
    check("  退路用 shutil.which 找 pythonw", "shutil.which" in det_src)
    check("  三个标准流都指向 DEVNULL（不继承父进程句柄）",
          all(k in det_src for k in
              ("stdin=subprocess.DEVNULL", "stdout=subprocess.DEVNULL",
               "stderr=subprocess.DEVNULL")))
    check("  过滤掉 --gui / --console / --elevate",
          '"--gui"' in det_src and '"--console"' in det_src and '"--elevate"' in det_src)
    check("  非 Windows 返回 False", 'os.name != "nt"' in det_src)
    if det is not None:
        calls = [n.func.attr for n in ast.walk(det)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        check("  没有真的调用 AllocConsole（否则会多一个窗口）",
              "AllocConsole" not in calls)
    check("  parse_args 识别 --gui", '"--gui"' in parser_src)
    check("  StartupArgs 有 gui 字段", "gui" in fields)
    check("  main() 里 --gui 优先于 --console",
          "parsed.gui" in main_src and "relaunch_detached()" in main_src)

    print("\n[2.5] relaunch_with_own_console（--console：带独立控制台）")
    rel = find_func(tree, "relaunch_with_own_console")
    check("存在 relaunch_with_own_console", rel is not None)
    rel_src = ast.get_source_segment(src, rel) or "" if rel else ""
    check("  用 CREATE_NEW_CONSOLE 开新控制台",
          "CREATE_NEW_CONSOLE" in rel_src)
    check("  启动前先 FreeConsole（否则子进程会继承父进程句柄、输出跑到旧窗口）",
          "FreeConsole" in rel_src)
    check("  通过环境变量防无限重启",
          "GUI_CHILD_ENV" in rel_src and "child_env" in rel_src)
    check("  过滤掉 --console / --elevate，避免子进程重复处理",
          '"--console"' in rel_src and '"--elevate"' in rel_src)
    check("  非 Windows 返回 False", 'os.name != "nt"' in rel_src)
    check("  GUI_CHILD_ENV 常量已定义", "GUI_CHILD_ENV = " in src)
    check("  main() 里先试脱离启动、失败才退回 AllocConsole",
          "relaunch_with_own_console()" in main_src
          and "alloc_console()" in main_src
          and main_src.index("relaunch_with_own_console()") < main_src.index("alloc_console()"))

    print("\n[3] main() 里的接线顺序")
    check("main() 存在", main_fn is not None)
    check("  调用了 alloc_console()", "alloc_console()" in main_src)
    check("  受 parsed.console 控制", "parsed.console" in main_src)
    if "alloc_console()" in main_src and "configure_qt_attributes()" in main_src:
        alloc_pos = main_src.index("alloc_console()")
        qt_pos = main_src.index("configure_qt_attributes()")
        check("  在 QApplication 之前调用（控制台要早于 Qt 初始化）",
              alloc_pos < qt_pos,
              "alloc@{}, qt@{}".format(alloc_pos, qt_pos))
    else:
        check("  顺序可判定", False, "缺 alloc_console 或 configure_qt_attributes")

    print("\n[4] 模块导入")
    imports = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    check("  已 import ctypes（alloc_console 需要）", "ctypes" in imports,
          "imports={}".format(sorted(imports)))

    print("\n[5] 帮助文本")
    doc = ast.get_docstring(tree) or ""
    check("  --console 出现在模块文档里", "--console" in doc)
    check("  --elevate 出现在模块文档里", "--elevate" in doc)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
