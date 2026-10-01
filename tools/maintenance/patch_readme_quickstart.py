# -*- coding: utf-8 -*-
"""README 开头加一个「怎么跑起来」的显眼入口，指向《启动方式说明.md》。

用户要求："readme 记得提及【启动方式说明.md】"。
README 里原本已经有两处提及（「### 0. 双击启动」结尾、项目结构清单），
但那都在中后段；这里在**开头**补一个一眼能看到的入口。

用法：
    python tools\\maintenance\\patch_readme_quickstart.py            # 预览
    python tools\\maintenance\\patch_readme_quickstart.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

ANCHOR = "#### 作者的话"

BLOCK = """### 怎么跑起来（先看这里）

| 你的情况 | 做什么 |
| --- | --- |
| 只想用起来 | 把 exe 复制到**纯 ASCII 路径**（如 `C:\\QQBotLauncher\\`）双击；或双击 `启动（普通模式）.bat` |
| 有程序需要管理员权限 | 用 `启动（管理员模式）.bat` / 管理员版 exe（启动时弹**一次** UAC，之后不再弹） |
| 要排障、看输出 | 在项目目录执行 `python main.py`（窗口被占用，但能看到全部输出与报错） |
| 想要零控制台 | `python main.py --gui`（用 pythonw 脱离启动，屏幕上只剩界面） |

**完整的启动方式对比、打包 exe、安全警报处理、常见坑** →
[**启动方式说明.md**](启动方式说明.md)

---

"""


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")

    if "### 怎么跑起来（先看这里）" in src:
        print("已经加过了，跳过。")
        return 0
    if src.count(ANCHOR) != 1:
        print("!! 锚点出现 {} 次（应为 1）".format(src.count(ANCHOR)))
        return 1

    src = src.replace(ANCHOR, BLOCK + ANCHOR, 1)
    print("将在「{}」之前插入 {} 行".format(ANCHOR, len(BLOCK.splitlines())))
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    README.write_text(src, encoding="utf-8")
    print("已写入 {}".format(README.relative_to(ROOT)))

    lines = README.read_text(encoding="utf-8").splitlines()
    print("\n---- 结果（1~32 行）----")
    for i, l in enumerate(lines[:32], 1):
        print("{:4d}| {}".format(i, l[:86]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
