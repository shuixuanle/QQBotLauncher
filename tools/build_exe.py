# -*- coding: utf-8 -*-
"""一键打包：用 PyInstaller 生成两个"双击即用"的 exe。

产出（都在 dist\\ 下）：
    QQBot启动管理器.exe             普通模式（不提权，需要提权的程序会弹 UAC）
    QQBot启动管理器（管理员）.exe   管理员模式（内嵌 requireAdministrator 清单，
                                    双击弹一次 UAC，之后提权程序不再弹）

为什么要打包（相对于 .bat / .vbs 启动脚本）：
    · 不再依赖"哪个 Python 装没装 PyQt6"（解释器与依赖都打进 exe）
    · 没有控制台窗口，也没有 .vbs/.bat 的脚本执行确认框
    · 可以直接建快捷方式、放进 shell:startup 开机自启

用法：
    python tools\\build_exe.py              # 前置检查 + 打包
    python tools\\build_exe.py --check      # 只做前置检查
    python tools\\build_exe.py --one        # 只打包普通模式
    python tools\\build_exe.py --admin      # 只打包管理员模式
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"

SPEC_NORMAL = BUILD_DIR / "QQBotLauncher.spec"
SPEC_ADMIN = BUILD_DIR / "QQBotLauncherAdmin.spec"
MANIFEST_ADMIN = BUILD_DIR / "admin.manifest"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def info(label: str, value) -> None:
    print("  {:<28} {}".format(label, value))


def preflight() -> None:
    print("[1] 构建环境")
    info("Python", "{} ({})".format(sys.version.split()[0], sys.executable))
    info("项目目录", ROOT)

    check("main.py 存在", (ROOT / "main.py").exists())

    try:
        import PyQt6  # noqa: F401

        pyqt_ok = True
        pyqt_version = getattr(
            __import__("PyQt6.QtCore", fromlist=["QT_VERSION_STR"]), "QT_VERSION_STR", "?"
        )
    except ImportError as exc:
        pyqt_ok = False
        pyqt_version = str(exc)
    check("当前解释器有 PyQt6（打包需要）", pyqt_ok, pyqt_version)

    try:
        import PyInstaller

        pyi_ok = True
        pyi_version = PyInstaller.__version__
    except ImportError:
        pyi_ok = False
        pyi_version = "未安装：pip install pyinstaller"
    check("PyInstaller 可用", pyi_ok, pyi_version)

    check("普通模式 spec 存在", SPEC_NORMAL.exists(), str(SPEC_NORMAL))
    check("管理员模式 spec 存在", SPEC_ADMIN.exists(), str(SPEC_ADMIN))
    check("管理员清单存在", MANIFEST_ADMIN.exists(), str(MANIFEST_ADMIN))

    print("\n[2] 打包配置检查")
    if SPEC_NORMAL.exists():
        text = SPEC_NORMAL.read_text(encoding="utf-8")
        check("  普通模式：无控制台（console=False）", "console=False" in text)
        check("  普通模式：不请求提权", "requireAdministrator" not in text)
    if SPEC_ADMIN.exists():
        text = SPEC_ADMIN.read_text(encoding="utf-8")
        check("  管理员模式：无控制台（console=False）", "console=False" in text)
        check("  管理员模式：内嵌 admin.manifest", "admin.manifest" in text)
    if MANIFEST_ADMIN.exists():
        text = MANIFEST_ADMIN.read_text(encoding="utf-8")
        check("  清单里 requestedExecutionLevel = requireAdministrator",
              'level="requireAdministrator"' in text)


def run_pyinstaller(spec: Path) -> bool:
    """调用 PyInstaller。优先用 `python -m PyInstaller`，避免 PATH 上没有 pyinstaller.exe。"""
    cmd = [sys.executable, "-m", "PyInstaller", str(spec), "--noconfirm", "--clean",
           "--distpath", str(DIST_DIR), "--workpath", str(BUILD_DIR / "work")]
    print("\n$ {}".format(" ".join(cmd)))
    try:
        completed = subprocess.run(cmd, cwd=str(ROOT), check=False)
    except OSError as exc:
        print("  启动 PyInstaller 失败：{}".format(exc))
        return False
    return completed.returncode == 0


def main() -> int:
    args = set(sys.argv[1:])
    preflight()

    if failures:
        print("\n前置检查未通过，已停止。请先解决上面的 [FAIL]。")
        return 1
    if "--check" in args:
        print("\n前置检查全部通过（未打包，因为指定了 --check）。")
        return 0

    want_normal = "--admin" not in args
    want_admin = "--one" not in args

    print("\n[3] 开始打包（首次约 1-3 分钟）")
    results = {}
    if want_normal:
        print("\n---- 普通模式 ----")
        results["普通"] = run_pyinstaller(SPEC_NORMAL)
    if want_admin:
        print("\n---- 管理员模式 ----")
        results["管理员"] = run_pyinstaller(SPEC_ADMIN)

    print("\n[4] 产物")
    for label, ok in results.items():
        info("{}模式".format(label), "成功" if ok else "失败")
    if DIST_DIR.exists():
        for item in sorted(DIST_DIR.glob("*.exe")):
            size_mb = item.stat().st_size / (1024 * 1024)
            info("  " + item.name, "{:.1f} MB".format(size_mb))

    print("\n提示：")
    print("  · bot 配置文件 bots_config.json 会生成在 exe **旁边**（首次运行）。")
    print("  · 把 exe 复制到别处也能自洽运行；建议连同 scripts\\ 一起复制。")
    print("  · 管理员版双击会弹一次 UAC —— 这是内嵌清单要求的，属预期行为。")

    failed = [label for label, ok in results.items() if not ok]
    print("\n结果：", "全部通过" if not failed else "失败：{}".format(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
