# -*- coding: utf-8 -*-
"""用户手改了 README：把「作者的话」移到了「它适合谁」表格之后（`## 功能一览` 之前），
并去掉了小标题、段间空行与结尾注记。这里按**新位置**重新排版。

保留用户选择的位置，只做排版：
  · 加回 `#### 作者的话`（四级小标题，低调）；
  · 四段【】之间各留一个空行；
  · 结尾补一句朴素注记；
  · 顺手清掉标题后多余的空行。

正文一字不改。

用法：
    python tools\\maintenance\\restyle_author_note.py            # 预览
    python tools\\maintenance\\restyle_author_note.py --apply    # 写入
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

HEADING = "#### 作者的话"
NOTE = "（以上这段是作者自己写的；本 README 其余部分由 AI 生成。）"
NEXT_SECTION = "## 功能一览"


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")

    # ---- 找到【】块所在的那一段（从第一个【到最后一个】）----
    blocks = re.findall(r"【[^】]*】", src)
    if not blocks:
        print("!! README 里找不到【】手记")
        return 1
    first = src.find(blocks[0])
    last_end = src.find(blocks[-1]) + len(blocks[-1])
    print("  找到 {} 段手记：".format(len(blocks)))
    for i, b in enumerate(blocks, 1):
        print("    {}. {} 字符 | {}".format(i, len(b), b[:36]))

    # ---- 重建这一段 ----
    new_block = HEADING + "\n\n" + "\n\n".join(blocks) + "\n\n" + NOTE + "\n\n"
    src = src[:first] + new_block + src[last_end:]

    # ---- 清理：标题后多余空行、注记与下一节之间的空行数量 ----
    src = re.sub(r"\n{3,}", "\n\n", src)          # 三个以上换行压成两个
    src = src.replace("# QQBot 启动管理器\n\n\n---", "# QQBot 启动管理器\n\n---")
    if NEXT_SECTION in src:
        src = re.sub(
            r"(" + re.escape(NOTE) + r")\s*\n+(" + re.escape(NEXT_SECTION) + r")",
            r"\1\n\n---\n\n\2",
            src,
        )

    print("\n  将改为：小标题 + 段间空行 + 结尾注记（位置不动）")
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    README.write_text(src, encoding="utf-8")
    print("  已写入 {}".format(README.relative_to(ROOT)))

    lines = README.read_text(encoding="utf-8").splitlines()
    print("\n  ---- 结果（前 4 行 + 手记附近）----")
    for i, l in enumerate(lines[:4], 1):
        print("  {:3d}| {}".format(i, l[:70]))
    print("   ...")
    for i, l in enumerate(lines, 1):
        if HEADING in l:
            for j in range(i - 1, min(i + 12, len(lines))):
                print("  {:3d}| {}".format(j + 1, lines[j][:70]))
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
