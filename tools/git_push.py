# -*- coding: utf-8 -*-
"""把本项目推送到 GitHub —— 不把 token 写进任何文件、不留在 git 配置里。

为什么单独写脚本：直接 `git remote add https://<token>@github.com/...` 会把
token 明文写进 `.git/config`（并且会随 reflog/日志泄漏）。这里改用一次性 URL，
推送成功后不保留任何痕迹。

用法（先建好本地仓库并提交，见 README 的「上传到 GitHub」一节）：

    :: ① 先在网页上新建空仓库（不要勾选 README/.gitignore/LICENSE）
    :: ② 然后推送（会提示输入 token，输入时不回显）
    python tools\\git_push.py --user <你的GitHub用户名> --repo <仓库名>

    :: 想先看看会发生什么
    python tools\\git_push.py --user someone --repo demo --dry-run

Token 需要 `repo` 权限（经典 token）或 Contents: Read and write（细粒度 token）。
本脚本不会把 token 写到磁盘：进程退出即消失，仓库里也不会留下记录。
"""

import argparse
import getpass
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args, **kwargs):
    """执行命令并回显（token 会被替换成 *** 后再打印）。"""
    display = []
    for arg in args:
        if isinstance(arg, str) and "@github.com" in arg:
            display.append("https://***@github.com" + arg.split("@github.com", 1)[1])
        else:
            display.append(arg)
    print("  $ {}".format(" ".join(display)))
    return subprocess.run(args, cwd=str(ROOT), check=False, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="推送本项目到 GitHub（token 不落盘）")
    parser.add_argument("--user", required=True, help="GitHub 用户名")
    parser.add_argument("--repo", required=True, help="仓库名（网页上已建好的空仓库）")
    parser.add_argument("--branch", default="main", help="分支名，默认 main")
    parser.add_argument("--message", default="", help="提交信息（默认自动生成）")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不推送")
    parser.add_argument("--token", default="", help="直接提供 token（不推荐，会进 shell 历史）")
    args = parser.parse_args()

    if not (ROOT / ".git").exists():
        print("!! 这里还不是 git 仓库。先在项目根目录执行：git init")
        return 1

    # ---- 前置检查 ----
    print("[1] 检查仓库状态")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(ROOT),
        capture_output=True, text=True, check=False,
    )
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        print("  有 {} 项未提交的改动：".format(len(dirty)))
        for line in dirty[:12]:
            print("     {}".format(line))
        if len(dirty) > 12:
            print("     ...还有 {} 项".format(len(dirty) - 12))
        print("  建议先提交（或用 --dry-run 只看不改）。")
    else:
        print("  工作区干净 ✓")

    log = subprocess.run(
        ["git", "log", "--oneline", "-5"], cwd=str(ROOT),
        capture_output=True, text=True, check=False,
    )
    print("  最近提交：")
    for line in (log.stdout or "  （还没有提交）").splitlines():
        print("     {}".format(line))

    # 确认敏感文件确实被忽略
    print("\n[2] 确认不该上传的文件已被忽略")
    for name in ("bots_config.json", "bots_config.json.bak", "nav_debug.log",
                 "build/work", "dist"):
        probe = subprocess.run(
            ["git", "check-ignore", "-q", name], cwd=str(ROOT), check=False,
        )
        mark = "已忽略 ✓" if probe.returncode == 0 else "!! 未被忽略（请检查 .gitignore）"
        print("     {:<26} {}".format(name, mark))

    if args.dry_run:
        print("\n（dry-run：不推送。确认无误后去掉 --dry-run 再执行）")
        return 0

    # ---- 取 token ----
    print("\n[3] 需要 GitHub 凭据")
    token = args.token or getpass.getpass(
        "  粘贴 Personal Access Token（输入时不显示，不会保存到磁盘）："
    ).strip()
    if not token:
        print("!! 没有提供 token，已取消。")
        return 1

    remote_url = "https://{}:{}@github.com/{}/{}.git".format(
        args.user, token, args.user, args.repo
    )
    clean_url = "https://github.com/{}/{}.git".format(args.user, args.repo)

    # ---- 推送（URL 只用于这一次命令，不写进 config）----
    print("\n[4] 推送 {} -> {}".format(args.branch, clean_url))
    push = run(["git", "push", remote_url, "{}:{}".format(args.branch, args.branch)])

    # ---- 成功后登记一个**不含 token** 的 origin，方便以后 git push ----
    if push.returncode == 0:
        print("\n[5] 登记远程地址（不含 token）")
        existing = subprocess.run(
            ["git", "remote", "get-url", "origin"], cwd=str(ROOT),
            capture_output=True, text=True, check=False,
        )
        if existing.returncode == 0:
            run(["git", "remote", "set-url", "origin", clean_url])
        else:
            run(["git", "remote", "add", "origin", clean_url])
        print("\n完成 ✓ 之后可以用普通方式推送：git push -u origin {}".format(args.branch))
        print("（首次会弹凭据窗口，登录一次即可；token 不会被写进仓库）")
        return 0

    print("\n!! 推送失败。常见原因：")
    print("   · token 权限不足（需要 repo 权限 / Contents: Read and write）")
    print("   · token 已过期或被撤销")
    print("   · 仓库名或用户名写错，或仓库还没在网页上创建")
    print("   · 远程已有提交（例如建仓库时勾了 README）—— 先 git pull --rebase")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
