# -*- coding: utf-8 -*-
"""静态检查：scripts\\start_hydrant.bat 与消防栓的启动命令是否仍然正确。

拦两类真实事故（都发生过）：
  1. `.bat` 里出现非 ASCII —— cmd 按系统 ANSI 解析，全角括号会把 `if (...)` 块
     提前闭合，后面所有行都被当命令执行，退出码乱掉；
  2. 启动命令里出现引号（尤其 `\\"` 或整串带双引号）—— argv 原样交给 QProcess，
     Qt 把引号转义成 `\\"`，PowerShell 又当转义符，于是命令被当字符串打印，
     退出码 0 但程序没启动。

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


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    print("[1] scripts\\start_hydrant.bat")
    check("文件存在", BAT.exists(), str(BAT))
    if not BAT.exists():
        print("\n结果：失败项 = {}".format(failures))
        return 1

    raw = BAT.read_bytes()
    non_ascii = [(i + 1, b) for i, b in enumerate(raw) if b > 127]
    check("纯 ASCII（不含多字节字符）", not non_ascii,
          "第 {} 字节起".format(non_ascii[0][0]) if non_ascii else "")
    check("没有 BOM", raw[:3] != b"\xef\xbb\xbf")

    text = raw.decode("ascii", errors="replace")
    check("没有 pause（会占住控制台）",
          not any(line.strip().lower() == "pause" for line in text.splitlines()))
    check("有提权判定（net session）", "net session" in text)
    check("提权用 -Wait（父进程需存活到子进程结束）",
          "-Verb RunAs -Wait" in text or ("-Verb RunAs" in text and "-Wait" in text))
    check("提权后会 cd 到脚本目录（否则 %~f0 解析不到）",
          'cd /d "%~dp0"' in text)
    check("脚本自带 cd /d 到编译产物目录", "cd /d \"%BOT_DIR%\"" in text)
    check("退出码会原样返回（exit /b %RC%）", "exit /b %RC%" in text)

    print("\n[2] bots_config.json 里消防栓的启动命令")
    if not CONFIG.exists():
        check("配置文件存在", False, str(CONFIG))
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
                check("{}：命令里不含单引号".format(where), "'" not in command, command)
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

    print("\n[3] 中文说明是否另存为 md（保证 bat 能保持纯 ASCII）")
    readme = ROOT / "scripts" / "README-start_hydrant.md"
    check("scripts\\README-start_hydrant.md 存在", readme.exists())

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
