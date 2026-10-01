# -*- coding: utf-8 -*-
"""整理「作者的话」：只保留一份，并调低存在感。

用户反馈（附截图）：上框（我加的）与下框（作者原本写在 README 里的）**内容重复**，
要删掉下框那份；并且**这些话不需要太显眼**。

因此：
  · 删掉末尾那份游离的重复文本；
  · 保留开头那份，但把 `##` 二级标题降为 `####`，去掉我加的那行"斜体说明"，
    改成文末一句朴素的小字注记；
  · 正文一字不改。

用法：
    python tools\\maintenance\\fix_readme_author_note.py            # 预览
    python tools\\maintenance\\fix_readme_author_note.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

#: 我插入的那份（含标题与斜体说明）—— 要替换成低调版本
OLD_HEAD = """## 作者的话

> *（以下由作者手写，未经 AI 改写 —— 与后文 AI 生成的说明风格明显不同，不用猜哪段是人写的 :D）*
>
"""

NEW_HEAD = """#### 作者的话

"""

#: 文末那句朴素的注记（替换原来那行斜体说明的位置：挪到引用块之后）
NOTE_LINE = "（以上这段是作者自己写的；本 README 其余部分由 AI 生成。）"


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")
    changed = []

    # ---- ① 删掉末尾那份重复文本（从"【作者的话："到那行日期后的 ---）----
    dup_start = src.find("\n\n【作者的话：")
    if dup_start == -1:
        print("  跳过（末尾没有重复文本）")
    else:
        tail_marker = "【2026年10月1日（国庆节快乐）】"
        dup_end = src.find(tail_marker, dup_start)
        if dup_end == -1:
            print("!! 找不到重复文本的结尾")
            return 1
        dup_end += len(tail_marker)
        # 连同紧随其后的换行/分隔线一起清掉
        after = src[dup_end:]
        strip_len = len(after) - len(after.lstrip("\n"))
        after = after[strip_len:]
        if after.startswith("---\n"):
            after = after[4:]
        src = src[:dup_start] + "\n" + after
        changed.append("删除末尾重复的「作者的话」")

    # ---- ② 保留的那份降级排版 ----
    if OLD_HEAD in src:
        src = src.replace(OLD_HEAD, NEW_HEAD, 1)
        changed.append("标题降级为 ####（不再抢眼）")
    else:
        print("  跳过（开头排版已是新版或结构不同）")

    # ---- ③ 在引用块后补一句朴素注记 ----
    if NOTE_LINE not in src:
        marker = "> 【2026年10月1日（国庆节快乐）】"
        if src.count(marker) == 1:
            src = src.replace(marker, marker + "\n\n" + NOTE_LINE, 1)
            changed.append("补一句朴素注记")
        else:
            print("!! 找不到引用块结尾（找到 {} 次）".format(src.count(marker)))

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
