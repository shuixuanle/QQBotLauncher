# -*- coding: utf-8 -*-
"""检查并（可选）清除启动脚本的"来源区域"标记，解决双击时的安全警报。

背景
----
Windows 的"打开文件 - 安全警报"（Open File - Security Warning）由
**附在文件上的 Zone.Identifier 数据流**触发 —— 只要文件被标记为来自
"Internet / 不受信任区域"，双击运行（.vbs / .bat / .exe / .msi 都一样）
就会先弹一次确认框。**这与扩展名无关**：换个 exe 名字并不会消失。

反过来，如果文件**没有**这个标记，却仍弹窗，那就不是 Zone 的问题，常见原因：
  · 组策略 / 安全软件启用了"脚本运行前确认"（很多安全套件默认拦截 VBS）；
  · 文件被标记为只读/被占用外的其它属性；
  · 受控文件夹访问（Controlled Folder Access）保护了该目录。

本工具做三件事：
  1. 列出启动脚本是否存在 Zone.Identifier，并打印其内容；
  2. （--clean）删除该数据流 —— 等价于右键属性里的"解除锁定"；
  3. 检查目录是否受"受控文件夹访问"或 Defender 排除项影响（尽力而为）。

用法：
    python tools\\check_zone_identifier.py            # 只检查
    python tools\\check_zone_identifier.py --clean     # 清除标记
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [
    "启动（普通模式）.vbs",
    "启动（管理员模式）.vbs",
    "启动（普通模式）.bat",
    "启动（管理员模式）.bat",
    "main.py",
]


def read_zone(path: Path):
    """返回 Zone.Identifier 的内容（没有则 None）。"""
    stream = str(path) + ":Zone.Identifier"
    try:
        with open(stream, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "读取受限：{}".format(exc).encode("utf-8")


def main() -> int:
    clean = "--clean" in sys.argv
    flagged = []
    failures = 0

    print("目录：{}".format(ROOT))
    print("=" * 70)
    print("[1] Zone.Identifier（来自网络的标记）")
    for name in TARGETS:
        path = ROOT / name
        if not path.exists():
            print("  {:<24} 不存在，跳过".format(name))
            continue
        data = read_zone(path)
        if data is None:
            print("  {:<24} 无标记".format(name))
            continue
        flagged.append(path)
        text = data.decode("utf-8", errors="replace").strip().replace("\r\n", " | ")
        print("  {:<24} !! 有标记（{} 字节）：{}".format(name, len(data), text))

    if flagged:
        print()
        print("  说明：有这个标记时，双击运行会先弹「打开文件 - 安全警报」，")
        print("        点「运行」才能继续 —— 这正是要消除的东西。")
        if clean:
            print()
            print("[2] 清除标记（等价于右键属性里的「解除锁定」）")
            for path in flagged:
                stream = str(path) + ":Zone.Identifier"
                try:
                    os.remove(stream)
                    print("  已清除：{}".format(path.name))
                except OSError as exc:
                    print("  !! 清除失败：{} —— {}".format(path.name, exc))
                    failures += 1
        else:
            print("  加 --clean 可以清除（无需管理员权限）。")
    else:
        print()
        print("  所有目标文件都没有标记 —— 那么双击时若仍弹窗，就不是 Zone 的问题。")
        print("  常见的其它原因（按可能性排序）：")
        print("    1. 安全软件 / 组策略对 .vbs 脚本本身做拦截确认（很多套件默认如此）")
        print("       → 在安全软件里把本目录加入信任区，或改用 .bat / .exe 启动")
        print("    2. 受控文件夹访问（Controlled Folder Access）保护了桌面目录")
        print("       → Windows 安全中心 → 病毒和威胁防护 → 勒索软件防护 → 添加排除目录")
        print("    3. 只用 SmartScreen 的信誉提示（首次运行未签名程序）")
        print("       → 点「更多信息 → 仍要运行」一次即可，之后不再提示")

    print()
    print("[3] Defender 排除项（尽力查询，可能无权限）")
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-MpPreference).ExclusionPath -join \"`n\""],
            capture_output=True, text=True, timeout=20, check=False,
        )
        out = (completed.stdout or "").strip()
        if out:
            print("  当前排除目录：")
            for line in out.splitlines():
                line = line.strip()
                if line:
                    mark = " <== 本项目" if str(ROOT).lower() in line.lower() else ""
                    print("    {}{}".format(line, mark))
        else:
            print("  查询不到（可能没有权限，或未安装 Defender）")
    except (OSError, subprocess.SubprocessError):
        print("  查询失败（跳过）")

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
