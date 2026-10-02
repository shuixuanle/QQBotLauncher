# -*- coding: utf-8 -*-
"""找一找「消防栓 NewHydrant」的编译产物目录在哪，并可一键写进 scripts\\hydrant_dir.txt。

为什么需要
----------
`scripts\\start_hydrant.bat` 会按"环境变量 → 向上搜索 → 手填"的顺序找 bot 目录。
真机上遇到过：bot 仓库根本不在管理器旁边（例如在 D:\\ 或别的项目目录），
向上搜索自然找不到，脚本只能报"找不到"。与其让你翻目录，不如让程序去找。

它找什么
--------
按优先级：
  1. `Bleatingsheep.NewHydrant.Bot.dll`（标准产物名）所在目录；
  2. 任何 `*NewHydrant*.dll`；
  3. 任何 `<某个 *.Bot 项目>\\bin\\Debug|Release\\net*` 目录。
搜索范围：本仓库及其上级、你的用户目录、以及各个盘的浅层目录
（跳过 AppData / Windows / node_modules / .git / $Recycle.Bin 等）。

用法：
    python tools\\find_hydrant.py            # 只找并打印
    python tools\\find_hydrant.py --write    # 找到唯一一个就写进 scripts\\hydrant_dir.txt
    python tools\\find_hydrant.py --root D:\\BOTBENTI   # 只在指定目录里找（可重复）
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_NAME = "Bleatingsheep.NewHydrant.Bot.dll"
OUT_FILE = ROOT / "scripts" / "hydrant_dir.txt"

#: 搜索时跳过的目录名（小写比较）
SKIP_DIRS = {
    "appdata", "windows", "winsxs", "$recycle.bin", "system volume information",
    "node_modules", ".git", ".vs", ".idea", ".vscode", "__pycache__",
    "packages", "obj", ".nuget", "temp", "tmp", "程序文件", "program files",
    "program files (x86)", "programdata",
}

#: 用户目录里的搜索深度上限（越深越慢）
USER_DEPTH = 6
#: 盘符根目录的搜索深度上限
DRIVE_DEPTH = 3
#: 总时间预算（秒），超了就停下来报告已找到的
TIME_BUDGET = 90.0

started = time.time()


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把工具自己弄崩。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def out_of_time() -> bool:
    return time.time() - started > TIME_BUDGET


def walk(base: Path, max_depth: int, hits: list) -> None:
    """广度优先找目标 dll；命中就记进 hits，并顺手记下 net* 目录。"""
    base = base.resolve()
    if not base.is_dir():
        return
    queue = [(base, 0)]
    while queue:
        folder, depth = queue.pop(0)
        if out_of_time():
            return
        try:
            entries = list(folder.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    name = entry.name.lower()
                    if name in SKIP_DIRS or name.startswith("."):
                        continue
                    # <某项目>\bin\Debug|Release\net* → 直接当成候选
                    if entry.name.startswith("net") and folder.name.lower() in ("debug", "release"):
                        hits.append(("net 产物目录", folder))
                    elif depth < max_depth:
                        queue.append((entry, depth + 1))
                elif entry.name.lower() == TARGET_NAME.lower():
                    hits.append(("目标 dll", folder))
                elif "newhydrant" in entry.name.lower() and entry.suffix.lower() == ".dll":
                    hits.append(("疑似 dll", folder))
            except (PermissionError, OSError):
                continue


def candidate_roots(extra) -> list:
    roots = []
    roots.extend(extra)
    # 本仓库及上级（脚本默认就搜这里）
    here = ROOT
    for _ in range(3):
        roots.append(here)
        here = here.parent
    # 用户目录（VS 默认的 source\\repos 也在里面）
    home = Path.home()
    roots.append(home)
    # 各盘根目录（浅层）
    for letter in "CDEFGH":
        drive = Path("{}:/".format(letter))
        if drive.exists():
            roots.append(drive)
    # 去重 + 去掉互相包含的重复项
    seen, unique = set(), []
    for path in roots:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def main() -> int:
    argv = sys.argv[1:]
    extra_roots = []
    if "--root" in argv:
        index = argv.index("--root")
        extra_roots = [Path(item) for item in argv[index + 1:]]
        argv = argv[:index]
    write = "--write" in argv

    log("找 {} …".format(TARGET_NAME))
    log("（最多 {} 秒；想更快就加 --root <你放 bot 的目录>）".format(int(TIME_BUDGET)))
    hits = []
    for base in candidate_roots(extra_roots):
        if out_of_time():
            log("  时间到了，停止搜索。")
            break
        log("  搜索 {}".format(base))
        walk(base, USER_DEPTH if base == Path.home() else DRIVE_DEPTH, hits)
        if any(kind == "目标 dll" for kind, _folder in hits):
            break

    # 去重（同一个目录可能被多次命中）
    unique, seen = [], set()
    for kind, folder in hits:
        key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append((kind, folder))
    # 目标 dll 优先
    unique.sort(key=lambda item: (item[0] != "目标 dll", len(str(item[1]))))

    log("")
    if not unique:
        log("没有找到。请确认：")
        log("  1) 那个 .NET 项目是不是已经编译过（需要 bin\\Debug\\net*\\*.dll）；")
        log("  2) 或者直接告诉我它的完整路径，我帮你写进 scripts\\hydrant_dir.txt；")
        log("  3) 也可以换个范围再找：python tools\\find_hydrant.py --root D:\\BOTBENTI")
        return 1

    log("找到 {} 个候选：".format(len(unique)))
    for kind, folder in unique[:12]:
        log("  [{}] {}".format(kind, folder))
    best = unique[0][1]

    log("")
    log("最像的是：{}".format(best))
    if write:
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text(str(best) + "\n", encoding="utf-8")
        log("已写进 {} —— 现在直接启动消防栓即可。".format(
            OUT_FILE.relative_to(ROOT)))
    else:
        log("要让启动脚本用它，执行：python tools\\find_hydrant.py --write")
        log("（或手工把这一行路径粘进 scripts\\hydrant_dir.txt）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
