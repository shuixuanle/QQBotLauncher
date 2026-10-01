# -*- coding: utf-8 -*-
"""README 改造（用户要求）：

1. **说清项目本质**：这是"把本机一堆 bot 的 cmd/终端窗口统合到一个窗口"的**个人项目**，
   而不是通用框架 —— 读者一眼要知道它解决的是什么问题。
2. **补"配置文件是怎么写入的"**：原文只给了 JSON 长什么样，没说它怎么产生。
   实际上有两条正道：**首次运行自动生成** + **在界面上新建/编辑（推荐）**，
   手写 JSON 只是备选。这条链路必须写明，否则新人对着空配置无从下手。

用法：
    python tools\\patch_readme_positioning.py            # 预览
    python tools\\patch_readme_positioning.py --apply    # 写入
"""

import sys
from pathlib import Path

# 本脚本在 tools/maintenance/ 下 —— 要上跳**三**级才是项目根
ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

# ---------------------------------------------------------------------------
# ① 开头：项目本质
# ---------------------------------------------------------------------------
OLD_INTRO = """基于 **PyQt6 + QProcess** 的 QQ 机器人启动管理器：一个机器人可以包含多个程序（主程序 + 若干副程序），
按配置顺序依次拉起、实时查看每个程序的日志、一键停止整棵进程树、自动选择最新的 jar 包。"""

NEW_INTRO = """> **这是什么**：一个**个人用**的 Windows 桌面工具，把散落在各处的
> **QQ 机器人项目**（GitHub 上那些 `start.bat` / `nb run` / `java -jar` / `dotnet run`）
> 从"一堆 cmd / 终端窗口"统合成**一个窗口**里管理：一个窗口里看所有机器人的日志、
> 一键启停、按分屏模板排版、记住每个实例的界面状态。
>
> 它不是 bot 框架，也不会替你运行机器人逻辑 —— 它只负责**启动、盯着、停掉**这些进程。

基于 **PyQt6 + QProcess** 的 QQ 机器人启动管理器：一个机器人可以包含多个程序（主程序 + 若干副程序），
按配置顺序依次拉起、实时查看每个程序的日志、一键停止整棵进程树、自动选择最新的 jar 包。

### 它适合谁

| 你的情况 | 是否合适 |
| --- | --- |
| 本机跑着**多个** bot 项目（JRebel/NowBot、NapCat、NoneBot、ATRI、自研 Java/.NET…），每次开机要手动开一堆窗口 | ✅ 正中目标 |
| 一个 bot = 多个进程（本体 + TTS + 中转 + Ollama + 绘图 API…），启停顺序有讲究 | ✅ 支持"主程序 + 副程序"与启动间隔 |
| 只跑一个 bot、一个进程 | ⚠️ 可以用，但收益有限（写个 `.bat` 就够了） |
| 想要 Web 面板 / 跨平台 / 多用户 | ❌ 这是 Windows 桌面单机工具 |

> 本仓库里的示例配置（[bots_config.example.json](bots_config.example.json)）就取自
> 真实在用的 4 个实例：Java(jar) + Node(yarn) + .NET(dotnet) + Python(uv) + Ollama + 批处理，
> 可作为"怎么把一类 bot 接进来"的参照。"""

# ---------------------------------------------------------------------------
# ② 配置写入方式（插在 §4 的 JSON 示例之前）
# ---------------------------------------------------------------------------
ANCHOR = """### 4. 配置文件长什么样

```json"""

NEW_SECTION = """### 4. 配置文件是怎么写入的（**先看这节**）

`bots_config.json` **不需要你手写**。它有两条正道，按推荐顺序：

#### 方式一：在界面上新建 / 编辑（推荐，日常都用这个）

程序**第一次运行**时，如果找不到 `bots_config.json`，会自动写入一份**默认配置**
（一个示例机器人 + 主/副两个程序），然后：

| 想做什么 | 怎么做 |
| --- | --- |
| 新建一个机器人 | 工具栏 **新建 Bot**（Ctrl+N），填名称、工作目录、程序与命令，点**保存** |
| 改现有机器人 | 左栏选中它 → **编辑当前 Bot**（Ctrl+E） |
| 删掉示例机器人 | 在编辑对话框里删除，或直接改 `bots_config.json` |
| 加一个程序 | 编辑对话框里「添加程序」；命令照抄你原来 `.bat` 里的那行即可 |

改动**立刻写盘**（编辑对话框点保存、删除机器人、导入配置等操作都会触发
`BotConfig.save()`），采用"先写临时文件再原子替换"的方式，写坏了也不会丢原文件。
文件若损坏，会被备份成 `bots_config.json.broken-<时间戳>` 并重建默认配置。

#### 方式二：直接编辑 JSON（批量改 / 版本管理时用）

工具栏 **打开配置文件**（Ctrl+Shift+O）会用系统默认程序打开它；保存后在管理器的
**机器人**菜单里重新载入即可生效。

#### 方式三：从别处拷一份

`bots_config.json` 就是一个纯 JSON，可以拷来拷去。注意两点：

- **路径基准**：`cwd` 与命令里的相对路径，都以 `bots_config.json` **所在目录**为基准
  （打包成 exe 后则是 exe 旁边），见「[四、bots_config.json 放在哪里](#四bots_configjson-放在哪里)」；
- 想要一份现成的骨架，直接拷 [bots_config.example.json](bots_config.example.json)
  改名为 `bots_config.json`，再把里面的 `<占位符>` 换成你的路径。

> ⚠️ 仓库里**不含** `bots_config.json` —— 它保存着你本机的绝对路径与个人配置，
> 已写进 `.gitignore`。

### 4.1 配置文件长什么样

```json"""


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")
    changed = []

    if NEW_INTRO.split("\n")[0] in src:
        print("  跳过（开头已是新版）")
    else:
        if src.count(OLD_INTRO) != 1:
            print("!! 开头锚点出现 {} 次".format(src.count(OLD_INTRO)))
            return 1
        src = src.replace(OLD_INTRO, NEW_INTRO, 1)
        changed.append("开头：说清项目本质与适用人群")

    if "### 4. 配置文件是怎么写入的" in src:
        print("  跳过（已有配置写入章节）")
    else:
        if src.count(ANCHOR) != 1:
            print("!! 配置章节锚点出现 {} 次".format(src.count(ANCHOR)))
            return 1
        src = src.replace(ANCHOR, NEW_SECTION, 1)
        changed.append("新增：配置文件是怎么写入的")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
