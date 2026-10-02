# -*- coding: utf-8 -*-
"""静态验证：左栏分支箭头样式（无需 PyQt6）。

验证三件事：
  1. `nav_branch_qss` 生成的规则能被外层 `.format()` 安全拼接（花括号已转义）；
  2. 取不到箭头图片时**不下发 image 规则**（宁可用原生箭头，也不让 Qt 解析失败）；
  3. 项目里不再使用内联 SVG 的 data URL。

背景：`url(data:image/svg+xml;utf8,<svg …>)` 里含 `:` `'` `,` `<` `>`，
Qt 会拒绝**整段**样式表（真机刷 `Could not parse stylesheet of object navPanel`，
该控件所有样式失效 → 深色下箭头/文字发黑）。因此改用 QPainter 画 PNG。

用法：
    python tools\\check_branch_qss.py
"""

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "app" / "ui" / "theme.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def load_namespace(src: str, tree: ast.AST) -> dict:
    """把几个纯字符串函数抽出来，在干净命名空间里执行（可注入替身）。

    坑（2026-10-02 修）：`theme.py` 顶部有 ``from __future__ import annotations``，
    所以 ``def nav_branch_qss(widget: Optional[QWidget] = None)`` 里的注解在**真模块**里
    是一串字符串、根本不求值；而这里是把函数源码单独抠出来 exec，
    少了那句 future import，注解就会在 def 时求值
    → ``NameError: name 'Optional' is not defined``，整个检查器红掉。

    补上 future import（必须是注入代码的**第一行**）即可与真模块环境一致 ——
    不需要给 Optional / QWidget 造替身。
    """
    want = ("nav_branch_qss", "nav_tree_qss")
    code = (
        "from __future__ import annotations\n"
        'DARK_TEXT = "#d6d6d6"\n'
        "import os\n"
        "WIDGET_DARK = True\n"
        "ARROW_URL = ''\n"
        "def is_dark(widget=None):\n"
        "    return WIDGET_DARK\n"
        "def _branch_arrow_url(color, direction):\n"
        "    return ARROW_URL.format(color=color, direction=direction)\n"
        "def nav_palette(widget=None):\n"
        "    return {'base': '#1e1f22', 'text': '#d6d6d6', 'border': '#3a3d41',\n"
        "            'sel': '#2f6fb5', 'sel_text': '#ffffff', 'hover': '#2a2a2a',\n"
        "            'button': '#3a3d41', 'disabled': '#9a9a9a'}\n"
    )
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            code += "\n" + (ast.get_source_segment(src, node) or "") + "\n"
    namespace: dict = {}
    exec(code, namespace)  # noqa: S102 - 只执行本项目自己的纯字符串函数
    return namespace

# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    src = THEME.read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns = load_namespace(src, tree)

    print("[1] 有箭头图片时：规则完整")
    ns["ARROW_URL"] = "url(C:/temp/branch_{direction}_{color}.png)"
    ns["WIDGET_DARK"] = True
    dark_rules = ns["nav_branch_qss"](None)
    check("深色：含 ::branch 基础规则", "::branch" in dark_rules)
    check("深色：含两条 image 规则", dark_rules.count("image:") == 2,
          "{} 处".format(dark_rules.count("image:")))
    check("深色：箭头带深色主题色 #d6d6d6", "#d6d6d6" in dark_rules)
    ns["WIDGET_DARK"] = False
    light_rules = ns["nav_branch_qss"](None)
    check("浅色：含两条 image 规则", light_rules.count("image:") == 2)
    check("浅色：箭头用深灰 #5a5a5a", "#5a5a5a" in light_rules)

    print("\n[2] 取不到图片时：不下发 image 规则（保证 Qt 不会解析失败）")
    ns["ARROW_URL"] = ""
    ns["WIDGET_DARK"] = True
    no_arrow = ns["nav_branch_qss"](None)
    check("无图片时不出现 image:", "image:" not in no_arrow, no_arrow[:60])
    check("无图片时仍保留 ::branch 背景规则", "::branch" in no_arrow)

    print("\n[3] 外层 format 安全性（花括号转义）")
    ns["ARROW_URL"] = "url(C:/temp/branch.png)"
    ns["WIDGET_DARK"] = True
    branch = ns["nav_branch_qss"](None)
    escaped = branch.replace("{", "{{").replace("}", "}}")
    tpl = (
        "QTreeWidget#navTree {{ color: {text}; }}"
        + "{branch}"
        + "QWidget#navPanel QLabel {{ color: {text}; }}"
    )
    try:
        out = tpl.format(text="#111", branch=escaped)
        ok, err = True, ""
    except (KeyError, IndexError, ValueError) as exc:
        ok, err, out = False, "{}: {}".format(type(exc).__name__, exc), ""
    check("转义后 format 成功", ok, err)
    check("结果里保留箭头图片", "image:" in out)
    check("结果里没有残留占位符", "{branch}" not in out)

    print("\n[4] 源码级防回归")
    fn_tree = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "nav_tree_qss"
    )
    tree_src = ast.get_source_segment(src, fn_tree) or ""
    check("深浅两条分支都拼了箭头样式", tree_src.count("{branch}") >= 2,
          "{} 处".format(tree_src.count("{branch}")))
    check("拼接前做了花括号转义",
          'nav_branch_qss(widget).replace("{", "{{").replace("}", "}}")' in tree_src)
    # 注意：docstring 里为了解释原因提到了 svg+xml，所以只在**去掉注释与文档字符串**
    # 的代码里检查，避免误报（检查器自己踩过这个坑）。
    code_only = [
        line for line in src.splitlines()
        if not line.strip().startswith("#")
    ]
    docstrings = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    code_only_text = "\n".join(code_only)
    for text in docstrings:
        code_only_text = code_only_text.replace(text, "")
    check("正式代码里没有内联 SVG 的 data URL", "svg+xml" not in code_only_text)
    check("箭头用 QPainter 画 PNG（不依赖 SVG 解析）",
          "def _branch_arrow_url" in src and "image.save(path, " in src)

    print("\n[5] 真正执行 _branch_arrow_url（用替身模拟 Qt，验证路径替换）")
    url_ok, detail = run_arrow_url_stub(src, tree)
    check("返回的是 url(正斜杠路径)", url_ok, detail)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


def run_arrow_url_stub(src: str, tree: ast.AST):
    """用替身 Qt 执行 `_branch_arrow_url`，验证：
      · 生成的是 `url(...)`；
      · 路径里的 Windows 反斜杠已全部换成正斜杠；
      · 颜色被用于文件名（说明不同主题会生成不同箭头）。
    """
    fn = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == "_branch_arrow_url"),
        None,
    )
    if fn is None:
        return False, "找不到 _branch_arrow_url"

    body = ast.get_source_segment(src, fn) or ""
    # 去掉 PyQt6 的 import（替身已提供同名类），其余原样执行
    body = "\n".join(
        line for line in body.splitlines()
        if "from PyQt6" not in line
    )

    stub = '''
import os
_BRANCH_ARROW_CACHE = {}
_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "qqbot_probe")
os.makedirs(_TMP, exist_ok=True)


class _Fmt:
    Format_ARGB32 = "argb32"


class QImage:
    # 真实调用是 QImage.Format.Format_ARGB32（嵌套枚举）
    Format = _Fmt

    def __init__(self, w, h, fmt=None):
        self.w, self.h = w, h

    def fill(self, *a):
        pass

    def save(self, path, kind):
        with open(path, "wb") as handle:
            handle.write(b"stub")


class QPointF:
    def __init__(self, x, y):
        self.x, self.y = x, y


class QPolygonF(list):
    pass


class _PenStyle:
    NoPen = "nopen"


class _RenderHint:
    Antialiasing = "aa"


class QPainter:
    # 真实调用是 QPainter.RenderHint.Antialiasing（嵌套枚举）
    RenderHint = _RenderHint

    def __init__(self, image):
        pass

    def setRenderHint(self, *a):
        pass

    def setBrush(self, *a):
        pass

    def setPen(self, *a):
        pass

    def drawPolygon(self, *a):
        pass

    def end(self):
        pass


class QColor:
    def __init__(self, value):
        self.value = value


class _Qt:
    GlobalColor = type("G", (), {"transparent": "transparent"})
    PenStyle = _PenStyle
    RenderHint = _RenderHint


Qt = _Qt


class _Tempfile:
    @staticmethod
    def gettempdir():
        return os.environ.get("TEMP", "/tmp")


tempfile = _Tempfile
'''
    namespace: dict = {}
    try:
        exec(stub + "\n" + body, namespace)  # noqa: S102 - 替身 + 本项目函数
        url = namespace["_branch_arrow_url"]("#d6d6d6", "right")
        url_down = namespace["_branch_arrow_url"]("#5a5a5a", "down")
    except Exception as exc:  # noqa: BLE001 - 诊断脚本，任何异常都要报出来
        return False, "{}: {}".format(type(exc).__name__, exc)

    if not url.startswith("url(") or not url.endswith(")"):
        return False, "返回值不是 url(...)：{}".format(url[:60])
    if "\\" in url:
        return False, "路径里还有反斜杠：{}".format(url[:80])
    if "d6d6d6" not in url:
        return False, "颜色没进文件名：{}".format(url[:80])
    if url_down == url:
        return False, "不同颜色/方向生成了同一个路径：{}".format(url)
    return True, url


if __name__ == "__main__":
    raise SystemExit(main())
