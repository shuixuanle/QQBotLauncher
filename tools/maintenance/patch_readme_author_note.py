# -*- coding: utf-8 -*-
"""把作者的「作者的话」插入 README（位置：标题之后、正文之前）。

用户要求：**内容原样，位置不用改，颜色大小随意** —— 所以文字一字不改，
只加排版（引用块 + 小标题），让它明显区别于 AI 生成的正文。

用法：
    python tools\\maintenance\\patch_readme_author_note.py            # 预览
    python tools\\maintenance\\patch_readme_author_note.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"
NOTE_SOURCE = Path(r"C:\Users\Strix\Desktop\新建 文本文档.txt")

ANCHOR = "> **这是什么**：一个**个人用**的 Windows 桌面工具，把散落在各处的"


def load_note() -> str:
    """读取作者原文（保持一字不改）。"""
    raw = NOTE_SOURCE.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(encoding).strip()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace").strip()


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")

    if "## 作者的话" in src:
        print("已经插入过「作者的话」，跳过。")
        return 0
    if src.count(ANCHOR) != 1:
        print("!! 锚点出现 {} 次（找不到应当插入的位置）".format(src.count(ANCHOR)))
        return 1

    note = load_note()
    block = (
        "## 作者的话\n\n"
        "> *（以下由作者手写，未经 AI 改写 —— 与后文 AI 生成的说明风格明显不同，"
        "不用猜哪段是人写的 :D）*\n>\n"
        + "\n".join("> " + line if line.strip() else ">" for line in note.splitlines())
        + "\n\n---\n\n"
    )

    src = src.replace(ANCHOR, block + ANCHOR, 1)
    print("将插入 {} 行（原文 {} 字符）".format(
        len(block.splitlines()), len(note)))
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    README.write_text(src, encoding="utf-8")
    print("已写入 {}".format(README.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
