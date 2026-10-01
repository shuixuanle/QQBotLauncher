# -*- coding: utf-8 -*-
"""修正 README 外观部分对"浅色模式日志区底色"的错误描述。

事实（代码为准）：
    LIGHT_WINDOW  = "#f0efe9"     ← 界面主体色
    LOG_LIGHT_BG  = "#f0efe9"     ← 浅色日志区底色：**与主体同色**（不是白底）
    LOG_DARK_BG   = "#1e1f22"     ← 深色日志区比窗口 #2b2b2b 更深

用户实测确认："浅色模式下日志区的颜色实际是浅黄，也就是界面主体颜色…
感觉不是特别别扭，所以我没让你改，保留即可"。
所以这里不仅要把"白底"改对，还要写明**这是有意为之**，
免得以后被当成 bug"优化"掉。

用法：
    python tools\\maintenance\\fix_readme_theme_colors.py            # 预览
    python tools\\maintenance\\fix_readme_theme_colors.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

#: (旧文本, 新文本, 说明)
REPLACEMENTS = [
    (
        "| **浅色模式** | 始终浅色：柔和黄灰底（`#f0efe9`）＋ 近黑字（`#1f1f1f`），日志区白底 |",
        "| **浅色模式** | 始终浅色：柔和黄灰底（`#f0efe9`）＋ 近黑字（`#1f1f1f`）；"
        "日志区**与主体同色**（`#f0efe9`，不是白底，见下方说明） |",
        "表格里「日志区白底」→ 与主体同色",
    ),
    (
        """- **日志区配色**：浅色下白底深字，深色下深底浅字（跟随主题）。
  它是用**控件调色板**着色的（不是只靠样式表），所以切换后一定会重绘。""",
        """- **日志区配色**（跟随主题，深浅两套）：
  - **浅色**：底色 `#f0efe9` —— **故意与界面主体同色**（不是纯白），配近黑字 `#1f1f1f`。
    这样日志区与窗体连成一片，不会在浅色界面里"挖"出一块刺眼的白方块；
    深色模式下它才比窗口更深（`#1e1f22`）以形成层次。这是有意设计，不是漏配。
  - **深色**：底色 `#1e1f22`、文字 `#d6d6d6`。
  - 着色方式是**控件调色板 + 样式表双写**（不只靠样式表），所以切换后一定会重绘，
    也不会出现"深色下日志文字是黑的"那种情况。""",
        "「日志区配色」小节改写",
    ),
]


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")
    changed = []
    for old, new, label in REPLACEMENTS:
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

    print("\n---- 结果 ----")
    for i, line in enumerate(README.read_text(encoding="utf-8").splitlines(), 1):
        if "浅色模式** |" in line or "日志区配色" in line:
            print("  {:4d}| {}".format(i, line[:100]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
