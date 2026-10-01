# -*- coding: utf-8 -*-
"""把 scripts/start_hydrant.bat 转成系统 ANSI(GBK) 编码。

为什么必须转：`.bat` 文件若存成无 BOM 的 UTF-8，cmd.exe 会按**系统 ANSI**
（中文 Windows 是 GBK/936）读取，于是中文注释变成乱码；更糟的是括号类全角字符
的尾字节可能被当成半角 `)`，把 `if (...)` 块提前闭合 —— 真机表现为
"'(「是」）...' is not recognized as an internal or external command"。

本工具做两件事：
  1) 把文件从 UTF-8 转存为 GBK（并校验往返一致）；
  2) 顺手检查是否还有"编码脆弱点"（非 ASCII 出现在 echo / if 块里）。

用法：
    python tools\\fix_bat_encoding.py            # 只检查
    python tools\\fix_bat_encoding.py --apply    # 转换并写回
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [ROOT / "scripts" / "start_hydrant.bat"]


def main() -> int:
    apply = "--apply" in sys.argv
    failures = 0

    for path in TARGETS:
        if not path.exists():
            print("跳过（不存在）：{}".format(path))
            continue
        raw = path.read_bytes()
        print("=" * 70)
        print("文件：{}（{} 字节）".format(path.relative_to(ROOT), len(raw)))
        print("  BOM      : {}".format("有" if raw[:3] == b"\xef\xbb\xbf" else "无"))

        text = None
        for encoding in ("utf-8", "gbk"):
            try:
                text = raw.decode(encoding)
                print("  当前编码 : {}".format(encoding))
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            print("  !! 无法解码（既不是 UTF-8 也不是 GBK）")
            failures += 1
            continue

        # 转 GBK（cmd 默认代码页）
        try:
            gbk = text.encode("gbk")
        except UnicodeEncodeError as exc:
            print("  !! 有字符无法用 GBK 表示：{}".format(exc))
            failures += 1
            continue
        back = gbk.decode("gbk")
        if back != text:
            print("  !! GBK 往返后内容不一致")
            failures += 1
            continue
        print("  可无损转 GBK : 是")

        # 脆弱点检查：echo / if 块里的非 ASCII
        risky = []
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip().lower()
            if stripped.startswith("rem") or stripped.startswith("::"):
                continue
            if any(ord(char) > 127 for char in line):
                if stripped.startswith("echo") or stripped.startswith("if ") or ")":
                    risky.append((number, line.strip()[:60]))
        if risky:
            print("  编码脆弱行（非 ASCII 出现在 echo/if 里）：")
            for number, line in risky:
                print("     第 {} 行: {}".format(number, line))
        else:
            print("  编码脆弱行 : 无（非 ASCII 都在注释里）")

        if gbk == raw:
            print("  已是 GBK，无需转换")
        elif apply:
            path.write_bytes(gbk)
            print("  已写回 GBK：{}".format(path.relative_to(ROOT)))
        else:
            print("  （预览）将会写回 GBK，加 --apply 执行")

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
