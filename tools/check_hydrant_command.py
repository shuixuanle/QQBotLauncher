# -*- coding: utf-8 -*-
"""验证「消防栓」新命令能被正确拆分（不需要 PyQt6）。

用法：
    python tools\\check_hydrant_command.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import format_command_line, split_command_line  # noqa: E402

#: 与 tools/fix_hydrant_command.py 里的 NEW_COMMAND 保持一致
NEW_COMMAND = (
    'powershell -NoProfile -Command "Start-Process powershell -Verb RunAs '
    "-ArgumentList '-NoExit','-Command','cmd.exe /c start_hydrant.bat'\""
)

#: 修复前的坏命令（含多余转义）—— 用来证明它能被检出
BAD_COMMAND = (
    'powershell -NoProfile -Command "Start-Process powershell -Verb RunAs '
    "-ArgumentList '-NoExit','-Command','cmd.exe /c \\\"\\\"C:\\x\\start_hydrant.bat\\\"\\\"'\""
)

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def main() -> int:
    print("[1] 新命令的 argv")
    argv = split_command_line(NEW_COMMAND)
    for index, item in enumerate(argv):
        print("    [{}] {}".format(index, item))

    check("拆成 4 段（exe + 3 个参数）", len(argv) == 4, str(len(argv)))
    check("第一段是 powershell", argv and argv[0] == "powershell")
    check("第二段是 -NoProfile", len(argv) > 1 and argv[1] == "-NoProfile")
    check("第三段是 -Command", len(argv) > 2 and argv[2] == "-Command")
    check("第四段是一条完整的引号串",
          len(argv) > 3 and argv[3].startswith('"') and argv[3].endswith('"'),
          argv[3] if len(argv) > 3 else "")
    check("**没有**反斜杠转义残留（这次的病根）",
          not any("\\" in item for item in argv),
          "；".join(item for item in argv if "\\" in item) or "干净")

    print("\n[2] 坏命令应能被识别出来（供编辑框提醒用）")
    bad_argv = split_command_line(BAD_COMMAND)
    check("坏命令里确实带反斜杠", any("\\" in item for item in bad_argv))
    check("回显时能看出多余转义",
          "\\" in format_command_line(bad_argv),
          format_command_line(bad_argv)[:70])

    print("\n[3] 命令里不含中文/空格路径（靠工作目录定位脚本）")
    check("新命令里没有 .bat 的完整路径", "start_hydrant.bat" in NEW_COMMAND
          and "C:\\" not in NEW_COMMAND and "D:\\" not in NEW_COMMAND)
    check("用的是相对文件名 cmd.exe /c start_hydrant.bat",
          "cmd.exe /c start_hydrant.bat" in NEW_COMMAND)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
