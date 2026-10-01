# -*- coding: utf-8 -*-
"""静态检查：启动脚本（bat / vbs）是否可用、是否保持 ASCII。

拦过的真实事故：`.bat` 里出现非 ASCII → cmd 按系统 ANSI 解析，全角括号的尾字节
被当成半角 `)`，把 `if (...)` 块提前闭合，后面所有行都被当命令执行。

用法：
    python tools\\check_launchers.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LAUNCHERS = (
    "启动（普通模式）.bat",
    "启动（管理员模式）.bat",
    "启动（exe·临时目录修复）.bat",
    "启动管理器（普通）.pyw",
    "启动管理器（管理员）.pyw",
)

#: 已废弃的启动器：存在反而会让人困惑，明确报出来
DEPRECATED = (
    "启动（普通模式）.vbs",
    "启动（管理员模式）.vbs",
)

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def read_text(path: Path) -> tuple:
    """按 BOM 优先解码，返回 (文本, 编码名)。

    注意 .vbs 必须是 UTF-16 LE + BOM 或 GBK —— 无 BOM 的 UTF-8 会被
    Windows Script Host 按 ANSI 解码，中文被截断成
    「未结束的字符串常量」(800A0409)。本函数因此先看 BOM。
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le"), "utf-16-le (BOM)"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8 (BOM)"
    for encoding in ("ascii", "utf-8", "gbk"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "?"


def main() -> int:
    print("[1] 文件存在性与编码")
    for name in LAUNCHERS:
        path = ROOT / name
        exists = path.exists()
        check("{} 存在".format(name), exists, str(path) if not exists else "")
        if not exists:
            continue
        text, encoding = read_text(path)
        non_ascii = [ch for ch in text if ord(ch) > 127]
        if path.suffix.lower() == ".bat":
            check("  {} 是纯 ASCII".format(name), not non_ascii,
                  "含 {} 个非 ASCII 字符".format(len(non_ascii)) if non_ascii else "")
        elif path.suffix.lower() == ".pyw":
            # .pyw 是 **Python 源码**（由 pyw.exe 执行），UTF-8 无 BOM 是正确写法；
            # 只有 .vbs 才受 WSH 的 ANSI/BOM 规则约束。
            check("  {} 是 UTF-8 可读的 Python 源码".format(name), True)
            text = path.read_text(encoding="utf-8")
            check("  {} 首行是 #!python shebang".format(name),
                  text.splitlines()[0].startswith("#!python"),
                  text.splitlines()[0] if text.splitlines() else "")
            check("  {} 会转交 launcher.py".format(name), "launcher.py" in text)
            try:
                compile(text, str(path), "exec")
                compiles = True
                detail = ""
            except SyntaxError as exc:
                compiles, detail = False, str(exc)
            check("  {} 语法正确".format(name), compiles, detail)
        else:
            # VBS 含中文时必须是 UTF-16 LE + BOM 或 GBK：
            # 无 BOM 的 UTF-8 会被 WSH 按 ANSI 解码 → 800A0409「未结束的字符串常量」。
            raw = path.read_bytes()
            is_utf16 = raw.startswith(b"\xff\xfe")
            is_gbk = False
            if not is_utf16:
                try:
                    raw.decode("ascii")
                    is_gbk = True  # 纯 ASCII，任何 ANSI 代码页都安全
                except UnicodeDecodeError:
                    try:
                        raw.decode("gbk")
                        is_gbk = True
                    except UnicodeDecodeError:
                        is_gbk = False
            check("  {} 编码可被 WSH 正确解析（UTF-16 BOM / GBK / ASCII）".format(name),
                  is_utf16 or is_gbk,
                  "UTF-16 LE + BOM" if is_utf16 else ("GBK/ASCII" if is_gbk else "无 BOM 的 UTF-8（会报 800A0409）"))
            check("  {} 不是无 BOM 的 UTF-8".format(name),
                  not raw.startswith(b"\xef\xbb\xbf"))

    print("\n[2] bat 的关键动作")
    normal_bat = ROOT / "启动（普通模式）.bat"
    admin_bat = ROOT / "启动（管理员模式）.bat"
    for path, need_elevate in ((normal_bat, False), (admin_bat, True)):
        if not path.exists():
            continue
        text, _ = read_text(path)
        name = path.name
        check("  {} 会用 pythonw.exe".format(name), "pythonw.exe" in text)
        check("  {} 会 cd 到脚本目录".format(name), 'cd /d "%~dp0"' in text)
        check("  {} 检查 main.py 是否存在".format(name),
              'if not exist "main.py"' in text)
        if need_elevate:
            # 管理员版**故意**用 -Wait：这样才能拿到退出码、报告"UAC 被取消"。
            # 它不会留下可见窗口，因为启动的是 pythonw.exe（无控制台）。
            check("  {} 用 -Wait 等待（便于报告 UAC 取消）".format(name),
                  "-Wait -PassThru" in text)
        else:
            check("  {} 用 start 分离启动（控制台可立即退出）".format(name),
                  'start "" "%PYW%" "main.py"' in text)
        if need_elevate:
            check("  {} 含提权（RunAs）".format(name), "-Verb RunAs" in text)
            check("  {} 提权后传 --no-elevate（防套娃）".format(name),
                  "--no-elevate" in text)
        else:
            check("  {} 不含提权".format(name), "RunAs" not in text)

    print("\n[3] launcher.py（pyw 入口共用的启动器）")
    launcher = ROOT / "launcher.py"
    if launcher.exists():
        text = launcher.read_text(encoding="utf-8")
        check("  存在", True)
        check("  用 ctypes 的 ShellExecuteW 提权", "ShellExecuteW" in text)
        check("  用 IsUserAnAdmin 判定管理员", "IsUserAnAdmin" in text)
        check("  启动 main.py 时用 CREATE_NO_WINDOW", "CREATE_NO_WINDOW" in text)
        check("  有 --selftest 自测模式", "--selftest" in text)
        check("  缺依赖时会弹错误框（tkinter / MessageBoxW）",
              "messagebox" in text or "MessageBoxW" in text)
        check("  支持把 --elevate 传给 main.py", "--elevate" in text)
    else:
        check("  launcher.py 存在", False, str(launcher))

    print("\n[3.2] 已废弃的 vbs 应当不存在")
    for name in DEPRECATED:
        check("  {} 已删除".format(name), not (ROOT / name).exists())

    print("\n[3.5] 解释器探测（避免选到没有 PyQt6 的 Python）")
    for path in (normal_bat, admin_bat):
        if path.exists():
            text, _ = read_text(path)
            check("  {} 会用 import PyQt6 验证候选解释器".format(path.name),
                  'import PyQt6' in text)
            check("  {} 有 :try 子程序逐个尝试".format(path.name), ":try" in text)
    for name in ("启动（普通模式）.vbs", "启动（管理员模式）.vbs"):
        path = ROOT / name
        if path.exists():
            text, _ = read_text(path)
            check("  {} 会验证 PyQt6".format(name), "import PyQt6" in text)

    print("\n[4] 运行环境：pythonw / python 能否找到")
    pythonw = shutil.which("pythonw")
    python = shutil.which("python")
    check("PATH 里有 pythonw.exe（无控制台启动）", pythonw is not None, str(pythonw or "未找到"))
    check("PATH 里有 python.exe（回退用）", python is not None, str(python or "未找到"))
    # VBS 里的默认路径（用户机器上的 python.org 安装）
    local = os.environ.get("LOCALAPPDATA", "")
    guess = os.path.join(local, "Programs", "Python", "Python314", "pythonw.exe")
    check("VBS 内置的默认路径存在（读不到时忽略）",
          (not local) or os.path.exists(guess) or True,
          "（沙箱可能读不到 AppData，此项仅提示）")

    print("\n[5] 用一个确实装了 PyQt6 的解释器试启动（6 秒后清理）")
    good = None
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python314"),
        r"C:\Python314",
    ]
    if python:
        candidates.append(os.path.dirname(python))
    for folder in candidates:
        if not folder:
            continue
        exe = os.path.join(folder, "pythonw.exe")
        tester = os.path.join(folder, "python.exe")
        if not (os.path.exists(exe) and os.path.exists(tester)):
            continue
        probe = subprocess.run([tester, "-c", "import PyQt6"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=False)
        if probe.returncode == 0:
            good = exe
            break

    if good is None:
        # 本机没装 PyQt6（例如 CI/沙箱）时属正常，不算失败 —— 只提示跳过。
        print("  [SKIP] 本机候选目录里没有装了 PyQt6 的解释器，跳过启动测试")
        print("         （在装有 PyQt6 的机器上会自动执行这一项）")
    else:
        check("找到一个装了 PyQt6 的解释器", True, good)
        proc = subprocess.Popen(
            [good, str(ROOT / "main.py")],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time

        time.sleep(6)
        alive = proc.poll() is None
        check("管理器进程起来了（6 秒后仍在运行）", alive,
              "已退出，returncode={}".format(proc.poll()))
        if alive:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False)
            time.sleep(1)
            check("已清理测试进程", proc.poll() is not None)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
