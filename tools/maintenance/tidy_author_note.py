# -*- coding: utf-8 -*-
"""作者手记排版微调：每段之间留空行 + 去掉引用块（更朴素、行更短）。

用户要求：
  · 每个【】各占一行；
  · 我写的话不需要太显眼。

现状：四段**已经**各自独占一行（第 5~8 行），但
  · 第 8 行的 `】` 紧邻第 12 行的 `---`，视觉上像连在一起；
  · 引用块（`>`）会加一层左缩进 + 灰色竖线，行宽被压缩，长段落更容易折行。
这里：段间加空行、去掉 `>`。正文一字不改。

用法：
    python tools\\maintenance\\tidy_author_note.py            # 预览
    python tools\\maintenance\\tidy_author_note.py --apply    # 写入
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

NOTE_LINE = "（以上这段是作者自己写的；本 README 其余部分由 AI 生成。）"


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")

    # 定位「#### 作者的话」到注记之间的那段
    start = src.find("#### 作者的话")
    if start == -1:
        print("!! 找不到「#### 作者的话」")
        return 1
    end = src.find(NOTE_LINE, start)
    if end == -1:
        print("!! 找不到结尾注记")
        return 1

    block = src[start:end]
    blocks = re.findall(r"【[^】]*】", block)
    if not blocks:
        print("!! 没找到任何【】段落")
        return 1
    print("  找到 {} 段手记：".format(len(blocks)))
    for i, b in enumerate(blocks, 1):
        print("    {}. {} 字符 | {}".format(i, len(b), b[:38]))

    if block.count("\n\n> 【") == len(blocks) - 1 and not block.startswith("#### 作者的话\n\n【"):
        print("\n  看起来已经是「段间空行 + 无引用块」的版本。")
        return 0

    # 重建这一段：标题 + 空行 + 每段一行（段间空行）
    new_block = "#### 作者的话\n\n" + "\n\n".join(blocks) + "\n\n"
    src = src[:start] + new_block + src[end:]

    print("\n  将改为：每段独占一行、段间空一行、去掉 > 引用块")
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    README.write_text(src, encoding="utf-8")
    print("  已写入 {}".format(README.relative_to(ROOT)))

    # 回读确认
    lines = README.read_text(encoding="utf-8").splitlines()
    print("\n  ---- 结果 ----")
    for i, l in enumerate(lines[:16], 1):
        print("  {:3d}| {}".format(i, l[:70]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
