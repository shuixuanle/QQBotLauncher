# -*- coding: utf-8 -*-
"""静态检查：仓库布局保持整洁（根目录只放该放的、生成物必须被忽略）。

为什么需要
----------
真机需求就是一句"请将该项目整理一下"。整理完最怕的是**再乱回去**：
  · 调试时随手写的日志 / 结果文件又出现在根目录；
  · 新写的检查器把临时文件丢进仓库，却忘了加 .gitignore
    （真机踩过：三个 `_*_result.json` 就是这么来的）；
  · `tools/` 下多出一堆说不清用途的脚本，新人不知道该跑哪个；
  · README 的「项目结构」写得还是去年的样子（文件早改名了）。

所以把"整洁"也变成断言，分五条：
  [1] 根目录白名单：出现清单外的条目就报出来
  [2] tools/ 分类：`check_*.py`（检查器）/ `_*.py`（夹具与共享件）/ 白名单工具 / 子目录
  [3] 生成物必须被 .gitignore 覆盖（日志、`_tmp/`、`_local/`、检查器结果文件、`__pycache__/`）
  [4] 仓库里不许跟踪 `__pycache__` / `*.pyc`
  [5] README「项目结构」里提到的文件名必须真实存在（用 basename 核对，防止文档漂移）

用法：
    python tools\\check_repo_layout.py
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
README = ROOT / "README.md"

#: 根目录允许出现的条目（白名单）。想加东西时**先加到这里**，
#: 这一步的意义就是逼自己想清楚"这个东西该不该待在根目录"。
ALLOWED_ROOT = {
    ".gitignore",
    "README.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "requirements.txt",
    "main.py",
    "launcher.py",
    "bots_config.example.json",
    "启动方式说明.md",
    "启动（普通模式）.bat",
    "启动（管理员模式）.bat",
    "启动（exe·临时目录修复）.bat",
    "启动管理器（普通）.pyw",
    "启动管理器（管理员）.pyw",
    "app",
    "tools",
    "docs",
    "scripts",
    "build",
    ".git",
    # 本机产物（都在 .gitignore 里）
    "bots_config.json",
    "bots_config.json.bak",
    "_tmp",
    "_local",
    "__pycache__",
}

#: tools/ 下允许出现的脚本（检查器与共享件之外的）
ALLOWED_TOOLS = {
    "run_all_checks.py",
    "run_all_checks.bat",
    "md_anchor.py",          # README 目录锚点算法（与目录生成共用）
    "palette_studio.py",     # 配色工作台（命令行入口）
    "build_exe.py",          # 一键打包
    "diagnose_temp.py",      # exe 临时目录诊断
    "find_hydrant.py",       # 找消防栓编译产物目录，并写进 scripts/hydrant_dir.txt
    "git_push.py",           # 推送助手
    "_theme_probe.py",       # 检查器共享：抠 theme.py 的纯函数
    "_detach_probe.py",      # 夹具：脱离启动探针（check_alloc_console_live 用）
}

#: tools/ 下允许出现的子目录
ALLOWED_TOOL_DIRS = {"diagnostics", "maintenance", "__pycache__"}

#: 必须被 .gitignore 覆盖的生成物样例（相对仓库根）
MUST_BE_IGNORED = (
    "close_debug.log",
    "nav_debug.log",
    "theme_debug.log",
    "launcher_error.log",
    "_tmp/",
    "_local/",
    "tools/_alloc_result.json",
    "tools/_detach_result.json",
    "tools/_gui_result.json",
    "__pycache__/",
    "bots_config.json",
)

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def git_ignored(rel_path: str):
    """路径是否被忽略：True / False / None（拿不到 git）。"""
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "--", rel_path],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def git_tracked():
    """仓库里被跟踪的文件列表（拿不到 git 返回 None）。"""
    try:
        proc = subprocess.run(
            ["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def check_root() -> None:
    print("[1] 根目录白名单")
    extra, ignored_here = [], []
    for entry in sorted(ROOT.iterdir(), key=lambda path: path.name):
        if entry.name in ALLOWED_ROOT:
            continue
        # 被 .gitignore 忽略的本机产物（运行时日志、缓存、配置……）不算"乱"：
        # 管理器一跑就会写出 close_debug.log 之类的东西，那是预期的。
        # 这条检查针对的是"会被提交的东西"。
        if git_ignored(entry.name) is True:
            ignored_here.append(entry.name)
            continue
        extra.append(entry.name + ("/" if entry.is_dir() else ""))
    is_git = (ROOT / ".git").exists()
    hint = "" if not extra else "（确实需要就加进 ALLOWED_ROOT 并写清用途）"
    check("根目录没有清单外的条目{}".format(hint), not extra, str(extra))
    if ignored_here:
        print("     （已忽略的本机产物 {} 个：{}）".format(
            len(ignored_here), ", ".join(ignored_here[:6])))
    if not is_git:
        print("     （提示：这不是 git 仓库，稍后几条 git 相关检查会跳过）")


def check_tools() -> None:
    print("\n[2] tools/ 分类清楚")
    unexpected = []
    for entry in sorted(TOOLS.iterdir(), key=lambda path: path.name):
        if entry.is_dir():
            if entry.name not in ALLOWED_TOOL_DIRS:
                unexpected.append(entry.name + "/")
            continue
        name = entry.name
        if name in ALLOWED_TOOLS or name.startswith("check_") and name.endswith(".py"):
            continue
        if name.startswith("_") and name.endswith(".py"):
            continue      # 夹具 / 共享件
        if name.endswith((".md", ".txt", ".json")):
            continue      # 说明与数据文件
        unexpected.append(name)
    check("tools/ 下每个文件都有明确身份（检查器/夹具/工具）", not unexpected,
          str(unexpected))
    scripts = sorted(TOOLS.glob("check_*.py"))
    print("     检查器 {} 个 · 夹具/共享件 {} 个".format(
        len(scripts),
        len([p for p in TOOLS.glob("_*.py")])))


def check_ignored() -> None:
    print("\n[3] 生成物都被 .gitignore 覆盖")
    if git_ignored("README.md") is None:
        check("能拿到 git（拿不到就跳过这一条）", True, "跳过：git 不可用")
        return
    not_ignored = [name for name in MUST_BE_IGNORED if not git_ignored(name)]
    check("日志 / 临时目录 / 检查器结果文件都已忽略", not not_ignored, str(not_ignored))
    check("README 自身**没有**被忽略（别把正经文件忽略了）",
          not git_ignored("README.md"))

    print("\n[4] 仓库里没有跟踪缓存文件")
    tracked = git_tracked()
    if tracked is None:
        check("能拿到 git ls-files", True, "跳过：git 不可用")
        return
    junk = [name for name in tracked
            if "__pycache__" in name or name.endswith((".pyc", ".pyo"))]
    check("没有 __pycache__ / *.pyc 被跟踪", not junk, str(junk[:5]))


def check_readme_tree() -> None:
    print("\n[5] README「项目结构」提到的文件真实存在")
    text = README.read_text(encoding="utf-8")
    block = re.search(r"## 六、项目结构\s*```(.*?)```", text, re.S)
    if block is None:
        check("README 里有「六、项目结构」代码块", False)
        return
    names = []
    for line in block.group(1).splitlines():
        body = line.split("#")[0]                       # 去掉行尾注释
        body = re.sub(r"^[\s│├└─]+", "", body).strip()  # 去掉树形符号
        if not body or body.endswith("/"):
            continue                                     # 目录行不核对
        if any(char in body for char in (" ", "*", "（", "/")):
            continue                                     # 通配/说明性写法跳过
        names.append(body)
    observed = {path.name for path in ROOT.rglob("*")
                if "__pycache__" not in path.parts and ".git" not in path.parts}
    missing = sorted({name for name in names if name not in observed})
    check("结构图里的 {} 个文件名都能找到".format(len(names)), not missing, str(missing))


def main() -> int:
    check_root()
    check_tools()
    check_ignored()
    check_readme_tree()
    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
