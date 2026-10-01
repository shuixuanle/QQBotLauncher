# -*- coding: utf-8 -*-
"""README：把「关闭时强制停止」写全两处（用户要求"两处都写"）。

  · **功能一览**（保持简短）：一句说明 + 指向操作章节的锚点；
  · **### 3. 界面内的操作顺序**（详述）：新增第 5 步 + 一个折叠式的说明块 ——
    结构对齐该节已有的「> **命令写法小抄**」写法（标题 + 项目符号），
    并列出状态栏/日志的**原文提示**，方便对照。

不改任何代码。

用法：
    python tools\\maintenance\\patch_readme_force_stop_ops.py            # 预览
    python tools\\maintenance\\patch_readme_force_stop_ops.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

#: ① 功能一览：换成简短版 + 跳转
OLD_OVERVIEW = """- **关闭时强制停止**（可选开关，**默认关**）：勾选后关掉管理器**不再询问**，
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

NEW_OVERVIEW = """- **关闭时强制停止**（可选开关，**默认关**）：勾选后关掉管理器**不再询问**，
  直接 `taskkill /T /F` 结束所有正在运行的程序 —— 适合"我关管理器就是要它全停"。
  开关在 **视图 →「关闭时强制停止所有程序」**（运行参数里也有同一个）；
  用法与状态栏提示见 [「界面内的操作顺序」第 5 步](#3-界面内的操作顺序)。"""

#: ② 操作章节：把第 4 步扩成"4 设置 + 5 退出"，并附详述块
OLD_STEPS = """4. 需要改配置时点 **编辑当前 Bot**，或在日志区右键「编辑配置」。

> 快捷键：`Ctrl+N` 新建 · `Ctrl+E` 编辑 · `Ctrl+B` 查看已有 bot · `F5` 启动当前 Bot ·"""

NEW_STEPS = """4. 需要改配置时点 **编辑当前 Bot**，或在日志区右键「编辑配置」。
5. 退出：直接关窗口即可 —— 默认会问一句「仍有 N 个程序在运行」，确认后统一停止；
   想让它**不询问、直接强停**，见下面「退出时强制停止」。

> **退出时强制停止**（`视图 →「关闭时强制停止所有程序」`，运行参数里也有同一个开关）
>
> 适合"关掉管理器就是要所有 bot 全停"的用法。勾选后：
>
> 1. **状态栏立刻提示** `关闭管理器时将强制停止所有程序`
>    （取消勾选则提示 `关闭管理器时将先询问再停止所有程序`）——
>    不用点确定，设置立即写入注册表 `process/force_stop_on_close`；
> 2. 之后关窗口（✕ / `Alt+F4`）**不再弹确认框**：状态栏显示
>    `正在强制停止所有程序…`，日志区出现
>    `强制停止模式：跳过确认与优雅等待`，窗口随即关闭；
> 3. 下次打开管理器时菜单项的**勾选态自动还原**成上次的选择
>    （注册表里存的是 `'true'`/`'false'` 字符串，程序用 `settings_bool()` 解析）；
> 4. 想恢复"先询问再停止"：取消勾选即可。
>
> 与默认方式的差别：默认先 `taskkill /T`（礼貌请求）并按「停止超时」等待程序自行退出；
> 勾选后跳过优雅等待、直接 `taskkill /T /F` 结束整棵进程树，关窗更快更干脆 ——
> 代价是程序没有机会做退出前的清理。

> 快捷键：`Ctrl+N` 新建 · `Ctrl+E` 编辑 · `Ctrl+B` 查看已有 bot · `F5` 启动当前 Bot ·"""


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")
    changed = []

    for old, new, label in (
        (OLD_OVERVIEW, NEW_OVERVIEW, "功能一览：精简为一句 + 跳转"),
        (OLD_STEPS, NEW_STEPS, "操作顺序：新增第 5 步与详述块"),
    ):
        if new in src:
            print("  跳过（已是新版）：{}".format(label))
            continue
        if src.count(old) != 1:
            print("!! 锚点出现 {} 次：{}".format(src.count(old), label))
            return 1
        src = src.replace(old, new, 1)
        changed.append(label)

    for item in changed:
        print("  - {}".format(item))
    if not changed:
        print("无需修改。")
        return 0
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    README.write_text(src, encoding="utf-8")
    print("已写入 {}".format(README.relative_to(ROOT)))

    lines = README.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines, 1):
        if l.startswith("5. 退出："):
            for j in range(i - 1, min(i + 24, len(lines))):
                print("{:4d}| {}".format(j + 1, lines[j][:96]))
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
