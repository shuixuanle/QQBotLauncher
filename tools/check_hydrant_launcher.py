# -*- coding: utf-8 -*-
"""静态检查：本机的 scripts\\start_hydrant.bat（提权启动脚本）是否写歪了。

这个脚本**不进仓库**（它带着作者本机的路径），所以：
  · 本机有  -> 逐条检查（下面这些坑都是真踩过的）；
  · 本机没有（别人 clone 下来）-> 直接跳过，不算失败。

拦过的四类真实事故：
  1. `.bat` 里出现非 ASCII —— cmd 按系统 ANSI 解析，全角括号会把 `if (...)` 块
     提前闭合，后面所有行都被当命令执行，退出码乱掉；
  2. 启动命令里出现引号（尤其 `\\"` 或整串带双引号）—— argv 原样交给 QProcess，
     Qt 把引号转义成 `\\"`，PowerShell 又当转义符，于是命令被当字符串打印，
     退出码 0 但程序没启动；
  3. **在 `( … )` 块里用 `%CD%`**（2026-10-02）—— cmd 在**解析整块时**就展开它，
     `pushd` 等于白做：`pushd ..\\..\\ && set "BOT_DIR=%CD%\\..."` 会把 BOT_DIR
     算成"脚本自己所在目录\\..."。真机表现是日志一切正常，然后
     `Cannot enter folder: ...\\scripts\\Bleatingsheep...\\net10.0` + 退出码 3。
     现在断言：不许用 pushd、不许在块里读 `%CD%`，路径只能从 `%~dp0` 派生；
  4. `.bat` 是**纯 LF** 换行 —— cmd 对 LF-only 的批处理解析不稳（`goto`/标签/
     `for` 块都可能不按预期走）。必须是 CRLF。

用法：
    python tools\\check_hydrant_launcher.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "scripts" / "start_hydrant.bat"
CONFIG = ROOT / "bots_config.json"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def cd_in_block(text: str) -> str:
    """找出"在 (...) 块里用 %CD%"的写法。返回第一处上下文，没有则空串。

    只看真正的命令行，`rem` 注释不算（脚本里那段"别这么写"的说明本身就会提到
    %CD% 和 pushd，那是教学用的）。
    """
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("rem"):
            continue
        if depth > 0 and "%CD%" in line:
            return stripped[:60]
        depth += line.count("(") - line.count(")")
        if depth < 0:
            depth = 0
    return ""


def pushd_outside_comment(text: str) -> str:
    """找出注释之外真正执行的 pushd（返回那一行，没有则空串）。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("rem"):
            continue
        head = stripped.split()[0].lower() if stripped.split() else ""
        if head == "pushd":
            return stripped[:60]
    return ""


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    print("[0] 本机有没有这个脚本")
    if not BAT.exists():
        print("  [SKIP] scripts\\start_hydrant.bat 不在本机")
        print("         它不进仓库（带着作者本机的路径）；仓库里只有 scripts\\README.md")
        print("         讲清这个目录干什么用，并给出提权脚本的模板与踩坑清单。")
        print("         你要是也写了同名脚本，放进 scripts\\ 就会被本检查器照常检查。")
        print("\n结果： 全部通过（本机没有该脚本，跳过）")
        return 0
    check("文件存在", True)

    print("\n[1] scripts\\start_hydrant.bat 本身")
    raw = BAT.read_bytes()
    non_ascii = [(i + 1, b) for i, b in enumerate(raw) if b > 127]
    check("纯 ASCII（不含多字节字符）", not non_ascii,
          "第 {} 字节起".format(non_ascii[0][0]) if non_ascii else "")
    check("没有 BOM", raw[:3] != b"\xef\xbb\xbf")
    lone_lf = raw.count(b"\n") - raw.count(b"\r\n")
    check("换行是 CRLF（纯 LF 会让 goto/标签/for 块行为异常）", lone_lf == 0,
          "有 {} 处纯 LF".format(lone_lf))

    text = raw.decode("ascii", errors="replace")
    check("没有 pause（会占住控制台）",
          not any(line.strip().lower() == "pause" for line in text.splitlines()))
    check("有提权判定（net session）", "net session" in text)
    check("提权用 -Wait（父进程需存活到子进程结束）",
          "-Verb RunAs -Wait" in text or ("-Verb RunAs" in text and "-Wait" in text))
    check("提权后会 cd 到脚本目录（否则 %~f0 解析不到）", 'cd /d "%~dp0"' in text)
    check("脚本自带 cd 到编译产物目录", 'cd /d "%BOT_DIR%"' in text)
    check("退出码会原样返回（exit /b %RC%）", "exit /b %RC%" in text)

    print("\n[1b] 回归：不许在 (...) 块里用 %CD%（真机事故：路径被算成脚本目录）")
    anchor = cd_in_block(text)
    pushd = pushd_outside_comment(text)
    check("没有真正执行的 pushd（配 %CD% 用必错）", not pushd, "找到：{}".format(pushd))
    check("没有在括号块里读 %CD%", not anchor, "找到：{}".format(anchor))
    check("BOT_DIR 从 %~dp0 派生（与当前目录无关）",
          "%~dp0.." in text or "QQBOT_HYDRANT_DIR" in text)
    check("找不到时有明确提示（告诉用户几种修法）",
          "QQBOT_HYDRANT_DIR" in text and "[ERROR]" in text)
    print("\n[1c] 启动方式：先 dotnet <dll>（.NET 项目常常没有 .exe），再 *.exe")
    dll_at = text.find('set "DLL="')
    exe_at = text.find('set "APP="')
    check("有 dotnet <dll> 这条路径", 'dotnet "%DLL%"' in text)
    check("dll 的判定写在 exe 之前（顺序反了就会去跑不存在的 exe）",
          0 <= dll_at < exe_at, "dll@{}, exe@{}".format(dll_at, exe_at))
    check("dotnet 不在 PATH 上有明确报错", "where dotnet" in text)
    check("支持 hydrant_dir.txt 第二行当自定义命令", "skip=1" in text and "run_custom" in text)

    print("\n[2] bots_config.json 里消防栓的启动命令")
    if not CONFIG.exists():
        print("  [SKIP] bots_config.json 不在本机（那是使用者自己的配置，不进仓库）")
    else:
        payload = json.loads(CONFIG.read_text(encoding="utf-8"))
        found = False
        for bot in payload.get("bots", []):
            for program in bot.get("programs", []):
                command = str(program.get("command", ""))
                if "start_hydrant" not in command:
                    continue
                found = True
                where = "{} / {}".format(bot.get("name"), program.get("name"))
                check("{}：命令里不含双引号".format(where), '"' not in command, command)
                check("{}：命令里不含单引号".format(where), "'" not in command)
                check("{}：不含反斜杠转义 \\\"".format(where), '\\"' not in command)
                check("{}：就是 cmd.exe /c 调用脚本".format(where),
                      command.strip().lower() == "cmd.exe /c start_hydrant.bat", command)
                cwd = Path(str(program.get("cwd", "")))
                check("{}：工作目录指向 scripts（命令里才不用写路径）".format(where),
                      cwd.name.lower() == "scripts", str(cwd))
                check("{}：via_shell 不需要开（命令里已含 cmd /c）".format(where),
                      not program.get("via_shell", False))
        if not found:
            check("配置里能找到引用 start_hydrant 的程序", False, "没找到")

    print("\n[3] scripts\\README.md 讲清了目录用途（这个才进仓库）")
    readme = ROOT / "scripts" / "README.md"
    check("scripts\\README.md 存在", readme.exists())
    if readme.exists():
        body = readme.read_text(encoding="utf-8")
        for keyword in ("net session", "-Verb RunAs", "CRLF", "cmd.exe /c",
                        "hydrant_dir.txt"):
            check("  README 里讲了 {}".format(keyword), keyword in body)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
