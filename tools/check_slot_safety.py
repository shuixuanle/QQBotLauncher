# -*- coding: utf-8 -*-
"""静态检查：高频 Qt 槽里必须有兜底（异常冒出去 = 整个程序被终止）。

为什么单列一个检查器
--------------------
PyQt6 对**槽里的未捕获异常**会直接终止进程（真机反复踩过："闪退"）。
而"拖动分隔条 / 滚动 / 换布局"这几条路径上的槽触发频率最高、又最容易碰到
"控件正在重建（deleteLater 了但 Python 包装还在）"的瞬间 —— 一旦抛
RuntimeError，用户看到的就是"拖一下就崩"。

真机 2026-10-05："测试 bot 实例依旧会导致左侧栏拖动宽度时崩溃"。

所以把这些槽钉住：函数体里必须出现 `except`（最好是
`except (RuntimeError, AttributeError, TypeError, ValueError)`）。

用法：
    python tools\\check_slot_safety.py
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 必须带兜底的"高频槽"：文件 -> 方法名
MUST_GUARD = {
    "app/ui/bot_tab.py": (
        "_moved",                 # splitterMoved（拖动分隔条，连续触发）
        "_on_split_settled",      # 去抖定时器
        "_capture_pane_sizes",    # 拖动时高频调用
        "_apply_pane_sizes",      # 重建/拖动后套比例
        "_on_layout_button",      # 布局按钮（菜单构建里会遍历窗格）
    ),
    "app/ui/program_widget.py": (
        "_on_scrolled",           # valueChanged（滚动/布局变化时连续触发）
        "_rerender_all",          # 换主题重画
    ),
    "app/ui/main_window.py": (
        "_rebuild_layout_menu",   # 每次展开菜单都会重建
    ),
}

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError。这里统一退化成 ?。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def method_sources(path: Path) -> dict:
    """文件里"方法名 -> 源码片段"（同类方法重名时取第一个）。"""
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name not in found:
            found[node.name] = ast.get_source_segment(source, node) or ""
    return found


def has_guard(source: str) -> bool:
    """函数体里有没有 try/except 兜底。"""
    try:
        tree = ast.parse("def _probe():\n" + "\n".join(
            "    " + line for line in source.splitlines()[1:]))
    except SyntaxError:
        return "except" in source
    return any(isinstance(node, ast.Try) for node in ast.walk(tree))


def main() -> int:
    print("[1] 高频槽必须有兜底（Qt 槽里抛异常 = 进程被终止）")
    missing = []
    for rel, names in MUST_GUARD.items():
        path = ROOT / rel
        if not path.exists():
            missing.append("{}（文件不存在）".format(rel))
            continue
        sources = method_sources(path)
        for name in names:
            body = sources.get(name)
            if body is None:
                # 方法没了（改名/删除）也要报出来，免得断言悄悄失效
                missing.append("{}::{} 不存在".format(rel, name))
                continue
            if not has_guard(body):
                missing.append("{}::{}".format(rel, name))
    check("{} 个高频槽都带兜底".format(sum(len(v) for v in MUST_GUARD.values())),
          not missing, "缺：{}".format("、".join(missing[:4])))

    print("\n[2] 这些槽本身不许变成空壳（兜底不等于什么都不做）")
    thin = []
    for rel, names in MUST_GUARD.items():
        sources = method_sources(ROOT / rel)
        for name in names:
            body = sources.get(name) or ""
            code = [line for line in body.splitlines()
                    if line.strip() and not line.strip().startswith(("#", '"""', "'''"))]
            if len(code) < 4:
                thin.append("{}::{}".format(rel, name))
    check("没有空壳槽", not thin, str(thin[:3]))

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
