# -*- coding: utf-8 -*-
"""把 .vbs 启动脚本重写为 UTF-16 LE + BOM，并对四个启动脚本做编码体检。

为什么必须这样（真机事故）：
    Windows Script Host 读取没有 BOM 的 .vbs 时按**系统 ANSI 代码页**
    （中文 Windows 是 GBK/936）解码。文件若是 UTF-8，中文字符的字节序列
    会被截断，某个多字节字符的尾部字节把后面的引号"吃掉"，
    于是弹出：

        Windows Script Host
        错误: 未结束的字符串常量
        代码: 800A0409

    UTF-16 LE + BOM（FF FE）是 WSH 明确支持的格式，与代码页无关。
    次选是 GBK（无 BOM），但换到非中文系统又可能出问题。

用法：
    python tools\\fix_vbs_encoding.py            # 只检查
    python tools\\fix_vbs_encoding.py --apply    # 转换并写回
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [
    ROOT / "启动（普通模式）.vbs",
    ROOT / "启动（管理员模式）.vbs",
]

BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF8 = b"\xef\xbb\xbf"


def decode_any(raw: bytes):
    """按 BOM 或试探顺序解码，返回 (文本, 原编码名)。"""
    if raw.startswith(BOM_UTF16_LE):
        return raw[2:].decode("utf-16-le"), "utf-16-le (BOM)"
    if raw.startswith(BOM_UTF8):
        return raw[3:].decode("utf-8"), "utf-8 (BOM)"
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "unknown"


def main() -> int:
    apply = "--apply" in sys.argv
    failures = 0

    for path in TARGETS:
        if not path.exists():
            print("跳过（不存在）：{}".format(path.name))
            continue
        raw = path.read_bytes()
        text, encoding = decode_any(raw)
        lines = text.splitlines()

        print("=" * 70)
        print("文件：{}（{} 字节）".format(path.name, len(raw)))
        print("  原编码        : {}".format(encoding))
        print("  UTF-16 LE BOM : {}".format("有" if raw.startswith(BOM_UTF16_LE) else "无"))

        # 体检：非 ASCII 出现在字符串/注释里时，编码必须是 WSH 认得的
        has_non_ascii = any(ord(ch) > 127 for ch in text)
        ok_encoding = raw.startswith(BOM_UTF16_LE) or encoding.startswith("gbk")
        if has_non_ascii:
            print("  含非 ASCII    : 是（中文注释/提示）")
            if not ok_encoding:
                print("  !! 危险：非 ASCII + {} —— WSH 会按 ANSI 解码，"
                      "报「未结束的字符串常量」".format(encoding))
                failures += 1
        else:
            print("  含非 ASCII    : 否")

        # 体检：引号配对（每行的双引号必须是偶数，忽略注释行）
        unbalanced = []
        for number, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("'"):
                continue
            if line.count('"') % 2 != 0:
                unbalanced.append(number)
        if unbalanced:
            print("  !! 引号数为奇数的行：{}".format(unbalanced[:8]))
            failures += 1
        else:
            print("  引号配对      : OK")

        if raw.startswith(BOM_UTF16_LE):
            print("  结论          : 已是 UTF-16 LE + BOM，无需转换")
            continue

        print("  结论          : 需要转换为 UTF-16 LE + BOM")
        if apply:
            path.write_bytes(BOM_UTF16_LE + text.encode("utf-16-le"))
            print("  已写回        : {} 字节".format(path.stat().st_size))
        else:
            print("  （预览）加 --apply 才会写入")

    # 顺带体检 .bat（必须是纯 ASCII）
    print("=" * 70)
    for name in ("启动（普通模式）.bat", "启动（管理员模式）.bat"):
        path = ROOT / name
        if not path.exists():
            continue
        raw = path.read_bytes()
        bad = [i for i, b in enumerate(raw) if b > 127]
        status = "OK（纯 ASCII）" if not bad else "!! 第 {} 字节起有非 ASCII".format(bad[0])
        if bad:
            failures += 1
        print("{:<26} {}".format(name, status))

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
