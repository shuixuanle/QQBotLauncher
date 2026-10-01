# -*- coding: utf-8 -*-
"""定位"Could not parse stylesheet"：把 nav_tree_qss 拆成片段逐个试。

Qt 解析失败时会向 stderr 打印警告，所以这里按片段逐个 setStyleSheet，
看哪一片触发警告（警告会出现在输出里，并标注片段编号）。

只读。用法：
    python tools\\diagnostics\\_qss_parse_check.py
"""

import sys

from PyQt6.QtWidgets import QApplication, QTreeWidget

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.ui import theme as theme_tokens  # noqa: E402


def split_rules(qss: str) -> list:
    """把样式表拆成单条规则（按 } 切分，保留花括号）。"""
    rules = []
    depth = 0
    current = []
    for char in qss:
        current.append(char)
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                text = "".join(current).strip()
                if text:
                    rules.append(text)
                current = []
    tail = "".join(current).strip()
    if tail:
        rules.append(tail)
    return rules


def main() -> int:
    app = QApplication(sys.argv[:1])
    tree = QTreeWidget()
    tree.setObjectName("navTree")

    print("=" * 70)
    print("被测样式表：nav_tree_qss（浅色 / 深色）")
    print("=" * 70)

    for mode, dark in (("浅色", False), ("深色", True)):
        theme_tokens.apply_theme(app, theme_tokens.MODE_DARK if dark
                                 else theme_tokens.MODE_LIGHT)
        app.processEvents()
        qss = theme_tokens.nav_tree_qss(tree)
        rules = split_rules(qss)
        print("\n########## {} 模式：共 {} 条规则 ##########".format(mode, len(rules)))

        print("--- 先整段设置一次（若下面出现 Could not parse，说明整段有问题）---")
        tree.setStyleSheet(qss)
        app.processEvents()

        print("--- 再逐条设置（触发警告的那条就是问题规则）---")
        for index, rule in enumerate(rules, 1):
            head = rule.split("{", 1)[0].strip()
            tree.setStyleSheet(rule)
            app.processEvents()
            marker = "箭头" if "image: url(" in rule else ""
            print("    [{}] {:<58} {}".format(index, head[:58], marker))

        # 单独验证 SVG 语法：不带 data: 前缀的写法、带引号包围的写法
        print("--- SVG 写法对照 ---")
        svg_body = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12'>"
            "<polygon points='4,2 4,10 9,6' fill='#d6d6d6'/></svg>"
        )
        candidates = [
            ("单引号 + utf8 前缀", "url(data:image/svg+xml;utf8,{})".format(svg_body)),
            ("双引号包围", 'url("data:image/svg+xml;utf8,{}")'.format(svg_body)),
            ("base64", "url(data:image/svg+xml;base64,"
                       "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4=)"),
        ]
        for name, value in candidates:
            tree.setStyleSheet(
                "QTreeWidget::branch:open:has-children {{ image: {}; }}".format(value)
            )
            app.processEvents()
            print("    试：{}".format(name))

    tree.close()
    print("\n判读：哪一行下面紧跟 'Could not parse stylesheet'，那条规则就是元凶；")
    print("      SVG 写法对照里只有不报错的那种写法可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
