# -*- coding: utf-8 -*-
"""静态检查：关闭管理器时"强制停止所有程序"的功能接线。

真机需求："添加个关闭管理器时，自动强制停止所有正在运行的程序的功能"

语义：
  · 默认**关**（保持原有行为：弹确认框 + 按停止超时优雅等待）；
  · 打开后：关窗**不询问**，直接 `taskkill /T /F` 结束整棵进程树，关窗干净利落；
  · 两个入口共享同一个 QSettings 键（`process/force_stop_on_close`）：
      视图菜单 →「关闭时强制停止所有程序」
      运行参数 →「关闭管理器时强制停止所有程序（不询问）」

用法：
    python tools\\check_force_stop_on_close.py
"""

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "app" / "ui" / "main_window.py"
MANAGER = ROOT / "app" / "process_manager.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def method_src(text, tree, cls_name, method):
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name), None
    )
    if cls is None:
        return ""
    fn = next(
        (c for c in cls.body if isinstance(c, ast.FunctionDef) and c.name == method), None
    )
    return ast.get_source_segment(text, fn) or "" if fn else ""

# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    src = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    mgr_src = MANAGER.read_text(encoding="utf-8")
    mgr_tree = ast.parse(mgr_src)

    print("[1] 设置项与两个入口")
    check("  定义了 SETTINGS_FORCE_STOP_ON_CLOSE",
          'SETTINGS_FORCE_STOP_ON_CLOSE = "process/force_stop_on_close"' in src)
    check("  视图菜单里有该动作",
          "self.view_menu.addAction(self.action_force_stop_on_close)" in src)
    check("  动作是可勾选的", "action_force_stop_on_close.setCheckable(True)" in src)
    check("  动作连到处理函数",
          "self.action_force_stop_on_close.toggled.connect(" in src)
    check("  运行参数对话框里也有开关",
          'self.force_stop_box = QCheckBox("关闭管理器时强制停止所有程序（不询问）"' in src)
    check("  对话框 values() 带出该键",
          '"force_stop_on_close": bool(self.force_stop_box.isChecked())' in src)

    print("\n[2] 持久化与还原")
    check("  存在 set_force_stop_on_close", "def set_force_stop_on_close" in src)
    setter = method_src(src, tree, "MainWindow", "set_force_stop_on_close")
    check("  setter 会写注册表", "setValue(" in setter and "SETTINGS_FORCE_STOP_ON_CLOSE" in setter)
    check("  setter 同步菜单勾选态（屏蔽信号防递归）",
          "blockSignals(True)" in setter and "setChecked(" in setter)
    # 注意：还原运行时参数的方法叫 _apply_runtime_settings_from_config，
    # 不是 _restore_settings（检查器第一版就写错了名字，被自己抓到）
    restore = method_src(src, tree, "MainWindow", "_apply_runtime_settings_from_config")
    check("  找到承载还原逻辑的方法", bool(restore))
    check("  启动时用 settings_bool 解析（避开 'false' 陷阱）",
          "settings_bool(" in restore and "SETTINGS_FORCE_STOP_ON_CLOSE" in restore)
    check("  启动时同步菜单勾选态",
          "set_force_stop_on_close(self._force_stop_on_close, persist=False)" in restore)
    apply_fn = method_src(src, tree, "MainWindow", "open_runtime_settings")
    check("  运行参数确定后写入设置",
          "force_stop_on_close" in apply_fn and "setValue(" in apply_fn)

    print("\n[3] 关闭流程")
    close = method_src(src, tree, "MainWindow", "closeEvent")
    check("  closeEvent 读取该设置", "_force_stop_on_close" in close)
    check("  打开时不弹确认框（条件里含 not force_close）",
          "not force_close" in close)
    check("  强制模式传 force=True 给 stop_all",
          "force=force_close" in close)
    check("  强制模式把超时压短（1.5 秒兜底）", "1500 if force_close" in close)
    check("  日志里标明是强制模式", "强制停止模式" in close)

    print("\n[4] 进程管理器支持 force")
    stop_all = method_src(mgr_src, mgr_tree, "ProcessManager", "stop_all")
    check("  stop_all 有 force 参数", "force: bool = False" in stop_all)
    check("  把 force 透传给每个 stop()", "force=force" in stop_all)
    stop = method_src(mgr_src, mgr_tree, "ProcessManager", "stop")
    check("  stop() 支持 force（跳过优雅等待）", "force: bool = False" in stop)
    check("  force 时立刻安排强制结束",
          "self._schedule_force_kill(key, pid, 1500)" in stop)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
