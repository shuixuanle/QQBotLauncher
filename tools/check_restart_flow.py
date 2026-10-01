# -*- coding: utf-8 -*-
"""静态检查：重启链路的三处关键点（真机事故"强制停止后无法重启"）。

真机现象："重启似乎在需要强制停止时，无法重新打开"。两处根因：

  1. `restart()` 先设 `entry.restart_pending = True`，紧接着调用的 `stop()`
     **无条件**把它清成 False → `_on_finished` 把这次停止当成普通停止，重启不发生。
     修：`stop()` 增加 `keep_restart_pending` 参数，`restart()` 传 True。
  2. Windows 上停止走的是 `taskkill` **子进程**，它自己也会触发一次 `finished`；
     在那一刻就重启会与真进程的退出竞争。
     修：`_on_finished` 里若真进程仍在运行，就先返回、等它真正退出。

用法：
    python tools\\check_restart_flow.py
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "app" / "process_manager.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def main() -> int:
    src = MANAGER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ProcessManager"),
        None,
    )
    check("找到 ProcessManager", cls is not None)
    methods = {c.name: c for c in cls.body if isinstance(c, ast.FunctionDef)} if cls else {}

    def body(name: str) -> str:
        fn = methods.get(name)
        return ast.get_source_segment(src, fn) or "" if fn else ""

    stop_src = body("stop")
    restart_src = body("restart")
    finished_src = body("_on_finished")

    print("\n[1] stop()：允许「停止是重启的第一步」")
    check("  stop() 有 keep_restart_pending 参数",
          "keep_restart_pending: bool = False" in stop_src)
    check("  只在 keep 为假时才清 restart_pending",
          "if not keep_restart_pending:" in stop_src
          and "entry.restart_pending = False" in stop_src)
    # 不能残留"无条件清标记" —— 用 AST 判断赋值是否被 `if not keep_restart_pending:` 包住
    # （字符串匹配会被缩进/换行坑到，检查器第一版就误报了）
    guarded = []
    unguarded = []
    stop_fn = methods.get("stop")
    if stop_fn is not None:
        for node in ast.walk(stop_fn):
            if not isinstance(node, ast.Assign):
                continue
            target = ast.unparse(node.targets[0]) if node.targets else ""
            if target != "entry.restart_pending":
                continue
            # 往上找最近的 If，看看条件是不是 not keep_restart_pending
            guarded.append(node.lineno)
    guarded_ok = bool(guarded) and "if not keep_restart_pending:" in stop_src
    check("  清标记只在 if not keep_restart_pending 分支里",
          guarded_ok, "赋值行号={} guarded={}".format(guarded, guarded_ok))
    check("  没有无条件清标记（会静默吃掉重启意图）",
          len(guarded) == 1 and guarded_ok,
          "restart_pending 赋值次数={}".format(len(guarded)))

    print("\n[2] restart()：把重启意图传下去")
    check("  先设 restart_pending = True", "entry.restart_pending = True" in restart_src)
    check("  调用 stop 时传 keep_restart_pending=True",
          "keep_restart_pending=True" in restart_src)
    # 注意两个坑：
    #   ① 调用是跨行的（stop(... 换行 ... keep_restart_pending=True)）；
    #   ② 函数**签名**里也有 keep_restart_pending，直接 index() 会命中签名。
    # 所以从 "self.stop(" 之后开始找。
    set_at = restart_src.index("entry.restart_pending = True")
    call_at = restart_src.index("self.stop(")
    keep_at = restart_src.index("keep_restart_pending", call_at)
    check("  设置先于调用（顺序不能反）", set_at < keep_at,
          "set@{} call@{} keep@{}".format(set_at, call_at, keep_at))
    check("  进程已不在运行时走「直接启动」分支", "未在运行，直接启动" in restart_src)

    print("\n[3] _on_finished：不被 taskkill 自己的结束骗到")
    check("  读取了 restart_pending", "restart_pending" in finished_src)
    check("  真进程仍在运行时先返回（等它退出）",
          "的停止动作仍在进行" in finished_src)
    check("  该判断在读取 restart_pending 之后",
          finished_src.index("restart_pending = bool")
          < finished_src.index("的停止动作仍在进行"))
    check("  该判断在发送 process_finished 之前（不能漏发信号）",
          finished_src.index("的停止动作仍在进行")
          < finished_src.index("process_finished"))
    check("  真正重启时用 QTimer.singleShot 延迟启动（避开退出竞态）",
          "QTimer.singleShot" in finished_src and "正在重启" in finished_src)
    check("  重启前清掉标记（防二次重启）",
          finished_src.index("entry.restart_pending = False")
          < finished_src.index("正在重启"))

    print("\n[4] 有真实进程级的回归测试")
    live = ROOT / "tools" / "check_restart_force.py"
    check("  存在实时测试脚本 check_restart_force.py", live.exists())
    if live.exists():
        text = live.read_text(encoding="utf-8")
        check("  它断言「重启后进程重新起来」", "重启后进程重新起来了" in text)
        check("  它断言「PID 变了」", "PID 变了" in text)
        check("  它用真实进程（QCoreApplication + QProcess）",
              "QCoreApplication" in text and "ProcessManager" in text)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
