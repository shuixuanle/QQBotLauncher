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
"""

import re

#: 需要删除的标点与行内标记（保留字母/数字/汉字/_/-）
PUNCTUATION = re.compile(
    r"[`*_~\[\](){}<>「」『』【】《》（）〈〉〔〕"
    r"、。，．,.!！?？:：;；'\"“”‘’…·～〜|｜/\\+＋=＝&＆@＠#$%^&*※°]"
)


def slugify(text: str) -> str:
    """把标题文字转成 GitHub 锚点（不含开头的 `#`）。"""
    s = text.strip().lower()
    s = PUNCTUATION.sub("", s)                 # ① 先删标点
    s = re.sub(r"[\s\u3000]+", "-", s)         # ② 空白转连字符
    s = re.sub(r"[^\w\u4e00-\u9fff-]", "", s)  # ③ 其余非词字符删掉
    return re.sub(r"-{2,}", "-", s).strip("-")


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
    print("\n结果：", "全部通过" if not bad else "{} 个不符".format(bad))
    raise SystemExit(1 if bad else 0)
