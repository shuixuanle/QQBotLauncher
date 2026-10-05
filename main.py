# -*- coding: utf-8 -*-
"""QQBot 启动管理器 - 程序入口。

职责
----
1. 初始化 QApplication（含 QSettings 使用的组织名 / 应用名、高分屏、字体）。
2. 读取 bots_config.json（缺失时自动生成默认配置）。
3. 创建 MainWindow 并显示。
4. 安装全局异常钩子，把未捕获异常弹窗提示，而不是静默崩溃。

运行
----
    python main.py                       # 正常启动
    python main.py --config D:\\x.json    # 指定配置文件
    python main.py --theme dark          # 临时用深色界面（不写入偏好）
    python main.py --theme light --palette   # 强制浅色，并改用自己的调色板
    python main.py --doctor              # 体检：导入所有模块、检查未定义名字，然后退出
    python main.py --nav-debug           # 开启左侧列表时序诊断（写 nav_debug.log）
    python main.py --theme-debug         # 打印外观诊断（调色板亮度 / hint / 是否自建调色板）
    python main.py --elevate             # 以管理员身份重新启动自己（弹一次 UAC）
    python main.py --gui                 # 脱离启动：cmd 立即返回，只留管理器界面（推荐）
    python main.py --console             # 要一个属于自己的控制台窗口（看日志用）
    python main.py --selftest            # 检查依赖与配置，输出摘要后退出
    python main.py --bare                # 同上（别名，便于脚本调用）

管理员（提权）说明
------------------
有些被管理的程序需要管理员权限，例如消防栓 bot 的 HttpListener 要绑定 http 前缀，
普通权限会直接抛 `HttpListenerException (5): 拒绝访问`。

  · 管理器**不是**管理员时：启动这类程序会先弹一次 UAC（脚本内部用
    `Start-Process -Verb RunAs` 提权）。每次启动都弹。
  · 管理器**是**管理员时（`--elevate`，或右键"以管理员身份运行"）：
    子进程直接继承管理员令牌，**一次 UAC 都不用弹**。

Windows 不允许非管理员进程静默提权，所以"完全不弹窗"只能靠"起点就是管理员"。

控制台说明（推荐用 --gui）
-------------------------
在 cmd 里敲 `python main.py` 时，`python.exe` 会把那个 cmd 当成自己的控制台 ——
**运行期间不能关闭**，一关就把管理器一起带走（控制台关闭会通知附加的进程）。

    python main.py --gui      # 推荐：脱离启动，屏幕上**只剩管理器界面**（零控制台）
    python main.py            # 留在前台：cmd 被占用，能看到全部输出（排障用）
    python main.py --console  # 要一个属于自己的控制台窗口（会多出一个窗口）

`--gui` 的实现：用 `pythonw.exe`（GUI 子系统，不创建控制台）配合
`DETACHED_PROCESS` 重新启动自己，父进程立即退出 —— 于是 cmd 回到提示符
（可直接关掉），屏幕上只有 Qt 界面。子进程带 `QQBOT_GUI_CHILD` 环境变量，
不会再自己重启。

`--console` 的实现：先 `FreeConsole()` 断开，再用 `CREATE_NEW_CONSOLE` 启动
子进程，父进程退出。若两者都失败，退回 `AllocConsole()` 就地换控制台。

环境变量
--------
    QQBOT_CONFIG    指定配置文件路径（命令行 --config 优先）
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence

# ---------------------------------------------------------------------------
# 路径准备：保证从任意工作目录启动都能 import app.*
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtCore import Qt, QSettings, QTimer  # noqa: E402
from PyQt6.QtGui import QFont, QFontInfo, QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT as CONFIG_ROOT,
    BotConfig,
)
from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.main_window import (  # noqa: E402
    ADMIN_FLAG_ATTR,
    APP_NAME,
    ORG_NAME,
    MainWindow,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 默认窗口标题
WINDOW_TITLE = APP_NAME

#: 可选图标路径（存在则使用）
ICON_CANDIDATES = (
    PROJECT_ROOT / "assets" / "app.ico",
    PROJECT_ROOT / "app.ico",
    PROJECT_ROOT / "icon.ico",
)

#: 异常提示框最多显示的字符数
MAX_ERROR_TEXT = 4000


# ---------------------------------------------------------------------------
# 命令行参数
# ---------------------------------------------------------------------------

class StartupArgs(NamedTuple):
    """解析出来的启动参数。"""

    config_path: Optional[Path]
    self_test: bool
    #: 命令行指定的主题模式（"" 表示用 QSettings 里的偏好）
    theme: str
    #: 是否强制改用自己的调色板（配合 --theme）
    force_palette: bool
    #: 是否把命令行主题写进偏好（默认只影响本次运行）
    remember_theme: bool
    #: 只做"导入 + 名字体检"然后退出（不开窗口）
    doctor: bool = False
    #: 打开左侧列表的时序诊断日志（写 nav_debug.log）
    nav_debug: bool = False
    #: 打印外观诊断信息（写 theme_debug.log）
    theme_debug: bool = False
    #: 以管理员身份重新启动自己（弹一次 UAC）
    elevate: bool = False
    #: 已由 --elevate 拉起的子进程（防止无限套娃）
    no_elevate: bool = False
    #: 申请一个**属于自己的**控制台窗口（`--console`，想看日志时用）
    console: bool = False
    #: 与 --console 一起用：**不另开窗口**，日志就留在这个 cmd 里（诊断用）
    console_here: bool = False
    #: 脱离启动：用 pythonw 重新启动自己，屏幕上只剩管理器界面（`--gui`）
    gui: bool = False


def parse_args(argv: Sequence[str]) -> StartupArgs:
    """解析命令行参数。

    支持：
        --config <path> / -c <path> / --config=<path>
        --theme <system|light|dark> / --theme=<mode>
        --palette            强制改用自己的调色板（与 --theme 一起用）
        --remember-theme     把 --theme 写进偏好（下次启动继续用）
        --doctor             逐个导入所有模块并检查名字，随后退出
        --nav-debug          记录左侧列表的时序诊断日志（nav_debug.log）
        --theme-debug        打印外观诊断（含调色板亮度，写 theme_debug.log）
        --elevate            以管理员身份重新启动自己（弹一次 UAC；内部用 --no-elevate 防套娃）
        --gui                脱离启动（pythonw，零控制台）：cmd 立即返回，只留管理器界面
        --console            申请一个独立控制台窗口（想看到日志输出时用）
    --console-here       同上，但不另开窗口：日志留在这个 cmd 里（诊断用）
        --selftest / --bare / -t
        --help / -h
    """
    config_path: Optional[Path] = None
    self_test = False
    theme = ""
    force_palette = False
    remember_theme = False
    doctor = False
    nav_debug = False
    theme_debug = False
    elevate = False
    no_elevate = False
    console = False
    console_here = False
    gui = False

    index = 0
    args = list(argv)
    while index < len(args):
        item = args[index]
        if item in ("--config", "-c"):
            if index + 1 >= len(args):
                raise SystemExit("--config 需要跟一个文件路径")
            config_path = Path(args[index + 1]).expanduser()
            index += 2
            continue
        if item.startswith("--config="):
            config_path = Path(item.split("=", 1)[1]).expanduser()
            index += 1
            continue
        if item in ("--theme", "--appearance"):
            if index + 1 >= len(args):
                raise SystemExit("--theme 需要跟一个值：system / light / dark")
            theme = theme_tokens.normalize_mode(args[index + 1])
            index += 2
            continue
        if item.startswith("--theme=") or item.startswith("--appearance="):
            theme = theme_tokens.normalize_mode(item.split("=", 1)[1])
            index += 1
            continue
        if item == "--palette":
            force_palette = True
            index += 1
            continue
        if item in ("--remember-theme", "--save-theme"):
            remember_theme = True
            index += 1
            continue
        if item in ("--doctor", "--check-names", "--imports"):
            doctor = True
            index += 1
            continue
        if item in ("--nav-debug", "--debug-nav"):
            nav_debug = True
            index += 1
            continue
        if item in ("--theme-debug", "--debug-theme"):
            theme_debug = True
            index += 1
            continue
        if item in ("--elevate", "--admin", "--runas"):
            elevate = True
            index += 1
            continue
        if item == "--no-elevate":
            # 由 --elevate 拉起的子进程会带这个参数：不要再尝试提权
            no_elevate = True
            index += 1
            continue
        if item in ("--gui", "--detach"):
            # 脱离启动：用 pythonw 重新跑一次，屏幕上只剩界面（推荐）
            gui = True
            index += 1
            continue
        if item == "--console-here":
            # 诊断专用：不要另开控制台，输出就留在这个 cmd 里
            console = True
            console_here = True
        elif item == "--console":
            # 要一个属于自己的控制台窗口（想看日志时用，会多一个窗口）
            console = True
            index += 1
            continue
        if item in ("--selftest", "--bare", "-t"):
            self_test = True
            index += 1
            continue
        if item in ("--help", "-h"):
            print(__doc__)
            raise SystemExit(0)
        index += 1

    return StartupArgs(
        config_path, self_test, theme, force_palette, remember_theme, doctor,
        nav_debug, theme_debug, elevate, no_elevate, console, console_here, gui,
    )


def resolve_config_path(explicit: Optional[Path]) -> Path:
    """决定使用哪个配置文件：命令行 > 环境变量 > 默认路径。"""
    if explicit is not None:
        try:
            if explicit.is_dir():
                return explicit / "bots_config.json"
        except OSError:
            pass
        return explicit
    env_value = os.environ.get("QQBOT_CONFIG", "").strip()
    if env_value:
        candidate = Path(env_value).expanduser()
        try:
            if candidate.is_dir():
                return candidate / "bots_config.json"
        except OSError:
            pass
        return candidate
    from app.config import DEFAULT_CONFIG_PATH

    return Path(DEFAULT_CONFIG_PATH)


def stored_config_path() -> Optional[Path]:
    """读取上次使用过的配置文件路径（由 QSettings 保存）。"""
    value = QSettings(ORG_NAME, APP_NAME).value("config/path", "")
    text = str(value or "").strip()
    return Path(text) if text else None


def remember_config_path(path: Path) -> None:
    """记住本次使用的配置文件路径，下次启动默认沿用。"""
    settings = QSettings(ORG_NAME, APP_NAME)
    settings.setValue("config/path", str(path))
    settings.sync()


# ---------------------------------------------------------------------------
# 外观（主题）
# ---------------------------------------------------------------------------

def stored_theme_mode() -> str:
    """读取上次选择的外观（QSettings 的 ui/theme，默认跟随系统）。"""
    return theme_tokens.load_mode(QSettings(ORG_NAME, APP_NAME), theme_tokens.MODE_SYSTEM)


def setup_theme(app: QApplication, args: StartupArgs) -> str:
    """在创建主窗口**之前**决定并应用外观，避免首屏"亮一下再变暗"。

    顺序：命令行 --theme > QSettings 偏好 > 跟随系统。
    命令行给出的模式默认只影响本次运行（加 --remember-theme 才写入偏好）。

    这里顺便载入**自定义日志配色**（如果有的话）—— 它必须赶在第一次画界面之前
    进内存，否则日志区会先按内置配色亮一下再变。
    """
    theme_tokens.load_custom_colors(QSettings(ORG_NAME, APP_NAME))
    mode = args.theme or stored_theme_mode()
    applied = theme_tokens.apply_theme(app, mode, force_palette=args.force_palette)
    if args.theme and args.remember_theme:
        theme_tokens.save_mode(QSettings(ORG_NAME, APP_NAME), applied)
    return applied


def remember_theme_mode(mode: str) -> None:
    """把外观写进偏好。"""
    theme_tokens.save_mode(QSettings(ORG_NAME, APP_NAME), mode)


def is_running_as_admin() -> bool:
    """当前进程是否带管理员令牌（仅 Windows 有意义）。

    为什么需要：有些被管理的程序必须提权才能跑 —— 例如消防栓 bot 的
    HttpListener 要绑定 http 前缀，普通权限会抛
    `HttpListenerException (5): 拒绝访问`。

    如果管理器**本身**已经是管理员，启动这类子进程时就会**继承**管理员令牌，
    一次 UAC 都不用弹；否则每次启动都要弹一次。

    非 Windows 或检测失败时返回 False（只影响一条提示，不影响功能）。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (ImportError, AttributeError, OSError):
        return False


def _windowless_interpreter() -> str:
    """返回不带控制台的解释器路径（pythonw.exe），拿不到就退回当前解释器。

    为什么重要（真机反馈）：用 `python.exe` 提权重启时，`Start-Process` 会
    **新开一个控制台窗口**当"宿主"；那个窗口要一直等到子进程结束才关闭，
    于是"关掉管理器后还得手动关那个黑窗口"，而且关窗时感觉卡顿。
    `pythonw.exe` 是 GUI 子系统程序，**根本不创建控制台**，就没有这个宿主窗口。

    找不到 pythonw.exe（例如嵌入式 Python 发行版）时返回 `sys.executable`，
    行为退回旧样子但功能不受影响。
    """
    exe = sys.executable or ""
    if not exe:
        return exe
    # PyInstaller 打包后 sys.executable 就是那个 exe，本身没有控制台，
    # 也不存在"同目录的 pythonw.exe"，直接用自己。
    if getattr(sys, "frozen", False):
        return exe
    folder = os.path.dirname(exe)
    candidate = os.path.join(folder, "pythonw.exe")
    try:
        if os.path.exists(candidate):
            return candidate
    except OSError:
        pass
    return exe


#: 标记环境变量：由 --gui 拉起的子进程会带上它，避免无限重启
GUI_CHILD_ENV = "QQBOT_GUI_CHILD"

#: 最近一次"脱离启动"拉起的子进程 PID（0 = 没有）。
#:
#: 为什么留着它：脱离启动是**故意**让子进程独立活的（关掉启动它的 cmd 也不受影响），
#: 于是"谁来收拾它"就成了问题 —— 检查器（tools/check_alloc_console_live.py）需要
#: 在测完之后精确地把它关掉，而不是靠 `taskkill /IM python.exe` 这种连坐式清理
#: （真机踩过：那样只杀掉了 python.exe，--gui 拉起的 **pythonw.exe** 活了下来，
#:  于是屏幕上一个管理器窗口一直开着）。
LAST_RELAUNCH_PID = 0


def relaunch_detached() -> bool:
    """用 ``pythonw.exe`` 脱离启动自己 —— 屏幕上**只剩管理器界面**，没有控制台。

    为什么需要：在 cmd 里敲 ``python main.py`` 时，`python.exe` 会把那个 cmd
    当成自己的控制台。结果有两种都不理想的形态：
      · 直接跑：cmd 窗口在运行期间不能关（关掉会把管理器带走）；
      · 换控制台（AllocConsole / CREATE_NEW_CONSOLE）：管理器是自己了，但
        屏幕上**仍然多出一个控制台窗口**。

    想要"只有界面"，只有一条路：用 **`pythonw.exe`（GUI 子系统，不创建控制台）**
    启动。本函数负责这件事：

        python main.py --gui        ← 你在 cmd 里敲的
              │  用 pythonw.exe 启动子进程（DETACHED_PROCESS），自己立即退出
              ▼
        pythonw main.py             ← 子进程：没有控制台，只有 Qt 界面

    子进程带 ``QQBOT_GUI_CHILD`` 环境变量，所以不会再自己重启（防无限套娃）。

    返回 True 表示已经拉起了子进程，调用方应立即退出。
    """
    if os.name != "nt":
        return False
    if os.environ.get(GUI_CHILD_ENV):
        # 自己就是那个子进程：已经没有控制台了，正常启动即可
        return False

    here = os.path.dirname(os.path.abspath(__file__)) or None

    # 选解释器：优先同目录的 pythonw.exe（无控制台），其次 PATH
    interpreter = ""
    if not getattr(sys, "frozen", False):
        current = sys.executable or ""
        if current:
            candidate = os.path.join(os.path.dirname(current), "pythonw.exe")
            if os.path.exists(candidate):
                interpreter = candidate
    if not interpreter:
        interpreter = shutil.which("pythonw.exe") or shutil.which("pythonw") or ""

    if not interpreter:
        # 找不到 pythonw.exe：退回"换一个自己的控制台"，至少不再受原 cmd 影响
        return False

    child_env = dict(os.environ)
    child_env[GUI_CHILD_ENV] = "1"

    # 子进程不需要再处理 --gui / --console / --elevate：它已经是 GUI 形态，
    # 提权与否沿用父进程（管理员进程的子进程继承令牌）。
    extra = [
        a for a in sys.argv[1:]
        if a not in ("--gui", "--console", "--elevate", "--no-elevate")
    ]
    args = [interpreter, os.path.abspath(__file__)] + extra + ["--no-elevate"]

    # DETACHED_PROCESS：完全脱离父进程的控制台（配合 pythonw 就是"零控制台"）
    creationflags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_UNICODE_ENVIRONMENT", 0)
    )
    try:
        child = subprocess.Popen(
            args,
            cwd=here,
            creationflags=creationflags,
            env=child_env,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        global LAST_RELAUNCH_PID
        LAST_RELAUNCH_PID = int(child.pid or 0)
        return True
    except (OSError, ValueError):
        return False


def relaunch_with_own_console() -> bool:
    """用 ``CREATE_NEW_CONSOLE`` 重新启动自己，让管理器独占一个控制台窗口。

    仅在"确实想看到日志输出"时使用（``--console``）。用户明确表示日常只想要
    界面，所以主入口是 :func:`relaunch_detached`（pythonw，零控制台）。

    注意：用 ``CREATE_NEW_CONSOLE`` 时，若父进程仍附着在某个控制台上，**子进程会
    继承父进程的标准句柄**，输出会跑到父进程那个 cmd 里 —— 所以父进程必须先
    ``FreeConsole()`` 断开再启动子进程（实测：不作这一步，新窗口里没有输出）。
    """
    if os.name != "nt":
        return False
    if os.environ.get(GUI_CHILD_ENV):
        return False

    child_env = dict(os.environ)
    child_env[GUI_CHILD_ENV] = "1"

    python_exe = sys.executable or "python.exe"
    args = [python_exe, os.path.abspath(__file__)]
    extra = [
        a for a in sys.argv[1:]
        if a not in ("--gui", "--console", "--elevate", "--no-elevate")
    ]
    args += extra
    args.append("--no-elevate")

    try:
        kernel32 = ctypes.windll.kernel32
        if kernel32.GetConsoleWindow():
            # ⚠️ 必须**先打印再断开**：FreeConsole() 之后当前进程就没有控制台了，
            # 之后所有 print 都会消失（真机 2026-10-05：用户以为"--console 没有日志"，
            # 其实是提示和日志都跑到**新开的那个控制台窗口**里去了）。
            print("管理器将在一个**新的控制台窗口**里启动，日志与报错都在那边。",
                  flush=True)
            print("（想看日志请切到那个新窗口；崩溃信息也会写进 launcher_error.log）",
                  flush=True)
            # 先断开，子进程才会真正拿到"全新的"控制台
            kernel32.FreeConsole()
        creationflags = (
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            | getattr(subprocess, "CREATE_UNICODE_ENVIRONMENT", 0)
        )
        child = subprocess.Popen(
            args,
            cwd=os.path.dirname(os.path.abspath(__file__)) or None,
            creationflags=creationflags,
            env=child_env,
            close_fds=True,
        )
        global LAST_RELAUNCH_PID
        LAST_RELAUNCH_PID = int(child.pid or 0)
        return True
    except (OSError, ValueError, AttributeError):
        return False


def alloc_console() -> bool:
    """给当前进程**就地**换一个属于自己的控制台窗口（最后的兜底路径）。

    与另外两个的区别：
      · :func:`relaunch_detached`（主路径，`--gui`）：pythonw 脱离启动 → **零控制台**；
      · :func:`relaunch_with_own_console`（`--console`）：重启一个带新控制台的进程；
      · 本函数：不重启，``FreeConsole() + AllocConsole()`` 换控制台。
        局限是**外层 cmd 窗口仍然存在**，所以只在前两者都失败时用。

    ``GetConsoleWindow`` / ``FreeConsole`` / ``AllocConsole`` 都在 **kernel32**
    （不在 user32）；写错 DLL 会抛 AttributeError 被 except 吞掉，表现为
    "什么都没发生"（真实踩过）。
    """
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.windll.kernel32

        had_console = bool(kernel32.GetConsoleWindow())
        if had_console:
            kernel32.FreeConsole()

        if not kernel32.AllocConsole():
            return False

        try:
            std_output = kernel32.CreateFileW(
                "CONOUT$", 0x40000000, 0x00000002, None, 3, 0, None
            )
            std_input = kernel32.CreateFileW(
                "CONIN$", 0x80000000, 0x00000002, None, 3, 0, None
            )
            if std_output and std_output != -1:
                kernel32.SetStdHandle(-11, std_output)
            if std_input and std_input != -1:
                kernel32.SetStdHandle(-10, std_input)
        except (AttributeError, OSError, ValueError):
            pass

        try:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        except OSError:
            pass

        try:
            kernel32.SetConsoleTitleW("QQBot启动管理器 - 控制台")
        except (AttributeError, OSError):
            pass

        print("已为管理器申请独立控制台（这个窗口属于它自己）。")
        return True
    except (OSError, ValueError, AttributeError):
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重新启动自己（会弹一次 UAC），返回是否成功发起。

    只在命令行加了 `--elevate` 时调用。用 PowerShell 的 Start-Process
    （比 ShellExecute 更好控制参数与工作目录），并显式给子进程加
    `--no-elevate`，避免它再次尝试提权而无限套娃。

    优先用 `pythonw.exe` 启动，避免多出一个"宿主控制台窗口"（见上）。
    只有当前进程带 `--keep-console` 时才保留控制台，方便看输出。

    Windows 不允许非管理员进程静默提权，所以"完全不弹窗"只有一条路：
    让起点就是管理员（本函数 / 右键"以管理员身份运行" / 管理员快捷方式）。
    """
    if os.name != "nt":
        return False
    keep_console = "--keep-console" in sys.argv
    python_exe = sys.executable if keep_console else _windowless_interpreter()
    extra = [
        arg for arg in sys.argv[1:]
        if arg not in ("--elevate", "--no-elevate", "--keep-console")
    ]
    if getattr(sys, "frozen", False):
        # 打包版：目标就是自己这个 exe（管理员版还内嵌了 requireAdministrator
        # 清单，ShellExecute 的 runas 与它配合，双击/自提权都只会弹一次 UAC）。
        pending = ["--no-elevate"] + extra
    else:
        script = os.path.abspath(__file__)
        pending = [script, "--no-elevate"] + extra
    arg_list = ",".join("'{}'".format(arg.replace("'", "''")) for arg in pending)
    command = (
        "Start-Process -FilePath '{python}' -ArgumentList {args} "
        "-WorkingDirectory '{cwd}' -Verb RunAs"
    ).format(
        python=python_exe.replace("'", "''"),
        args=arg_list,
        cwd=os.path.abspath(os.path.dirname(script)).replace("'", "''"),
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            timeout=60,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def log_theme_debug(app: QApplication, stage: str) -> None:
    """把当前外观的"实际呈现"写进日志（N2：排查"切了没反应"用）。

    记录的关键是 **Window 角色亮度** —— 它才是"界面到底深还是浅"的唯一可信判据；
    QStyleHints.colorScheme() 只代表意图，真机上会撒谎。
    """
    try:
        info = theme_tokens.theme_debug_info()
    except Exception:  # noqa: BLE001 - 诊断代码绝不打扰主流程
        return
    line = (
        "[{stage}] mode={mode} 系统={os} 实际={resolved} hint={hint} scheme={scheme} "
        "Window亮度={lum} 调色板偏深={dark} 自建调色板={own}".format(
            stage=stage,
            mode=info.get("mode"),
            os=info.get("os"),
            resolved=info.get("resolved"),
            hint=info.get("hint"),
            scheme=info.get("scheme"),
            lum=info.get("window_lightness"),
            dark=info.get("palette_dark"),
            own=info.get("overridden"),
        )
    )
    print(line)
    try:
        log_path = PROJECT_ROOT / "theme_debug.log"
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 体检：逐个导入模块 + 名字检查
# ---------------------------------------------------------------------------

#: 体检要导入的项目模块（顺序：被依赖的在前）
DOCTOR_MODULES = (
    "app.config",
    "app.layout",
    "app.process_manager",
    "app.ui.theme",
    "app.ui.program_widget",
    "app.ui.bot_tab",
    "app.ui.edit_bot_dialog",
    "app.ui.bot_list_dialog",
    "app.ui.main_window",
)


def run_doctor() -> int:
    """逐个导入项目模块，报告失败原因。

    为什么需要它：``py_compile`` 只查语法，**不查名字**。
    例如类体里写了 ``pyqtSignal(str)`` 却忘了 import，编译期毫无问题，
    直到 Python 执行到那一行才 NameError（而且是在启动瞬间直接崩）。
    把每个模块都 import 一遍，就能在开窗口之前把这类错误全抓出来。
    """
    import importlib
    import traceback

    print("项目目录：{}".format(PROJECT_ROOT))
    print("Python：{}".format(sys.version.split()[0]))
    print("逐个导入 {} 个项目模块：\n".format(len(DOCTOR_MODULES)))

    failed: List[str] = []
    for name in DOCTOR_MODULES:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - 体检就是要抓住一切异常
            failed.append(name)
            print("  [失败] {}".format(name))
            print("         {}: {}".format(type(exc).__name__, exc))
            for line in traceback.format_exc().splitlines()[-6:]:
                print("         " + line)
            continue
        path = getattr(module, "__file__", "?")
        print("  [通过] {:<26} {}".format(name, path))

    print("")
    if failed:
        print("体检失败：{} 个模块导入出错 -> {}".format(len(failed), ", ".join(failed)))
        return 1

    # 入口自身也验证一遍（含 QApplication 相关导入）
    try:
        importlib.import_module("main")
    except Exception as exc:  # noqa: BLE001
        print("体检失败：main 无法导入 -> {}: {}".format(type(exc).__name__, exc))
        return 1
    print("体检通过：{} 个模块全部可导入，未发现未定义名字 / 导入错误。".format(
        len(DOCTOR_MODULES)
    ))
    print("（若仍启动失败，用 python main.py 看具体报错，或查看 launcher_error.log）")
    return 0


# ---------------------------------------------------------------------------
# 异常处理
# ---------------------------------------------------------------------------

def install_exception_hook(app: QApplication) -> None:
    """把未捕获异常记录到日志并弹窗提示。"""

    def _hook(exc_type, exc_value, exc_tb) -> None:  # pragma: no cover - 异常路径
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        # 控制台输出（如果是从终端启动）
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except Exception:
            pass
        # 写入日志文件，便于事后排查
        try:
            log_path = PROJECT_ROOT / "launcher_error.log"
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write("\n" + "=" * 60 + "\n")
                handle.write(text)
        except Exception:
            log_path = None

        try:
            detail = text if len(text) <= MAX_ERROR_TEXT else text[-MAX_ERROR_TEXT:]
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("{} 发生错误".format(WINDOW_TITLE))
            box.setText("程序遇到未处理的错误：{}".format(exc_value))
            box.setInformativeText(
                "详细信息已写入 {}".format(log_path) if log_path else "详细信息见控制台输出"
            )
            box.setDetailedText(detail)
            box.exec()
        except Exception:
            pass

    sys.excepthook = _hook


# ---------------------------------------------------------------------------
# 应用初始化
# ---------------------------------------------------------------------------

def configure_qt_attributes() -> None:
    """必须在创建 QApplication 之前完成的 Qt 全局设置。

    注意：setHighDpiScaleFactorRoundingPolicy() 只能在 QGuiApplication 创建之前调用，
    否则 Qt 会打印 "must be called before creating the QGuiApplication instance"
    并且设置不生效。因此这里把它单独拆出来，在 QApplication 之前调用。
    """
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, TypeError):
        # 该 API 缺失时忽略：Qt6 默认已启用高分屏缩放
        pass


def configure_application(app: QApplication) -> None:
    """设置 QSettings 标识与默认字体（在 QApplication 创建之后调用）。"""
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(True)

    # 默认字体：优先微软雅黑，回退系统默认
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
        try:
            probe = QFont(family)
            available = QFontInfo(probe).family().lower() == family.lower()
        except (RuntimeError, TypeError):
            available = False
        if available or family.startswith("Microsoft"):
            font = QFont(app.font())
            font.setFamily(family)
            app.setFont(font)
            break

    # 图标（可选）
    for candidate in ICON_CANDIDATES:
        try:
            if candidate.is_file():
                app.setWindowIcon(QIcon(str(candidate)))
                break
        except (OSError, RuntimeError):
            continue


def load_config(config_path: Path) -> BotConfig:
    """读取配置（缺失时自动生成默认配置），并输出加载提示。"""
    config = BotConfig.load(config_path, create_if_missing=True)
    for message in config.load_warnings:
        print("[配置] {}".format(message))
    return config


def describe_environment(config: BotConfig, theme_mode: str = "") -> str:
    """生成自检摘要文本。"""
    lines: List[str] = []
    lines.append("Python：{}".format(sys.version.split()[0]))
    lines.append("PyQt6：已加载")
    lines.append("项目目录：{}".format(CONFIG_ROOT))
    lines.append("配置文件：{}".format(config.path))
    lines.append("配置存在：{}".format(Path(config.path).exists()))
    if theme_mode:
        lines.append(
            "外观：{}（偏好 ui/theme = {}，窗口亮度 {}）".format(
                theme_tokens.mode_label(theme_mode),
                stored_theme_mode(),
                theme_tokens.palette_window_lightness(),
            )
        )
    lines.append("机器人数量：{} / 程序数量：{}".format(len(config.bots), config.total_programs))
    for bot in config.bots:
        lines.append(
            "  - {}{}：{} 个程序".format(bot.name, "" if bot.enabled else "（已禁用）", len(bot.programs))
        )
        for program in bot.programs:
            argv = program.command_argv(config.base_dir)
            lines.append(
                "      * {} | role={} | delay={:.1f}s | auto_latest_jar={} | argv={}".format(
                    program.name,
                    program.role,
                    program.delay,
                    program.auto_latest_jar,
                    argv if argv else "（未配置命令）",
                )
            )
    problems = config.validate()
    if problems:
        lines.append("配置提示：")
        lines.extend("  ! {}".format(item) for item in problems)
    else:
        lines.append("配置校验：通过")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _install_crash_logging() -> Optional[str]:
    """装崩溃日志：Python 异常、以及**致命信号**（段错误/abort）都记到文件里。

    为什么要它（真机 2026-10-05）
    -----------------------------
    用户报告"拖动左栏就崩，但 `python main.py --console` 什么也不打印" ——
    这种情况通常不是普通的 Python 异常（那会打到 stderr），而是：
      · PyQt 在**槽里**遇到异常时直接 abort（有时只留下一行，容易被吞）；
      · 或者 Qt/C++ 层的致命错误（访问越界、重复释放），进程直接死掉。
    `faulthandler` 能在**致命信号**发生时把当时的 Python 调用栈写进文件，
    `sys.excepthook` 负责普通未捕获异常。两者都写 `launcher_error.log`
    （已在 .gitignore 里），下次崩了就有据可查。

    返回日志文件路径（装不上就返回 None，绝不影响启动）。
    """
    path = Path(__file__).resolve().parent / "launcher_error.log"
    try:
        handle = open(path, "ab", buffering=0)
    except OSError:
        return None

    try:
        import faulthandler

        faulthandler.enable(file=handle, all_threads=True)
    except (ImportError, AttributeError, ValueError, RuntimeError):
        pass

    def _hook(exc_type, exc_value, exc_tb):
        try:
            handle.write("\n=== 未捕获异常 {} ===\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S")).encode("utf-8"))
            traceback.print_exception(exc_type, exc_value, exc_tb, file=handle)
            handle.flush()
        except (OSError, ValueError):
            pass
        # 同时按老样子打到 stderr（--console 时能直接看到）
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

    def _thread_hook(args):
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    try:
        import threading

        threading.excepthook = _thread_hook
    except (ImportError, AttributeError):
        pass
    return str(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """程序主入口：返回进程退出码。"""
    args = list(sys.argv if argv is None else argv)
    parsed = parse_args(args[1:])
    explicit_config = parsed.config_path
    self_test = parsed.self_test

    # 崩溃日志要**尽早**装上（越早，越能覆盖启动期的问题）
    crash_log = _install_crash_logging()
    if crash_log and parsed.console:
        print("崩溃日志：{}".format(crash_log))

    # --doctor：只做"导入 + 名字体检"，连 QApplication 都不需要
    if parsed.doctor:
        return run_doctor()

    # --elevate：以管理员身份重新启动自己（弹一次 UAC），本进程随即退出。
    # 已经是管理员、或本进程由 --elevate 拉起（带 --no-elevate）时不再重复提权。
    if parsed.elevate and not parsed.no_elevate and not is_running_as_admin():
        if os.name != "nt":
            print("--elevate 只在 Windows 上有效，已忽略。")
        elif relaunch_as_admin():
            print("已以管理员身份重新启动（请在新窗口里继续操作）。")
            return 0
        else:
            print("提权失败或被取消（UAC 未确认）；继续以当前权限启动。")

    # --gui：脱离启动（pythonw，零控制台）→ 屏幕上只剩管理器界面。
    # 子进程带 QQBOT_GUI_CHILD 标记，不会无限重启。
    if parsed.gui:
        if relaunch_detached():
            print("已脱离启动管理器（无控制台）：这个窗口可以关掉了，"
                  "稍等片刻会出现管理器界面。")
            return 0
        print("脱离启动失败（找不到 pythonw.exe？），改为就地启动。")

    # --console：想看到日志输出时用 —— 让管理器独占一个控制台窗口，
    # 原来那个 cmd 随即退出（子进程带 QQBOT_GUI_CHILD 标记，不会无限重启）。
    if parsed.console and parsed.console_here:
        # 诊断用：输出就留在**当前这个 cmd**（不另开窗口、也不换控制台），
        # traceback / faulthandler 的文字因此都看得见
        print("已按 --console-here 启动：日志与崩溃信息都会留在当前这个窗口。")
    elif parsed.console:
        if relaunch_with_own_console():
            print("已用独立控制台重新启动管理器；这个窗口（原 cmd）可以关掉了。")
            return 0
        # 已经在独立控制台里（或重启失败）：就地换一个属于自己的控制台作兜底
        alloc_console()

    # 必须在 QApplication 之前设置（否则 Qt 会打印告警且设置不生效）
    configure_qt_attributes()

    app = QApplication.instance()
    if app is None:
        app = QApplication(args)
    configure_application(app)
    install_exception_hook(app)
    # 把权限状态放到 QApplication 上，供 MainWindow 显示（避免循环 import）
    try:
        setattr(app, ADMIN_FLAG_ATTR, is_running_as_admin())
    except (AttributeError, TypeError):
        pass

    # 外观要在主窗口之前定下来：否则深色偏好下会先画一屏浅色再变色
    theme_mode = setup_theme(app, parsed)
    if parsed.theme_debug:
        # N2 诊断：打印"实际呈现"（调色板亮度才是可信判据）
        log_theme_debug(app, "启动-应用外观后")

    # 配置文件优先级：命令行 --config > 环境变量 QQBOT_CONFIG > 上次使用过的 > 项目默认
    env_backed = explicit_config is not None or bool(os.environ.get("QQBOT_CONFIG", "").strip())
    config_path = resolve_config_path(explicit_config)
    if not env_backed:
        remembered = stored_config_path()
        if remembered is not None:
            try:
                if remembered.exists() or remembered.parent.is_dir():
                    config_path = remembered
            except OSError:
                pass

    try:
        config = load_config(config_path)
    except Exception as exc:  # 配置层出问题时给出可读提示
        QMessageBox.critical(
            None,
            WINDOW_TITLE,
            "读取配置失败：\n{}\n\n配置文件：{}".format(exc, config_path),
        )
        return 2

    # 让窗口使用同一个配置文件，并记住路径供下次启动沿用
    try:
        config.path = Path(config_path)
        remember_config_path(config.path)
    except (OSError, ValueError):
        pass

    if self_test:
        print(describe_environment(config, theme_mode))
        problems = config.validate()
        if problems:
            print("自检完成：环境正常，但配置存在 {} 处提示（见上）。".format(len(problems)))
            return 1
        print("自检完成：环境与配置均可正常使用。")
        return 0

    window = MainWindow(config)
    window.setWindowTitle(WINDOW_TITLE)
    # 让窗口的动作勾选状态与启动时实际应用的外观一致
    try:
        window.sync_theme_actions()
    except (AttributeError, RuntimeError):
        pass
    if parsed.theme_debug:
        log_theme_debug(app, "主窗口构建后")

    # --nav-debug：记录左侧列表的时序（排查"高亮被重建抹掉"这类竞态）
    if parsed.nav_debug:
        try:
            window.set_nav_debug(True)
            window.statusBar().showMessage(
                "导航诊断已开启：时序写入 {}".format(PROJECT_ROOT / "nav_debug.log"), 8000
            )
        except (AttributeError, RuntimeError):
            pass
    window.show()

    # 提示配置文件路径与恢复状态（延迟到事件循环启动之后）
    QTimer.singleShot(
        200,
        lambda: window.statusBar().showMessage(
            "配置文件：{}　|　外观：{}（窗口亮度 {}）　|　启动后会按「运行参数」中的间隔依次拉起机器人".format(
                config.path,
                theme_tokens.mode_label(theme_mode),
                theme_tokens.palette_window_lightness(app),
            ),
            8000,
        ),
    )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
