# -*- coding: utf-8 -*-
"""实测 alloc_console：验证 FreeConsole + AllocConsole 是否真的换掉了控制台。

为什么必须实测：这个 bug 的形态是"代码看着对、行为没变化"。
第一版只调 AllocConsole 并且"已有控制台就 return"，编译通过、检查器全绿，
但关掉 cmd 依然会把管理器带走。唯一可信的判据是：
  · AllocConsole 之后 GetConsoleWindow() 拿到的是**新的**窗口句柄；
  · 新窗口的**宿主进程 ID** 变成了自己。

用法：
    python tools\\check_alloc_console_live.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 这段代码在**子进程**里运行：先记录原控制台信息，再调用 main.alloc_console，
# 然后对比前后变化。不导入 PyQt6（alloc_console 不依赖它），所以可以单独抽出。
CHILD = r'''
import ast, ctypes, json, sys
from pathlib import Path

root = Path(sys.argv[1])
src = (root / "main.py").read_text(encoding="utf-8")
tree = ast.parse(src)
fn = next(n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "alloc_console")
code = ast.get_source_segment(src, fn)

import os
ns = {"os": os, "sys": sys, "ctypes": ctypes}
exec(code, ns)
alloc_console = ns["alloc_console"]

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

def owner_pid(hwnd):
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value

# GetConsoleWindow 在 kernel32（不在 user32）
before_hwnd = kernel32.GetConsoleWindow()
before_pid = owner_pid(before_hwnd) if before_hwnd else 0

ok = alloc_console()

after_hwnd = kernel32.GetConsoleWindow()
after_pid = owner_pid(after_hwnd) if after_hwnd else 0

# 注意：alloc_console() 会把标准输出重绑到**新控制台**，所以结果不能靠 stdout
# 回传（父进程的管道收不到），必须写文件。
result = {
    "self_pid": os.getpid(),
    "before_hwnd": before_hwnd,
    "before_pid": before_pid,
    "after_hwnd": after_hwnd,
    "after_pid": after_pid,
    "returned": bool(ok),
}
Path(sys.argv[2]).write_text(json.dumps(result), encoding="utf-8")
'''


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    script = ROOT / "tools" / "_alloc_probe.py"
    result_file = ROOT / "tools" / "_alloc_result.json"
    script.write_text(CHILD, encoding="utf-8")
    result_file.unlink(missing_ok=True)
    failures = []
    try:
        print("[0] 脱离启动：--gui（pythonw，零控制台）与 --console（独立控制台）")
        detach_file = ROOT / "tools" / "_detach_result.json"
        detach_file.unlink(missing_ok=True)
        detach = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "_detach_probe.py"),
             str(ROOT), str(detach_file), "detached"],
            capture_output=True, text=True, timeout=60, check=False,
            encoding="utf-8", errors="replace",
        )
        print("  父进程 returncode = {} （0 = 已成功拉起子进程并退出）".format(
            detach.returncode))
        if detach.stderr and detach.stderr.strip():
            print("  父进程 stderr：", detach.stderr.strip()[:500])

        def check_detach(label, cond, detail=""):
            print(("  [OK]   " if cond else "  [FAIL] ") + label
                  + (("  " + detail) if detail else ""))
            if not cond:
                failures.append(label)

        check_detach("脱离探针正常结束（returncode=0）", detach.returncode == 0,
                     "returncode={}".format(detach.returncode))
        if detach_file.exists():
            import json as _json

            info = _json.loads(detach_file.read_text(encoding="utf-8"))
            print("  探针结果 = {}".format(info))
            check_detach("relaunch_with_own_console() 返回 True（已拉起子进程）",
                         info.get("started") is True)
            check_detach("父进程原本有控制台（模拟从 cmd 启动）",
                         bool(info.get("before_hwnd")),
                         "hwnd={}".format(info.get("before_hwnd")))
            check_detach("父进程的控制台宿主不是自己（那是外层 cmd）",
                         info.get("before_pid") != info.get("self_pid"),
                         "owner={} self={}".format(info.get("before_pid"),
                                                   info.get("self_pid")))
            detach_file.unlink(missing_ok=True)
        else:
            check_detach("脱离探针写出了结果文件", False, str(detach_file))

        print()
        print("[0.5] --gui 的专项断言（这一段才是「只想要界面」的保证）")
        # 再跑一次 detached，专门核对三个关键点
        gui_file = ROOT / "tools" / "_gui_result.json"
        gui_file.unlink(missing_ok=True)
        gui = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "_detach_probe.py"),
             str(ROOT), str(gui_file), "detached"],
            capture_output=True, text=True, timeout=60, check=False,
            encoding="utf-8", errors="replace",
        )
        check_detach("--gui 探针正常结束", gui.returncode == 0,
                     "returncode={}".format(gui.returncode))
        if gui_file.exists():
            import json as _json2

            ginfo = _json2.loads(gui_file.read_text(encoding="utf-8"))
            print("  探针结果 = {}".format(ginfo))
            check_detach("relaunch_detached() 返回 True（已拉起 pythonw 子进程）",
                         ginfo.get("started") is True)
        else:
            check_detach("--gui 探针写出了结果文件", False, str(gui_file))

        # 源码级断言：必须用 pythonw + DETACHED_PROCESS，且不能创建控制台
        main_src_all = (ROOT / "main.py").read_text(encoding="utf-8")
        import ast as _ast

        _tree = _ast.parse(main_src_all)
        _rel = next(
            n for n in _tree.body
            if isinstance(n, _ast.FunctionDef) and n.name == "relaunch_detached"
        )
        rel_src = _ast.get_source_segment(main_src_all, _rel) or ""
        check_detach("  用 DETACHED_PROCESS（彻底脱离父进程控制台）",
                     "DETACHED_PROCESS" in rel_src)
        check_detach("  优先选同目录的 pythonw.exe（GUI 子系统，不建控制台）",
                     "pythonw.exe" in rel_src)
        check_detach("  带 GUI_CHILD_ENV 防无限重启", "GUI_CHILD_ENV" in rel_src)
        check_detach("  关闭三个标准流（devnull），不继承父进程句柄",
                     "stdin=subprocess.DEVNULL" in rel_src
                     and "stdout=subprocess.DEVNULL" in rel_src)
        # 注意：不能用"源码里是否出现 AllocConsole"来判断 —— 该词在文档串里
        # 也会出现（解释为什么不能用它）。必须看有没有**真的调用**。
        calls = [
            n.func.attr
            for n in _ast.walk(_rel)
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
        ]
        check_detach("  没有真的调用 AllocConsole（那会多出一个窗口）",
                     "AllocConsole" not in calls, "调用列表={}".format(sorted(set(calls))))
        check_detach("  没有调用 FreeConsole（脱离由 DETACHED_PROCESS 完成）",
                     "FreeConsole" not in calls)

        gui_file.unlink(missing_ok=True)

        # 清理探针拉起的子进程（它是个真实的 python 进程，会一直开着控制台窗口）
        cleanup = subprocess.run(
            ["taskkill", "/F", "/FI", "IMAGENAME eq python.exe", "/FI",
             "WINDOWTITLE eq *"],
            capture_output=True, text=True, check=False,
        )
        del cleanup  # 结果不重要，失败也无妨（沙箱可能不允许）
        print()

        print("[1] 子进程：从 cmd 启动（本来就有控制台）")
        import os as _os

        child_env = dict(_os.environ, PYTHONIOENCODING="utf-8")
        completed = subprocess.run(
            [sys.executable, str(script), str(ROOT), str(result_file)],
            capture_output=True, text=True, timeout=60, check=False,
            encoding="utf-8", errors="replace", env=child_env,
        )
        if completed.returncode != 0:
            print("  子进程失败（returncode={}）：".format(completed.returncode))
            print((completed.stderr or "")[:1500])
            print("---- stdout ----")
            print((completed.stdout or "")[:1500])
            return 1

        import json

        if not result_file.exists():
            print("  子进程没有写出结果文件。stdout/stderr：")
            print((completed.stdout or "")[:600])
            print((completed.stderr or "")[:1200])
            return 1
        data = json.loads(result_file.read_text(encoding="utf-8"))

        print("  self pid        = {}".format(data["self_pid"]))
        print("  调用前 hwnd/pid = {} / {}".format(data["before_hwnd"], data["before_pid"]))
        print("  调用后 hwnd/pid = {} / {}".format(data["after_hwnd"], data["after_pid"]))
        print("  alloc_console() = {}".format(data["returned"]))

        def check(label, cond, detail=""):
            print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
            if not cond:
                failures.append(label)

        print()
        print("[2] 断言")
        check("调用前确实有控制台（模拟从 cmd 启动）", bool(data["before_hwnd"]),
              "hwnd={}".format(data["before_hwnd"]))
        check("alloc_console() 返回 True", data["returned"])
        check("调用后仍有控制台窗口", bool(data["after_hwnd"]))
        check("窗口句柄已改变（说明换成了新控制台）",
              data["before_hwnd"] != data["after_hwnd"],
              "{} -> {}".format(data["before_hwnd"], data["after_hwnd"]))
        check("新控制台的宿主进程 = 自己（真正脱离原 cmd）",
              data["after_pid"] == data["self_pid"],
              "owner={} self={}".format(data["after_pid"], data["self_pid"]))
        check("原控制台的宿主不是自己（那是 cmd/父进程）",
              data["before_pid"] != data["self_pid"],
              "owner={}".format(data["before_pid"]))
    finally:
        script.unlink(missing_ok=True)
        result_file.unlink(missing_ok=True)

    print()
    print("结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
