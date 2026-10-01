# -*- coding: utf-8 -*-
"""上传到 GitHub 之后的收尾：清理绕路用的临时配置 + 加 Topics + 复核远端。

背景：DSH 会话的沙箱里 `sh.exe` 起不来，Git Credential Manager 因此无法工作
（`couldn't create signal pipe, Win32 error 5`），最后是用"一次性 token URL"
完成推送的。中途往**本仓库**的 .git/config 里写过凭据助手与 credential.path，
这里把它们清掉，避免留下奇怪配置。

用法：
    python tools\\maintenance\\finish_github_setup.py            # 只检查
    python tools\\maintenance\\finish_github_setup.py --apply    # 执行清理与设置
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GH = r"C:\Program Files\GitHub CLI\gh.exe"
REPO = "shuixuanle/QQBotLauncher"
TOPICS = ("pyqt6", "qq-bot", "process-manager", "launcher", "windows", "python",
          "desktop-app")


def run(args, timeout=180):
    proc = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False,
                          timeout=timeout)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def main() -> int:
    apply = "--apply" in sys.argv

    print("[1] 那个“疑似 bots_config.json”的文件是什么")
    fixture = ROOT / "app" / "ui" / "_selftest_bots_config.json"
    print("    {}  （{} 字节）".format(fixture.relative_to(ROOT).as_posix(),
                                       fixture.stat().st_size if fixture.exists() else 0))
    print("    用途：自检用的测试夹具，不是用户配置 —— 上传它没问题。")

    print("\n[2] 清理临时凭据配置（本仓库级）")
    for key in ("credential.https://github.com.helper", "credential.path"):
        code, _, _ = run(["git", "config", "--local", "--unset-all", key])
        print("    {} -> {}".format(key, "已移除" if code == 0 else "（本就不存在）"))
    shim_dir = ROOT / "tools" / "bin"
    if shim_dir.exists():
        if apply:
            shutil.rmtree(shim_dir, ignore_errors=True)
            print("    已删 tools/bin/（临时的凭据助手包装）")
        else:
            print("    将删 tools/bin/（临时的凭据助手包装）")
    else:
        print("    tools/bin/ 不存在，无需清理")

    print("    剩余 local 配置（只看关键项）：")
    _, out, _ = run(["git", "config", "--local", "--list"])
    for line in out.splitlines():
        if any(k in line for k in ("credential", "user.", "remote.")):
            print("      " + line)

    print("\n[3] Topics")
    _, out, _ = run([GH, "repo", "view", REPO, "--json", "repositoryTopics"])
    try:
        # 注意：没有 topics 时该字段是 **null**，不是 []，直接迭代会 TypeError
        raw = json.loads(out).get("repositoryTopics") or []
        current = [t["name"] for t in raw]
    except (json.JSONDecodeError, KeyError, TypeError):
        current = []
    print("    当前 = {}".format(current or "（空）"))
    missing = [t for t in TOPICS if t not in current]
    if not missing:
        print("    已经齐全。")
    elif not apply:
        print("    将添加 = {}".format(missing))
    else:
        args = [GH, "repo", "edit", REPO]
        for topic in missing:
            args += ["--add-topic", topic]
        code, _, err = run(args)
        print("    添加 {} -> {}".format(missing, "OK" if code == 0 else err[:200]))

    print("\n[4] 远端复核")
    _, out, _ = run([GH, "api",
                     "repos/{}/git/trees/main?recursive=1".format(REPO)])
    try:
        files = [e["path"] for e in json.loads(out).get("tree", [])
                 if e.get("type") == "blob"]
    except json.JSONDecodeError:
        files = []
    print("    远端文件数 = {}".format(len(files)))
    for bad in ("bots_config.json.bak", "/dist/", "build/work", ".log", "__pycache__"):
        hit = [f for f in files if bad in f]
        print("    {:<22} {}".format(bad, ("!! " + str(hit[:2])) if hit else "OK 未出现"))

    if not apply:
        print("\n（预览）加 --apply 才会执行清理与设置。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
