# -*- coding: utf-8 -*-
"""一次性维护：在 README 的「### 1. 正常启动（图形界面）」前补一节"双击启动"。

用法：
    python tools\\patch_readme_launchers.py            # 预览
    python tools\\patch_readme_launchers.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

ANCHOR = "### 1. 正常启动（图形界面）"

SECTION = """### 0. 双击启动（推荐，不用开命令行）

| 文件 | 控制台窗口 | 权限 | 用途 |
| --- | --- | --- | --- |
| `启动（普通模式）.vbs` | **无** | 普通 | 日常使用（推荐） |
| `启动（普通模式）.bat` | 闪一下 | 普通 | 想看到"用了哪个 Python" |
| `启动（管理员模式）.vbs` | **无** | **管理员**（1 次 UAC） | 管理的程序里有需要提权的（如消防栓） |
| `启动（管理员模式）.bat` | 闪一下 | **管理员**（1 次 UAC） | 同上 |

- `.vbs` 用 `WScript.Shell.Run(cmd, 0, ...)`（窗口样式 0）配合 `pythonw.exe`
  启动，**完全不出现命令行窗口**；`.bat` 由 cmd 执行，必然闪一下。
- 脚本会挨个验证候选解释器（`python -c "import PyQt6"`），
  取第一个**真的装了 PyQt6** 的 —— 只信 PATH 会踩坑（可能选到没装 PyQt6 的
  Python，表现为闪一下就退出并报 `ModuleNotFoundError`）。
- **管理员模式**：启动时弹一次 UAC，之后启动需要提权的程序
  （如消防栓的 HttpListener）**不再弹**，因为子进程继承了管理员令牌。
- 想开机自启：把快捷方式放进 `shell:startup`。

细节与维护注意见 [启动方式说明.md](启动方式说明.md)。

"""


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")

    if "### 0. 双击启动（推荐，不用开命令行）" in src:
        print("README 已经有这一节，跳过。")
        return 0
    if src.count(ANCHOR) != 1:
        print("!! 找不到唯一锚点：{}（出现 {} 次）".format(ANCHOR, src.count(ANCHOR)))
        return 1

    src = src.replace(ANCHOR, SECTION + ANCHOR, 1)
    print("将在「{}」前插入 {} 行".format(ANCHOR, len(SECTION.splitlines())))
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    README.write_text(src, encoding="utf-8")
    print("已写入 README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
