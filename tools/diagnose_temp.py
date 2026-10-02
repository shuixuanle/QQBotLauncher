# -*- coding: utf-8 -*-
"""诊断 PyInstaller 单文件 exe 报 "Could not create temporary directory!" 的原因。

单文件 exe 启动时会把自己解包到 ``%TEMP%\\_MEIxxxxxx``；若 ``%TEMP%`` 不存在、
不可写、路径含特殊字符、或被杀软拦截，就会弹这个错误。

用法：
    python tools\\diagnose_temp.py
"""

import os
import tempfile
import uuid
from pathlib import Path
import sys


def try_dir(label: str, path) -> bool:
    print("  {:<24} {}".format(label, path or "<未设置>"))
    if not path:
        return False
    target = Path(path)
    exists = target.exists()
    print("      存在        = {}".format(exists))
    if not exists:
        try:
            target.mkdir(parents=True, exist_ok=True)
            print("      已创建      = True")
            exists = True
        except OSError as exc:
            print("      !! 创建失败 : {}".format(exc))
            return False

    try:
        probe = target / ("_wtest_" + uuid.uuid4().hex[:8])
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError as exc:
        writable = False
        print("      !! 不可写   : {}".format(exc))
    print("      可写        = {}".format(writable))

    non_ascii = [ch for ch in str(target) if ord(ch) > 127]
    print("      含非 ASCII  = {}{}".format(
        bool(non_ascii),
        "（{}）".format("".join(non_ascii[:10])) if non_ascii else "",
    ))
    print("      路径长度    = {} {}".format(
        len(str(target)), "（偏长，>120 易出问题）" if len(str(target)) > 120 else ""
    ))
    return writable

# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    print("=== 环境变量 ===")
    for name in ("TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA"):
        print("  {:<14} = {}".format(name, os.environ.get(name, "<未设置>")))

    print()
    print("=== 逐个测试可写的临时目录 ===")
    ok_temp = try_dir("TEMP", os.environ.get("TEMP"))
    ok_tmp = try_dir("TMP", os.environ.get("TMP"))

    print()
    print("=== Python 眼中的临时目录 ===")
    print("  tempfile.gettempdir() = {}".format(tempfile.gettempdir()))
    print("  该目录可写            = {}".format(os.access(tempfile.gettempdir(), os.W_OK)))

    print()
    print("=== exe 目录（dist\\）与磁盘剩余空间 ===")
    dist = Path(__file__).resolve().parent.parent / "dist"
    print("  dist 存在 = {}".format(dist.exists()))
    try:
        usage = __import__("shutil").disk_usage(str(dist if dist.exists() else Path.cwd()))
        print("  剩余空间  = {:.1f} GB（解包单个 exe 约需 150-250 MB）".format(
            usage.free / (1024 ** 3)))
    except OSError as exc:
        print("  查询失败：{}".format(exc))

    print()
    print("=== 结论 ===")
    if ok_temp and ok_tmp:
        print("  TEMP/TMP 都正常 —— exe 报错更可能来自：")
        print("    · 杀软 / 勒索软件防护拦截了 exe 在 TEMP 下创建目录")
        print("      安全中心 → 病毒和威胁防护 → 勒索软件防护 → 添加排除目录")
        print("    · 或磁盘空间不足")
    else:
        print("  !! TEMP/TMP 有问题 —— 这正是 \"Could not create temporary directory!\" 的原因。")
        print("     修复办法（任选其一）：")
        print("       1. 把系统临时目录设回默认值：")
        print("          系统属性 → 高级 → 环境变量 → 用户变量")
        print("          TEMP = %USERPROFILE%\\AppData\\Local\\Temp")
        print("          TMP  = %USERPROFILE%\\AppData\\Local\\Temp")
        print("       2. 或只给这个 exe 指定可用目录（不改系统设置）：")
        print("          set TEMP=D:\\Temp && set TMP=D:\\Temp && start \"\" \"dist\\QQBot启动管理器.exe\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
