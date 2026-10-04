# -*- coding: utf-8 -*-
"""GitHub 风格的标题锚点（README 目录与它的校验共用这一份，避免两边算法不一致）。

GitHub 的规则（实测归纳）：
  1. 转小写；
  2. **删掉**标点与行内标记（`.` `/` `、` `（）` `` ` `` `*` `:` `+` 等一律**删除**，
     不是替换成连字符）；
  3. 空格 → `-`；
  4. 保留：中日韩文字、字母、数字、`_`、`-`。

踩过的坑：一开始把标点"替换"成 `-`，于是
`## 四、bots_config.json 放在哪里` 生成出 `#四-botsconfigjson-放在哪里`，
而 GitHub 实际是 `#四botsconfigjson-放在哪里` —— 目录点不动。
关键是**顺序**：先删标点，再把空白换成连字符。

显式锚点（2026-10-04，真机反馈"有一个跳转出问题"）
--------------------------------------------------
上面这套规则是**归纳**出来的，而 GitHub 的实现（github-slugger）只清掉
ASCII 标点 + `U+2000–206F` + `U+2E00–2E7F` 这些区段 —— **中文标点**
（`、` `？` `：` `（）` `「」` `，`）到底留不留，只能靠猜；一旦猜错，
中文标题的目录链接就点不动（本仓库有 30 多个这样的标题）。

所以：**标题里带中文标点时，在标题上一行放一个显式锚点**，目录指向它，
从此不再依赖 GitHub 的标点规则：

    <a id="深色模式下日志文字是黑的"></a>
    ### 深色模式下日志文字是黑的？

`heading_anchor()` 会优先用这种显式 id；`check_doc_links.py` 会检查
"带中文标点的标题必须配显式锚点"，免得以后又踩。
"""

import re

#: 需要删除的标点与行内标记（保留字母/数字/汉字/_/-）
PUNCTUATION = re.compile(
    r"[`*_~\[\](){}<>「」『』【】《》（）〈〉〔〕"
    r"、。，．,.!！?？:：;；'\"“”‘’…·～〜|｜/\\+＋=＝&＆@＠#$%^&*※°]"
)

#: 显式锚点：单独一行 `<a id="xxx"></a>`
ANCHOR_LINE = re.compile(r"^\s*<a\s+id=\"([^\"]+)\"\s*>\s*</a\s*>\s*$", re.I)

#: 两种实现可能不一致的标点：非 ASCII、非字母数字、非空白，
#: 且**不在** github-slugger 明确会删除的那两个区段里（U+2000–206F / U+2E00–2E7F）。
#: 中文标点（`、` `？` `（）` `「」` `，` `：`）全落在这里 —— 它们必须配显式锚点。
UNSAFE_RANGES = ((0x2000, 0x206F), (0x2E00, 0x2E7F))


def slugify(text: str) -> str:
    """把标题文字转成 GitHub 锚点（不含开头的 `#`）。"""
    s = text.strip().lower()
    s = PUNCTUATION.sub("", s)                 # ① 先删标点
    s = re.sub(r"[\s\u3000]+", "-", s)         # ② 空白转连字符
    s = re.sub(r"[^\w\u4e00-\u9fff-]", "", s)  # ③ 其余非词字符删掉
    return re.sub(r"-{2,}", "-", s).strip("-")


def unsafe_punctuation(text: str) -> str:
    """标题里"两种锚点算法可能不一致"的字符（没有则返回空串）。"""
    bad = []
    for char in text:
        code = ord(char)
        if code < 128 or char.isspace() or char.isalnum():
            continue
        if any(low <= code <= high for low, high in UNSAFE_RANGES):
            continue
        bad.append(char)
    return "".join(sorted(set(bad)))


def explicit_anchor(line: str) -> str:
    """这一行如果是显式锚点，返回它的 id，否则返回空串。"""
    match = ANCHOR_LINE.match(line or "")
    return match.group(1) if match else ""


def heading_anchor(lines, index: int, title: str) -> str:
    """标题的锚点：优先用它上一行的显式 `<a id>`，否则按 slugify 推。

    `lines` 是整篇文档按行拆开的结果，`index` 是标题所在行的下标。
    """
    if index > 0:
        found = explicit_anchor(lines[index - 1])
        if found:
            return found
    return slugify(title)


if __name__ == "__main__":
    # 自测：拿 README 里真实的几个标题验证
    cases = {
        "四、bots_config.json 放在哪里": "四botsconfigjson-放在哪里",
        "3.3 外观（浅色 / 深色 / 跟随系统）": "33-外观浅色-深色-跟随系统",
        "界面状态的存放位置（QSettings）": "界面状态的存放位置qsettings",
        "左侧列表的记忆规则（nav 前缀）": "左侧列表的记忆规则nav-前缀",
        "3.2 右侧分屏布局（每个程序一格 cmd/终端）": "32-右侧分屏布局每个程序一格-cmd终端",
    }
    bad = 0
    for title, expect in cases.items():
        got = slugify(title)
        ok = got == expect
        bad += 0 if ok else 1
        print("  {} {:<44} → {}".format("OK " if ok else "!! ", title[:44], got))
        if not ok:
            print("      期望 {}".format(expect))

    print("\n危险标点识别：")
    for title, expect in (("深色模式下日志文字是黑的？", "？"),
                          ("窗格上的「启动/停止/重启」到底作用在哪些程序？", "「」？"),
                          ("怎么跑起来（先看这里）", "（）"),
                          ("3.3 外观（浅色 / 深色 / 跟随系统）", "（）"),
                          ("四、bots_config.json 放在哪里", "、"),
                          ("纯 ASCII 标题 title", "")):
        got = unsafe_punctuation(title)
        ok = got == expect
        bad += 0 if ok else 1
        print("  {} {:<44} → {!r}".format("OK " if ok else "!! ", title[:44], got))

    print("\n结果：", "全部通过" if not bad else "{} 个不符".format(bad))
    raise SystemExit(1 if bad else 0)
