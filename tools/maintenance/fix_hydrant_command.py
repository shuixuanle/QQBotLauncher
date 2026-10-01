# -*- coding: utf-8 -*-
"""修好「消防栓」那条命令的引号（一次性维护脚本）。

背景：旧命令里含 `\\"` —— 那是从别处复制时多带的一层转义。
我们的解释器**不会**把 `\\"` 还原成 `"`（见 app/config.py 的 format_argv 文档：
"绝不用 \\"，因为 cmd 不认识反斜杠转义"），于是 PowerShell 看到字面反斜杠，
报 `UnexpectedToken`，进程立刻以退出码 1 结束。

本脚本把 command 改成只用**双引号 + 单引号**的正确写法，并保留：
  · 提权（Start-Process -Verb RunAs）
  · 已提权窗口用 -NoExit 保持可见

用法：
    python tools\\fix_hydrant_command.py            # 只预览
    python tools\\fix_hydrant_command.py --apply    # 真正写入
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "bots_config.json"


#: 正确的命令（2026-10 定稿 · 第二次修正）：
#:   cmd.exe /c start_hydrant.bat —— **一个引号都不需要**。
#:
#:   为什么不能用 powershell -NoProfile -Command "…"：
#:     · 我们的 argv 是原样交给 QProcess 的，Qt 会把参数里的引号转义成 \\" ；
#:     · 而 PowerShell 又把 \\" 当转义符，于是内层引号被吃掉，
#:       -Command 收到的是一整条带引号的字符串 → PowerShell **把它打印出来**，
#:       退出码 0 但程序没启动（真机事故）。
#:     · 提权与等待逻辑现在都写在 start_hydrant.bat 里（脚本内自提权），
#:       所以命令里既没有路径也没有引号，三层嵌套问题从根上消失。
NEW_COMMAND = "cmd.exe /c start_hydrant.bat"


def main() -> int:
    apply = "--apply" in sys.argv
    bat_path = ROOT / "scripts" / "start_hydrant.bat"
    if not bat_path.exists():
        print("找不到脚本：{}".format(bat_path))
        return 1

    scripts_dir = bat_path.parent
    print("目标脚本 =", bat_path)
    print("工作目录 =", scripts_dir)
    print("新命令   =", NEW_COMMAND)
    print()

    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    changed = 0
    for bot in payload.get("bots", []):
        for program in bot.get("programs", []):
            command = str(program.get("command", ""))
            if '\\"' not in command and "start_hydrant" not in command:
                continue
            print("命中：{} / {}".format(bot.get("name"), program.get("name")))
            print("  旧命令 =", command)
            print("  旧目录 =", program.get("cwd"))
            program["command"] = NEW_COMMAND
            program["cwd"] = str(scripts_dir)
            print("  新命令 =", NEW_COMMAND)
            print("  新目录 =", scripts_dir)
            changed += 1

    if not changed:
        print("没有需要修改的命令（可能已经修好）。")
        return 0

    if not apply:
        print("\n以上是预览。加 --apply 才会写入。")
        return 0

    backup = CONFIG.with_suffix(".json.bak")
    backup.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    CONFIG.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("\n已写入 {}（备份：{}）".format(CONFIG.name, backup.name))
    print("提示：脚本自带 cd /d 到编译产物目录，所以工作目录改成 scripts 不影响启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
