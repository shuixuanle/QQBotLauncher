# -*- coding: utf-8 -*-
"""静态检查：文档里的链接都指向真实存在的东西（不需要 PyQt6、不开窗口）。

为什么需要这个检查器（每一条都是踩过的坑）：

  · **站内锚点**：README 有几十个 `](#xxx)`（目录 + 正文交叉引用）。
    改一次标题就可能悄悄失效，而 GitHub 上点不动**没有任何报错** ——
    之前真出现过两处：`#四botsconfigjson-放在哪里`（多写了连字符）、
    `#33-外观浅色--深色--跟随系统`（标点算法把标点换成了连字符）。
    锚点算法与目录生成共用 `tools/md_anchor.py`，两边不会各算各的。
  · **截图**：README 里的 `docs/screenshots/*.png` 路径写错，GitHub 上就是一个破图标。
  · **最阴的一条**：图片本地确实存在，但路径落在 `.gitignore` 里 ——
    本机看得好好的，推上去却没有。这一条用 `git check-ignore` 查（没有 git 就跳过）。

用法：
    python tools\\check_doc_links.py
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 锚点算法与 README 目录生成共用同一份（避免"目录能点、正文点不动"）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from md_anchor import (explicit_anchor, heading_anchor,  # noqa: E402
                       slugify, unsafe_punctuation)

#: 要检查的文档；不存在的直接跳过（比如别人只拿走一部分文件）
DOCS = (
    "README.md",
    "CONTRIBUTING.md",
    "启动方式说明.md",
    "scripts/README.md",
)

#: `[文字](目标)` 与 `![文字](目标)`
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")

#: 这些后缀当作"图片"，额外做一次 gitignore 检查
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico")

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def headings(text: str) -> set:
    """收集文档里的标题锚点（跳过围栏代码块里的 `#` 行，例如 spec 示例）。

    两种来源都算数：
      · 标题自动推导的 slug；
      · 标题上一行的**显式锚点** `<a id="…"></a>`（中文标点的标题靠它，
        见 md_anchor.py 里"为什么不能依赖 GitHub 的标点规则"）。
    """
    found = set()
    in_fence = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            found.add(heading_anchor(lines, index, match.group(2).strip()))
    return found


def unsafe_headings(text: str) -> list:
    """标题里带"跨实现有歧义的标点"、却没有显式锚点的那些行。

    GitHub 的自动锚点只清掉 ASCII 标点 + 两个 Unicode 区段，**中文标点留不留
    只能靠猜**（真机反馈过"有一个跳转出问题"）。所以约定：
    标题里带中文标点 → 上一行必须放 `<a id="…"></a>`，目录指向它。
    """
    problems = []
    in_fence = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            continue
        title = match.group(2).strip()
        bad = unsafe_punctuation(title)
        if not bad:
            continue
        if index > 0 and explicit_anchor(lines[index - 1]):
            continue
        problems.append((index + 1, title[:40], bad))
    return problems


def git_ignored(rel_path: str):
    """路径是否被 .gitignore 忽略。返回 True / False / None（拿不到 git 就 None = 跳过）。"""
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "--", rel_path],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None  # 128 = 不是 git 仓库 / git 不可用


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    print("检查文档：", "、".join(DOCS))
    anchor_total = file_total = image_total = 0
    ignored_checked = 0

    for rel in DOCS:
        doc = ROOT / rel
        if not doc.exists():
            print("\n[{}] 不存在，跳过".format(rel))
            continue
        text = doc.read_text(encoding="utf-8", errors="replace")
        anchors = headings(text)
        # 带中文标点的标题必须配显式锚点（否则 GitHub 上的跳转要靠猜，真出过问题）
        problems = unsafe_headings(text)
        check("{}：带中文标点的标题都配了显式锚点（不靠 GitHub 猜标点）".format(rel),
              not problems, str(problems[:3]))
        bad_anchor, bad_file, ignored_image = [], [], []
        doc_ignored_checked = 0

        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            if target.startswith("#"):
                anchor_total += 1
                if target[1:] and target[1:] not in anchors:
                    bad_anchor.append(target)
                continue

            path = target.split("#")[0]
            if not path:
                continue
            file_total += 1
            absolute = (doc.parent / path)
            if not absolute.exists():
                bad_file.append(target)
                continue
            if path.lower().endswith(IMAGE_SUFFIXES):
                image_total += 1
                try:
                    rel_to_root = absolute.resolve().relative_to(ROOT)
                except ValueError:
                    continue  # 文档指向仓库外的文件：不归 .gitignore 管
                ignored = git_ignored(str(rel_to_root).replace("\\", "/"))
                if ignored is None:
                    continue
                ignored_checked += 1
                doc_ignored_checked += 1
                if ignored:
                    ignored_image.append(path)

        print("\n[{}] 标题 {} 个".format(rel, len(anchors)))
        check("站内锚点全部有效", not bad_anchor, "失效 = {}".format(bad_anchor) if bad_anchor else "")
        check("本地文件链接都存在", not bad_file, "缺失 = {}".format(bad_file) if bad_file else "")
        if doc_ignored_checked:
            check("图片没有被 .gitignore 忽略", not ignored_image,
                  "被忽略 = {}".format(ignored_image) if ignored_image else "")

    print("\n统计：站内锚点 {} · 本地链接 {} · 图片 {}（其中 {} 条查过 .gitignore）".format(
        anchor_total, file_total, image_total, ignored_checked))
    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
