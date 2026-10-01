# -*- coding: utf-8 -*-
"""README：把「关闭时强制停止」的行为写全（用户要求"这个在 readme 里面说明了就好"）。

补两块内容：
  1. 勾选后的**即时反馈**：状态栏提示 `关闭管理器时将强制停止所有程序`；
  2. 完整的**操作步骤**（勾选 → 关窗 → 再打开时是什么状态），
     免得用户不知道开关是否生效。

不改任何代码。

用法：
    python tools\\maintenance\\patch_readme_force_stop.py            # 预览
    python tools\\maintenance\\patch_readme_force_stop.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

OLD = """- **关闭时强制停止**（可选开关）：勾选后关掉管理器**不再询问**，直接
  `taskkill /T /F` 结束所有正在运行的程序 —— 适合"我关管理器就是要它全停"。
  两个入口共享同一设置：**视图 →「关闭时强制停止所有程序」**、
  **视图 → 运行参数 →「关闭管理器时强制停止所有程序（不询问）」**。"""

NEW = """- **关闭时强制停止**（可选开关，**默认关**）：勾选后关掉管理器**不再询问**，
  直接 `taskkill /T /F` 结束所有正在运行的程序 —— 适合"我关管理器就是要它全停"。

  两个入口共享同一设置（勾一处，另一处同步）：

  | 入口 | 说明 |
  | --- | --- |
  | **视图 →「关闭时强制停止所有程序」** | 可勾选项，最快捷 |
  | **视图 → 运行参数 →「关闭管理器时强制停止所有程序（不询问）」** | 与其它运行时参数放一起 |

  **怎么用 / 会看到什么**：

  1. 勾选后**状态栏立刻提示**：`关闭管理器时将强制停止所有程序`
     （取消勾选则提示 `关闭管理器时将先询问再停止所有程序`）——
     不需要点确定，设置**立即写入**注册表 `process/force_stop_on_close`；
  2. 之后关掉管理器窗口（✕ / Alt+F4）时**不再弹确认框**：状态栏显示
     `正在强制停止所有程序…`，日志区出现 `强制停止模式：跳过确认与优雅等待`，
     窗口随即关闭；
  3. 下次打开管理器时，菜单项的**勾选态会自动还原**成上次的选择
     （存的是 `'true'`/`'false'` 字符串，程序用 `settings_bool()` 解析）；
  4. 想恢复"先询问再停止"：取消勾选，或在运行参数里取消同一个开关。"""


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")

    if "状态栏立刻提示" in src:
        print("已经补过了，跳过。")
        return 0
    if src.count(OLD) != 1:
        print("!! 锚点出现 {} 次（应为 1）".format(src.count(OLD)))
        return 1

    src = src.replace(OLD, NEW, 1)
    print("将替换为 {} 行的说明".format(len(NEW.splitlines())))
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    README.write_text(src, encoding="utf-8")
    print("已写入 {}".format(README.relative_to(ROOT)))

    lines = README.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines, 1):
        if l.startswith("- **关闭时强制停止**"):
            for j in range(i - 1, min(i + 24, len(lines))):
                print("{:4d}| {}".format(j + 1, lines[j][:96]))
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
