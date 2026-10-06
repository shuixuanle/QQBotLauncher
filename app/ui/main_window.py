# -*- coding: utf-8 -*-
"""主窗口：QQBot 启动管理器的外壳。

组成
----
顶部工具栏：
    新建 Bot、编辑当前 Bot、启动/停止/重启当前 Bot、查看已有 bot、
    打开配置文件、启动全部、停止全部
中央（Phase C 起）：
    QSplitter = 左侧竖栏导航（查看已有 bot / 编辑或新建 bot 按钮 +
                              bot/程序两级状态树 + 打开/停止/关闭按钮）
              + 右侧实例区（QStackedWidget：空状态占位页 + 每个已打开机器人一页）
    BotTab 内部自行决定单日志窗口还是"上主下副"的多程序分屏
底部：
    状态栏（运行中的程序数 / 配置文件路径）
菜单：
    文件、机器人、视图（含窗口切换与侧栏折叠）、帮助

QSettings 持久化
----------------
    window/geometry        窗口大小与位置
    window/state           工具栏、状态栏等窗口状态
    window/current_bot     当前显示的机器人 id（Phase C 起）
    window/current_tab     旧版本的 Tab 序号（仅用于兼容读取）
    nav/expanded           左侧列表里展开的机器人 id 列表
    nav/split              左栏与实例区的宽度比例
    nav/collapsed          左栏是否折叠
    open/bots              上次打开的机器人 id 列表
    split/<bot_id>         每个机器人的 QSplitter 比例
    start/interval         机器人之间的启动间隔（秒）
    process/stop_timeout   停止超时（秒）
    log/max_lines          每个日志窗口保留的最大行数
"""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    QEventLoop,
    QItemSelectionModel,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QKeySequence,
    QPalette,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# 允许 "python app/ui/main_window.py" 直接运行自检
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import (  # noqa: E402
    PROJECT_ROOT,
    ROLE_PRIMARY,
    Bot,
    BotConfig,
    DEFAULT_CONFIG_PATH,
    Program,
)
from app import layout as layout_model  # noqa: E402
from app.process_manager import (  # noqa: E402
    DEFAULT_STOP_TIMEOUT_MS,
    ProcessManager,
    build_manager_key,
)
from app.ui.bot_tab import BotTab, collect_splitter_orientations  # noqa: E402
from app.ui.edit_bot_dialog import EditBotDialog  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ORG_NAME = "QQBotLauncher"
APP_NAME = "QQBot启动管理器"

SETTINGS_GEOMETRY = "window/geometry"
SETTINGS_WINDOW_STATE = "window/state"
#: 旧键：QTabWidget 时代的"当前 Tab 序号"（仅用于兼容迁移）
SETTINGS_CURRENT_TAB = "window/current_tab"
#: 当前选中的机器人 id（Phase C 起用）
SETTINGS_CURRENT_BOT = "window/current_bot"
#: 左侧导航栏展开的机器人 id 列表
SETTINGS_NAV_EXPANDED = "nav/expanded"
#: 左侧导航栏与实例区的宽度比例
SETTINGS_NAV_SPLIT = "nav/split"
#: 导航栏是否折叠
SETTINGS_NAV_COLLAPSED = "nav/collapsed"
#: 实例区顶部浏览器式标签栏：用户是否手动勾选显示（左栏折叠时无论如何都显示）
SETTINGS_BOT_TAB_BAR = "nav/bot_tab_bar"
SETTINGS_OPEN_BOTS = "open/bots"
SETTINGS_SPLIT_PREFIX = "split/"
SETTINGS_START_INTERVAL = "start/interval"
SETTINGS_STOP_TIMEOUT = "process/stop_timeout"
SETTINGS_LOG_LINES = "log/max_lines"
#: 关闭管理器时是否**强制**停止所有程序（跳过确认、不等优雅退出）
SETTINGS_FORCE_STOP_ON_CLOSE = "process/force_stop_on_close"

DEFAULT_START_INTERVAL = 1.0
DEFAULT_LOG_LINES = 5000

#: 左侧导航栏默认宽度 / 最小宽度 / 最大宽度（像素）
DEFAULT_NAV_WIDTH = 280
#: 窗口最小宽度下限（实际最小值还会跟着内容走，见 resizeEvent）
MIN_WINDOW_WIDTH = 460
MIN_NAV_WIDTH = 180
#: 上限：避免记忆里存进一个夸张的宽度（例如窗口曾最大化过）把实例区挤没
MAX_NAV_WIDTH = 640

def settings_bool(value: object, default: bool = False) -> bool:
    """把 QSettings 读出来的值安全地转成 bool。

    为什么需要它（真机踩过的坑）：
    Windows 注册表把 QSettings 写入的布尔存成 ``REG_SZ``，读回来是**字符串**
    ``'true'`` / ``'false'``；而 ``bool('false') is True``（非空字符串恒为真）。
    直接用 ``bool(...)`` 判断会导致"注册表明明写着 false，程序却当成 true"。
    """
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on", "y", "t"):
        return True
    if text in ("false", "0", "no", "off", "n", "f", ""):
        return False
    return bool(default)


#: main.py 会把「当前进程是否管理员」放到 QApplication 上的属性名。
#: 用属性传递是为了避免 main_window 反向 import main 造成循环导入。
ADMIN_FLAG_ATTR = "qqbot_is_admin"

#: 机器人节点的数据角色
ROLE_NAV_BOT_ID = Qt.ItemDataRole.UserRole
#: 程序节点的数据角色（存 manager key）
ROLE_NAV_PROGRAM_KEY = Qt.ItemDataRole.UserRole + 1
#: 该条目是否是"当前正在看的程序"（R1-2：焦点标记）
ROLE_NAV_FOCUSED = Qt.ItemDataRole.UserRole + 2
#: 该机器人是否已打开窗口（R1-2：机器人行左侧竖线）
ROLE_NAV_OPENED = Qt.ItemDataRole.UserRole + 3

# ---------------------------------------------------------------------------
# 左栏三态视觉（R1-2）
#
#   选中   —— QSS 的 Highlight 实心底 + HighlightedText 文字（系统色，深浅主题都好看）
#   焦点   —— "当前正在看的程序"：前缀 ▸ + 加粗（即使没被选中也能一眼看出）
#   已打开 —— 该机器人有窗口：机器人行前缀 ▌（窗口关掉即消失）
#
# 前缀固定占 2 个字符宽度（符号 + 空格），保证同级条目文字左对齐。
# 颜色字面量集中在这里，R2 抽主题令牌时只改这一处。
# ---------------------------------------------------------------------------

NAV_FOCUS_MARK = "▸"
NAV_OPEN_MARK = "▌"
#: 文本前缀使用的间隔（等宽，保证对齐）
NAV_PREFIX_GAP = " "


def nav_bot_prefix(opened: bool) -> str:
    """机器人行的前缀：窗口已打开显示 ▌，否则留等宽空白。"""
    return "{}{}".format(NAV_OPEN_MARK if opened else " ", NAV_PREFIX_GAP)


def nav_program_prefix(focused: bool) -> str:
    """程序行的前缀：当前正在看的程序显示 ▸，否则留等宽空白。"""
    return "{}{}".format(NAV_FOCUS_MARK if focused else " ", NAV_PREFIX_GAP)


# ---------------------------------------------------------------------------
# Phase A：Bot 状态公共层
#
# 说明：左侧导航栏（Phase C）、「查看已有 bot」对话框（Phase B）、Tab 标题、
#       状态栏都需要同一份"某个 bot 现在怎么样"的信息。这里把它集中成一个
#       BotStatus 数据类 + 几个格式化函数，避免各处各写一套导致颜色/文案不一致。
#       本层只读 ProcessManager 与 BotConfig，不缓存、不发信号、不改状态。
# ---------------------------------------------------------------------------


class BotStatusFlags:
    """状态取值（与 ProcessManager.STATE_* 保持一致，另加 disabled 与 partial）。"""

    DISABLED = "disabled"
    STOPPED = ProcessManager.STATE_STOPPED
    STARTING = ProcessManager.STATE_STARTING
    RUNNING = ProcessManager.STATE_RUNNING
    STOPPING = ProcessManager.STATE_STOPPING
    FAILED = ProcessManager.STATE_FAILED
    #: 一部分程序在跑，一部分没跑
    PARTIAL = "partial"
    #: ProcessManager 里表达"未运行"的两种写法，统一归到 STOPPED
    STOPPED_ALIASES = (ProcessManager.STATE_IDLE, ProcessManager.STATE_STOPPED)


@dataclass
class BotStatus:
    """一个机器人某一时刻的状态快照（纯数据，随时重新计算）。"""

    #: 机器人 id
    bot_id: str = ""
    #: 机器人名称
    name: str = ""
    #: 机器人是否启用
    enabled: bool = True
    #: 该 bot 配置里的程序总数
    total: int = 0
    #: 其中启用（enabled=True）的程序数
    enabled_total: int = 0
    #: 正在运行的程序数
    running: int = 0
    #: 处于启动中的程序数
    starting: int = 0
    #: 处于停止中的程序数
    stopping: int = 0
    #: 处于失败状态的程序数
    failed: int = 0
    #: 右侧是否已经打开该 bot 的窗口
    opened: bool = False
    #: 程序角色映射：manager key -> "primary" / "secondary"
    roles: Dict[str, str] = field(default_factory=dict)
    #: 程序名映射：manager key -> 程序名
    program_names: Dict[str, str] = field(default_factory=dict)
    #: 每个程序的原始状态：manager key -> STATE_*
    program_states: Dict[str, str] = field(default_factory=dict)

    # ---- 派生信息 ----

    @property
    def status(self) -> str:
        """归类后的状态。

        优先级（先命中先返回）：
            禁用 -> 停止中 -> 启动中 -> 异常 -> 全部运行 -> 部分运行 -> 未运行

        「停止中 / 启动中」优先于运行数，因为过渡状态下用户更关心"正在变化"；
        若有程序异常但仍在过渡，异常数量会在文案里单独列出，信息不丢。
        """
        if not self.enabled:
            return BotStatusFlags.DISABLED
        if self.stopping:
            return BotStatusFlags.STOPPING
        if self.starting:
            return BotStatusFlags.STARTING
        if self.failed:
            return BotStatusFlags.FAILED
        if self.running and self.enabled_total and self.running >= self.enabled_total:
            return BotStatusFlags.RUNNING
        if self.running:
            return BotStatusFlags.PARTIAL
        return BotStatusFlags.STOPPED

    @property
    def any_running(self) -> bool:
        """是否有任意程序在运行（含运行中 / 启动中 / 停止中）。"""
        return bool(self.running or self.starting or self.stopping)

    @property
    def all_running(self) -> bool:
        """所有"启用的程序"是否都在运行。"""
        return bool(self.enabled_total) and self.running >= self.enabled_total

    @property
    def counter_text(self) -> str:
        """计数文字，例如 "2/3"。"""
        if not self.total:
            return "无程序"
        return "{}/{}".format(self.running, self.total)

    @property
    def short_text(self) -> str:
        """一句话摘要（不含颜色），界面各处统一用这个。"""
        return format_bot_status(self)

    def clone(self) -> "BotStatus":
        """浅拷贝（映射字段做一份浅副本），避免调用方改到快照内部。"""
        return BotStatus(
            bot_id=self.bot_id,
            name=self.name,
            enabled=self.enabled,
            total=self.total,
            enabled_total=self.enabled_total,
            running=self.running,
            starting=self.starting,
            stopping=self.stopping,
            failed=self.failed,
            opened=self.opened,
            roles=dict(self.roles),
            program_names=dict(self.program_names),
            program_states=dict(self.program_states),
        )


#: 状态 -> 文字（同时覆盖 ProcessManager 的 idle 写法）
BOT_STATUS_TEXTS: Dict[str, str] = {
    BotStatusFlags.DISABLED: "已禁用",
    ProcessManager.STATE_IDLE: "未运行",
    BotStatusFlags.STOPPED: "未运行",
    BotStatusFlags.STARTING: "启动中…",
    BotStatusFlags.STOPPING: "停止中…",
    BotStatusFlags.RUNNING: "运行中",
    BotStatusFlags.PARTIAL: "部分运行",
    BotStatusFlags.FAILED: "异常",
}

#: 状态 -> 颜色（R2 起统一取自 app.ui.theme 的状态色表）
BOT_STATUS_COLORS: Dict[str, str] = theme_tokens.status_colors()

#: 状态 -> 圆点符号（避免用 emoji，保证各种字体下都能正常显示）
BOT_STATUS_DOTS: Dict[str, str] = {
    BotStatusFlags.DISABLED: "○",
    ProcessManager.STATE_IDLE: "○",
    BotStatusFlags.STOPPED: "○",
    BotStatusFlags.STARTING: "◐",
    BotStatusFlags.STOPPING: "◐",
    BotStatusFlags.RUNNING: "●",
    BotStatusFlags.PARTIAL: "◐",
    BotStatusFlags.FAILED: "!",
}


def bot_status_text(status: BotStatus) -> str:
    """状态文字，例如 "部分运行" / "运行中"。"""
    return BOT_STATUS_TEXTS.get(status.status, status.status)


def bot_status_color(status: BotStatus) -> str:
    """状态颜色（十六进制字符串）或 ""（表示无法着色）。"""
    color = BOT_STATUS_COLORS.get(status.status, "")
    if not status.enabled:
        return BOT_STATUS_COLORS[BotStatusFlags.DISABLED]
    return color


def bot_status_dot(status: BotStatus) -> str:
    """状态圆点符号。"""
    return BOT_STATUS_DOTS.get(status.status, "○")


def format_bot_status(status: BotStatus, with_opened: bool = True) -> str:
    """拼出一行状态摘要，例如 "部分运行 2/3  1 个异常 窗口已关闭"。

    侧栏、对话框、Tab 标题、状态栏共用这一份文案，改这里即可全局生效。
    """
    parts: List[str] = []
    if status.total == 0:
        parts.append("无程序")
    else:
        parts.append("{text} {running}/{total}".format(
            text=bot_status_text(status), running=status.running, total=status.total
        ))
    if status.failed:
        parts.append("{} 个异常".format(status.failed))
    if not status.enabled:
        parts.append("已禁用")
    if with_opened:
        parts.append("窗口已打开" if status.opened else "窗口已关闭")
    return "  ".join(parts)


def format_bot_status_rich(status: BotStatus, with_opened: bool = False) -> str:
    """带颜色的富文本片段（供 QLabel 使用），例如 "● 部分运行 2/3"。"""
    color = bot_status_color(status) or theme_tokens.COLOR_IDLE
    text = "{text} {running}/{total}".format(
        text=bot_status_text(status), running=status.running, total=status.total
    )
    if status.total == 0:
        text = bot_status_text(status)
    suffix = ""
    if with_opened:
        suffix = "&nbsp;<span style=\"color:{muted};\">{}</span>".format(
            "窗口已打开" if status.opened else "窗口已关闭",
            muted=theme_tokens.COLOR_IDLE,
        )
    return "<span style=\"color:{color};\">{dot}</span>&nbsp;{text}{suffix}".format(
        color=color, dot=bot_status_dot(status), text=text, suffix=suffix
    )


def format_program_status_line(status: BotStatus, program_key: str) -> str:
    """左侧导航里单个程序节点的一行摘要，例如 "主程序 · 运行中"。

    仅使用 BotStatus 快照，不访问管理器，便于 Phase B/C 复用。
    """
    name = status.program_names.get(program_key, program_key)
    state = status.program_states.get(program_key, ProcessManager.STATE_STOPPED)
    state_text = BOT_STATUS_TEXTS.get(state, BOT_STATUS_TEXTS[BotStatusFlags.STOPPED])
    return "{} · {}".format(name, state_text)


def format_program_status_color(status: BotStatus, program_key: str) -> str:
    """单个程序节点的颜色。"""
    state = status.program_states.get(program_key, ProcessManager.STATE_STOPPED)
    return BOT_STATUS_COLORS.get(state, theme_tokens.COLOR_IDLE)


# ---------------------------------------------------------------------------
# 运行参数设置对话框（轻量）
# ---------------------------------------------------------------------------

class RuntimeSettingsDialog(QDialog):
    """调整启动间隔、停止超时、日志行数等运行时参数（含「外观」）。"""

    #: 用户在下拉里换了外观（参数为主题模式字符串）
    theme_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        start_interval: float = DEFAULT_START_INTERVAL,
        stop_timeout: float = 10.0,
        log_max_lines: int = DEFAULT_LOG_LINES,
        manager: Optional[ProcessManager] = None,
        theme_mode: str = "",
        force_stop_on_close: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("运行参数")
        self.setMinimumWidth(460)
        self._manager = manager

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.interval_spin = QDoubleSpinBox(self)
        self.interval_spin.setRange(0.0, 120.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setSingleStep(0.5)
        self.interval_spin.setSuffix(" 秒")
        self.interval_spin.setValue(max(0.0, float(start_interval)))
        self.interval_spin.setToolTip("点击「启动全部」时，两个机器人之间的等待时间")
        form.addRow("机器人启动间隔：", self.interval_spin)

        self.timeout_spin = QDoubleSpinBox(self)
        self.timeout_spin.setRange(1.0, 600.0)
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setSingleStep(1.0)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setValue(max(1.0, float(stop_timeout)))
        self.timeout_spin.setToolTip("停止程序时先等待这么久，仍未退出就用 taskkill /F 强制结束")
        form.addRow("停止超时：", self.timeout_spin)

        self.lines_spin = QSpinBox(self)
        self.lines_spin.setRange(200, 200000)
        self.lines_spin.setSingleStep(500)
        self.lines_spin.setValue(max(200, int(log_max_lines)))
        self.lines_spin.setToolTip("每个日志窗口保留的最大行数，超出后自动丢弃最旧的行")
        form.addRow("日志最大行数：", self.lines_spin)

        # R2：外观（改动立即生效，不需要点确定）
        self.theme_combo = QComboBox(self)
        for mode in theme_tokens.THEME_MODES:
            self.theme_combo.addItem(theme_tokens.mode_label(mode), mode)
        self.theme_combo.setToolTip("浅色 / 深色 / 跟随系统；在这里改动会立即生效并记住")
        self.set_theme_mode(theme_mode or theme_tokens.current_mode())
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        form.addRow("外观：", self.theme_combo)

        layout.addLayout(form)

        self.theme_hint = QLabel("", self)
        self.theme_hint.setWordWrap(True)
        self.theme_hint.setStyleSheet(theme_tokens.dialog_hint_qss(self))
        layout.addWidget(self.theme_hint)

        self.show_manager_log_box = QCheckBox("把管理器自身日志也写入窗口", self)
        self.show_manager_log_box.setChecked(self._manager_log_enabled())
        self.show_manager_log_box.setToolTip("例如：启动命令、PID、taskkill 结果")
        layout.addWidget(self.show_manager_log_box)

        # 关闭时强制收摊（真机需求）：跳过确认、直接 taskkill /T /F
        self.force_stop_box = QCheckBox("关闭管理器时强制停止所有程序（不询问）", self)
        self.force_stop_box.setChecked(bool(force_stop_on_close))
        self.force_stop_box.setToolTip(
            "勾选后：关窗时不再弹「仍有 N 个程序在运行」的确认框，"
            "直接 taskkill /T /F 结束整棵进程树（跳过优雅等待，关窗更快）。\n"
            "不勾选：先弹确认框，并在停止超时内等待程序自行退出。"
        )
        layout.addWidget(self.force_stop_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText("确定")
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_theme_hint()

    def _manager_log_enabled(self) -> bool:
        if self._manager is None:
            return True
        return getattr(self._manager, "show_manager_log", True)

    # ---- 外观 ----

    def set_theme_mode(self, mode: str) -> None:
        """把下拉切到指定模式（不发信号、不触发切换）。"""
        normalized = theme_tokens.normalize_mode(mode)
        index = self.theme_combo.findData(normalized)
        if index < 0:
            index = 0
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(index)
        self.theme_combo.blockSignals(False)
        self._sync_theme_hint()

    def theme_mode(self) -> str:
        """下拉里当前选中的模式。"""
        return theme_tokens.normalize_mode(self.theme_combo.currentData())

    def _on_theme_combo_changed(self, _index: int) -> None:
        """下拉变化：立即通知宿主应用（不用点确定）。"""
        self._sync_theme_hint()
        self.theme_changed.emit(self.theme_mode())

    def _sync_theme_hint(self) -> None:
        """刷新外观说明文字。"""
        hint = getattr(self, "theme_hint", None)
        if hint is None:
            return
        try:
            hint.setText(theme_tokens.MODE_HINTS.get(self.theme_mode(), ""))
            hint.setStyleSheet(theme_tokens.dialog_hint_qss(hint))
        except (RuntimeError, AttributeError):
            pass

    def apply_theme(self) -> None:
        """主题变化时刷新本对话框自己的说明文字。"""
        self._sync_theme_hint()

    def values(self) -> Dict[str, object]:
        """返回界面上的参数。"""
        return {
            "start_interval": float(self.interval_spin.value()),
            "stop_timeout": float(self.timeout_spin.value()),
            "log_max_lines": int(self.lines_spin.value()),
            "show_manager_log": bool(self.show_manager_log_box.isChecked()),
            "force_stop_on_close": bool(self.force_stop_box.isChecked()),
            "theme": self.theme_mode(),
        }


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """QQBot 启动管理器主窗口。"""

    def __init__(
        self,
        config: Optional[BotConfig] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("{} - {}".format(APP_NAME, Path(DEFAULT_CONFIG_PATH).parent.name))
        self.resize(1180, 760)
        self.setMinimumSize(760, 520)

        self._settings = QSettings(ORG_NAME, APP_NAME)

        # ---- 配置 ----
        if config is not None:
            self.config = config
        else:
            config_path = Path(os.environ.get("QQBOT_CONFIG", str(DEFAULT_CONFIG_PATH)))
            self.config = BotConfig.load(config_path, create_if_missing=True)
        self._apply_runtime_settings_from_config()

        # ---- 进程管理器 ----
        self.manager = ProcessManager(self)
        self.manager.set_config_provider(self._find_program)
        self.manager.state_changed.connect(self._on_state_changed)
        # 注意：**不要**再把 log_message 也写进日志区。
        # 真机事故（2026-10-03）：管理器日志以前有两条写入路径 ——
        #   · ProcessManager._log() 发 output_text（带 "[管理器] " 前缀，见 BotTab）
        #   · 这里把 log_message 也 append 一次
        # 结果是每条管理器日志都显示两遍，而且「运行参数 → 把管理器自身日志也写入
        # 窗口」这个开关关不掉它（output_text 那条路不看开关）。
        # 现在只保留 output_text 这一条；开关按 channel == "manager" 在 BotTab 过滤
        # （见 BotTab.set_show_manager_log / _on_output_text）。

        # ---- 状态 ----
        #: bot_id -> BotTab（已打开窗口的机器人）
        self._tabs: Dict[str, BotTab] = {}
        self._starting_queue: List[str] = []
        self._start_timer: Optional[QTimer] = None
        self._show_manager_log = True
        #: 关闭管理器时是否强制停止所有程序（跳过确认 + 不做优雅等待）
        self._force_stop_on_close = False
        self._closing = False
        #: 最近一次打开的「查看已有 bot」对话框（Phase B）
        self._bot_list_dialog: Optional[object] = None
        #: 左侧导航栏：bot_id -> 树节点
        self._nav_items: Dict[str, QTreeWidgetItem] = {}
        #: 左侧导航栏是否折叠（Phase C）
        self._nav_collapsed = False
        #: 标签栏是否由用户手动勾选显示（左栏折叠时无论如何都显示）
        self._bot_tab_bar_forced = False
        #: 左栏状态是否已应用过（标签栏等它之后再决定显示，避免启动闪动）
        self._nav_state_applied = False
        #: 主题刷新是否已经排队（合并 paletteChanged 的密集触发，R2）
        self._theme_refresh_pending = False
        #: 是否正处在一次主题切换中（避免 paletteChanged 重复排队，R2）
        self._theme_applying = False
        #: _apply_theme 的执行次数（自检用，判断合并刷新是否生效，R2）
        self._theme_refresh_count = 0
        #: 已打开的对话框（主题切换时一起刷新，R2）
        self._open_dialogs: List[object] = []
        #: 「视图 → 外观」的三个单选项
        self._theme_actions: Dict[str, QAction] = {}
        #: 刷新导航时抑制信号，避免选中项抖动（Phase C）
        self._nav_guard = False
        #: 待局部刷新的机器人 id（步骤 3：避免整棵树重建）
        self._nav_dirty_bots: set = set()
        #: 是否需要整棵树重建
        self._nav_dirty_all = False
        #: 导航刷新去抖定时器
        self._nav_refresh_timer: Optional[QTimer] = None
        #: 左侧列表"当前高亮的程序行"（重建后据此恢复，修 1）
        self._nav_active_program_key: str = ""
        #: 左侧列表"当前高亮的机器人行"
        self._nav_active_bot_id: str = ""
        #: _refresh_nav 全量重建次数与"重建后没有选中项"的次数（自检/诊断用，修 1）
        self._nav_rebuild_count: int = 0
        self._nav_rebuild_miss_count: int = 0
        #: 导航时序诊断日志（--nav-debug 时打开）
        self._nav_debug = False
        self._nav_debug_lines: List[str] = []

        self._build_actions()
        self._build_toolbar()
        # 注意顺序：状态栏要先于中央区域构建。
        # QStackedWidget.addWidget() 会触发 currentChanged -> _on_current_page_changed
        # -> refresh_status()，如果此时 status_label 还没创建就会报
        # AttributeError: 'MainWindow' object has no attribute 'status_label'。
        self._build_statusbar()
        self._build_central()
        self._build_menus()

        self._restore_settings()
        self._refresh_title()
        self._apply_theme()
        # 系统切换深色/浅色主题时同步刷新（Qt 会广播调色板变化）
        app = QApplication.instance()
        if app is not None:
            try:
                app.paletteChanged.connect(self._on_palette_changed)
            except (AttributeError, TypeError):
                pass
        QTimer.singleShot(0, self._restore_open_tabs)
        # N3.0：跟随系统时定时检查系统深浅（paletteChanged 在部分 Windows 上不上报）
        self._start_os_theme_watch()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """窗口大小变化：把窗口最小宽度重新对齐到"内容的实际需要"。

        真机 2026-10-06："没有解决对整个管理器窗口大小操作时被压缩的问题" ——
        原因是主窗口自己写死了 `setMinimumWidth(460)`，而 Qt 里**显式设置的最小值
        会盖过布局算出来的最小值**，于是把窗口拖到 460 宽时，控制条 / 左栏按钮
        就被裁掉了。这里改成"跟着内容走"：取 max(460, minimumSizeHint().width())，
        内容需要多宽，窗口就拦在多宽（高度不受影响）。
        ⚠️ 只在数值变化时 setMinimumWidth（否则会变成"设最小值 → 重排 → 又设"的循环）。
        """
        super().resizeEvent(event)
        try:
            need = max(MIN_WINDOW_WIDTH, self.minimumSizeHint().width())
        except (RuntimeError, AttributeError, TypeError, ValueError):
            return
        if need != getattr(self, "_window_min_width", -1):
            self._window_min_width = need
            self.setMinimumWidth(need)

    def showEvent(self, event) -> None:
        """窗口第一次显示后，再补一次菜单栏配色（N2.8）。

        为什么必要：Qt 会在**控件首次绘制**时确定菜单栏的配色，此时若沿用
        构造阶段设下的那份，之后再切换主题就不会重取 —— 真机症状正是
        "启动时是什么明暗，菜单栏文字色就永远是什么颜色"。
        放在 show 之后再设一次，可以覆盖掉首次绘制造成的固着。
        """
        super().showEvent(event)
        if getattr(self, "_menubar_synced_after_show", False):
            return
        self._menubar_synced_after_show = True
        QTimer.singleShot(0, self._sync_menubar_theme)
        QTimer.singleShot(120, self._sync_menubar_theme)

    def _sync_menubar_theme(self) -> None:
        """把当前主题的配色套到菜单栏上（可反复调用）。"""
        menu_bar = getattr(self, "menu_bar", None) or self.menuBar()
        if menu_bar is None:
            return
        try:
            if menu_bar.isNativeMenuBar():
                menu_bar.setNativeMenuBar(False)
            menu_bar.setStyleSheet(theme_tokens.menubar_qss(menu_bar))
            theme_tokens.apply_menubar_palette(menu_bar, self)
        except (AttributeError, RuntimeError):
            pass

    # ------------------------------------------------------------------
    # 运行参数
    # ------------------------------------------------------------------

    def _apply_runtime_settings_from_config(self) -> None:
        """用配置 + QSettings 初始化运行时参数。"""
        self.start_interval = float(
            self._settings.value(SETTINGS_START_INTERVAL, self.config.start_interval)
            or 0.0
        )
        self.stop_timeout = float(
            self._settings.value(SETTINGS_STOP_TIMEOUT, self.config.stop_timeout)
            or 10.0
        )
        self.log_max_lines = int(
            self._settings.value(SETTINGS_LOG_LINES, self.config.log_max_lines)
            or DEFAULT_LOG_LINES
        )
        if self.log_max_lines <= 0:
            self.log_max_lines = DEFAULT_LOG_LINES
        # 注意：注册表里存的是 'true'/'false' 字符串，必须用 settings_bool 解析
        # （bool('false') 是 True —— 这个坑在 nav/collapsed 上踩过）
        self._force_stop_on_close = settings_bool(
            self._settings.value(SETTINGS_FORCE_STOP_ON_CLOSE, False), False
        )
        # 菜单项的勾选态跟着设置走（persist=False：刚读出来，不需要再写回）
        try:
            self.set_force_stop_on_close(self._force_stop_on_close, persist=False)
        except (AttributeError, RuntimeError):
            pass

    @property
    def stop_timeout_ms(self) -> int:
        """停止超时（毫秒）。"""
        return int(max(1.0, float(self.stop_timeout)) * 1000)

    def open_runtime_settings(self) -> None:
        """打开运行参数对话框（含「外观」下拉，可当场切换主题）。"""
        dialog = RuntimeSettingsDialog(
            self,
            start_interval=self.start_interval,
            stop_timeout=self.stop_timeout,
            log_max_lines=self.log_max_lines,
            manager=self.manager,
            force_stop_on_close=self._force_stop_on_close,
        )
        # R2：把「外观」初值填成当前模式，并让主窗口能立刻应用它
        try:
            dialog.set_theme_mode(theme_tokens.current_mode())
            dialog.theme_changed.connect(self.set_theme_mode)
        except (AttributeError, TypeError):
            pass
        self.register_dialog(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self.start_interval = float(values["start_interval"])
        self.stop_timeout = float(values["stop_timeout"])
        self.log_max_lines = int(values["log_max_lines"])
        self._show_manager_log = bool(values["show_manager_log"])
        self._sync_manager_log_flag()      # 立刻推给已打开的窗口（开关要即时生效）
        self._force_stop_on_close = bool(values.get("force_stop_on_close", False))
        try:
            self._settings.setValue(
                SETTINGS_FORCE_STOP_ON_CLOSE, self._force_stop_on_close
            )
            self._settings.sync()
        except (AttributeError, TypeError):
            pass
        self.statusBar().showMessage(
            "关闭时强制停止：{}".format("开" if self._force_stop_on_close else "关"),
            4000,
        )
        # 外观：对话框里选了就按它生效（用户点取消时这里不会走到）
        mode = str(values.get("theme", "") or "").strip()
        if mode:
            self.set_theme_mode(mode)

        self.config.start_interval = self.start_interval
        self.config.stop_timeout = self.stop_timeout
        self.config.log_max_lines = self.log_max_lines
        self.config.save()

        for tab in self._tabs.values():
            for key in tab.all_keys():
                view = tab.log_view(key)
                if view is not None:
                    view.set_max_lines(self.log_max_lines)
        self.statusBar().showMessage(
            "运行参数已更新：间隔 {:.1f}s / 超时 {:.1f}s / 日志 {} 行".format(
                self.start_interval, self.stop_timeout, self.log_max_lines
            ),
            5000,
        )

    # ------------------------------------------------------------------
    # 动作与工具栏
    # ------------------------------------------------------------------

    def _build_actions(self) -> None:
        # 新建 Bot
        self.action_new_bot = QAction("新建 Bot", self)
        self.action_new_bot.setShortcut(QKeySequence("Ctrl+N"))
        self.action_new_bot.setToolTip("创建一个新的机器人配置")
        self.action_new_bot.triggered.connect(self.new_bot)

        # 编辑当前 Bot
        self.action_edit_bot = QAction("编辑当前 Bot", self)
        self.action_edit_bot.setShortcut(QKeySequence("Ctrl+E"))
        self.action_edit_bot.setToolTip("编辑当前 Tab 对应机器人的配置")
        self.action_edit_bot.triggered.connect(lambda: self.edit_bot(self._current_bot_id()))

        # 启动当前 Bot
        self.action_start_bot = QAction("启动当前 Bot", self)
        self.action_start_bot.setShortcut(QKeySequence("F5"))
        self.action_start_bot.setToolTip("打开当前机器人的 Tab 并启动其所有程序")
        self.action_start_bot.triggered.connect(lambda: self.start_bot(self._current_bot_id()))

        # 停止当前 Bot
        self.action_stop_bot = QAction("停止当前 Bot", self)
        self.action_stop_bot.setShortcut(QKeySequence("Shift+F5"))
        self.action_stop_bot.setToolTip("停止当前机器人所有程序（含进程树）")
        self.action_stop_bot.triggered.connect(lambda: self.stop_bot(self._current_bot_id()))

        # 重启当前 Bot
        self.action_restart_bot = QAction("重启当前 Bot", self)
        self.action_restart_bot.setShortcut(QKeySequence("Ctrl+R"))
        self.action_restart_bot.setToolTip("先停止再启动当前机器人的所有程序")
        self.action_restart_bot.triggered.connect(lambda: self.restart_bot(self._current_bot_id()))

        # 查看已有 bot（Phase B）
        self.action_bot_list = QAction("查看已有 bot…", self)
        self.action_bot_list.setShortcut(QKeySequence("Ctrl+B"))
        self.action_bot_list.setToolTip(
            "列出全部机器人及其状态，可打开窗口 / 启动 / 停止 / 重启 / 编辑 / 删除"
        )
        self.action_bot_list.triggered.connect(self.open_bot_list_dialog)

        # 打开全部窗口（等价于空状态页上的同名按钮）
        self.action_open_all_windows = QAction("打开全部窗口", self)
        self.action_open_all_windows.setToolTip(
            "为所有机器人各打开一个窗口（不启动任何程序）"
        )
        self.action_open_all_windows.triggered.connect(self.open_all_windows)

        # 打开配置文件
        self.action_open_config = QAction("打开配置文件", self)
        self.action_open_config.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.action_open_config.setToolTip("用系统默认程序打开 bots_config.json")
        self.action_open_config.triggered.connect(self.open_config_file)

        # 启动全部 / 停止全部
        self.action_start_all = QAction("启动全部", self)
        self.action_start_all.setToolTip("按设定间隔依次启动所有启用的机器人")
        self.action_start_all.triggered.connect(self.start_all_bots)

        self.action_stop_all = QAction("停止全部", self)
        self.action_stop_all.setToolTip("停止所有正在运行的程序")
        self.action_stop_all.triggered.connect(self.stop_all_bots)

        # 重新载入配置
        self.action_reload_config = QAction("重新载入配置", self)
        self.action_reload_config.setShortcut(QKeySequence("F6"))
        self.action_reload_config.setToolTip("从 bots_config.json 重新读取配置")
        self.action_reload_config.triggered.connect(self.reload_config)

        # 关闭当前窗口 / 全部窗口
        self.action_close_tab = QAction("关闭当前窗口", self)
        self.action_close_tab.setShortcut(QKeySequence("Ctrl+W"))
        self.action_close_tab.setToolTip("关闭当前窗口（程序继续在后台运行）")
        self.action_close_tab.triggered.connect(self.close_current_tab)

        self.action_close_all_windows = QAction("关闭全部窗口", self)
        self.action_close_all_windows.setShortcut(QKeySequence("Ctrl+Shift+W"))
        self.action_close_all_windows.setToolTip(
            "关闭所有机器人窗口，程序继续在后台运行；需要结束进程请用「停止全部」"
        )
        self.action_close_all_windows.triggered.connect(
            lambda: self.close_all_windows(confirm=True)
        )

        # 运行参数
        self.action_settings = QAction("运行参数…", self)
        self.action_settings.triggered.connect(self.open_runtime_settings)

        # 折叠 / 展开左侧列表（Phase C，Phase D 起文案随状态变化）
        self.action_toggle_bot_tab_bar = QAction("显示窗口标签栏", self)
        self.action_toggle_bot_tab_bar.setCheckable(True)
        self.action_toggle_bot_tab_bar.setChecked(False)
        self.action_toggle_bot_tab_bar.setToolTip(
            "在实例区顶部显示浏览器式窗口标签栏\n"
            "（折叠左侧列表、或已有打开的窗口时会自动出现）"
        )
        self.action_toggle_bot_tab_bar.triggered.connect(
            self._on_toggle_bot_tab_bar_action
        )

        self.action_force_stop_on_close = QAction(
            "关闭时强制停止所有程序", self
        )
        self.action_force_stop_on_close.setCheckable(True)
        self.action_force_stop_on_close.setChecked(False)
        self.action_force_stop_on_close.setToolTip(
            "勾选后：关掉管理器窗口时不再询问，直接强制结束所有正在运行的程序\n"
            "（等价于「运行参数」里的同名开关，两处共享同一个设置）"
        )
        self.action_force_stop_on_close.toggled.connect(
            self._on_force_stop_on_close_toggled
        )

        # 左栏宽度恢复默认：万一记录里的宽度不合适（拖不回来 / 显示不全），
        # 给一个"一键回到 280px"的出口（真机 2026-10-05：左栏宽度出问题那次加的）
        self.action_reset_nav_width = QAction("左栏宽度：恢复默认", self)
        self.action_reset_nav_width.setToolTip("把左侧列表宽度恢复成默认的 280 像素")
        self.action_reset_nav_width.triggered.connect(self.reset_nav_width)

        self.action_toggle_nav = QAction("折叠左侧列表", self)
        self.action_toggle_nav.setShortcut(QKeySequence("Ctrl+L"))
        self.action_toggle_nav.setToolTip("折叠或展开左侧的机器人列表（Ctrl+L）")
        self.action_toggle_nav.triggered.connect(self._on_toggle_nav_action)

        # 在已打开的窗口之间切换（Phase C）
        self.action_next_window = QAction("下一个窗口", self)
        self.action_next_window.setShortcut(QKeySequence("Ctrl+Tab"))
        self.action_next_window.triggered.connect(lambda: self._cycle_page(1))

        self.action_prev_window = QAction("上一个窗口", self)
        self.action_prev_window.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        self.action_prev_window.triggered.connect(lambda: self._cycle_page(-1))

        # 状态图例（Phase D）
        self.action_status_legend = QAction("状态图例与快捷键…", self)
        self.action_status_legend.setToolTip("说明左侧列表的状态符号与常用快捷键")
        self.action_status_legend.triggered.connect(self.show_status_legend)

        # 关于
        self.action_about = QAction("关于", self)
        self.action_about.triggered.connect(self.show_about)

    def _build_toolbar(self) -> None:
        """顶部工具栏：**只保留一行**，全部命令都在这里（真机确认的布局）。

        演进过程（每一步都是真机反馈驱动的）：
          1. 原来只有一条主工具栏，挂满 11 个动作；
          2. 折叠左栏后按钮全没了 → 加了一条 Bot 工具条；
          3. 两条各挂一份「新建/编辑/启动/停止/重启/查看/配置」→ 用户反馈"有重复"；
          4. 按职责切开（主工具栏=全局，Bot 工具条=机器人/窗口）→ 用户确认
             "如果觉得主工具栏那 2 个按钮也可以并进 Bot 工具条（只留一行）" → **可以**。

        于是现在只有一条工具条，顺序按"用得多 → 全局"排：

            「当前 Bot ▾」（下拉：对当前 Bot / 对当前程序 各三个操作）
            ▏打开全部窗口 · 查看已有 bot…（=按需挑一个打开）
            ▏新建 Bot · 编辑当前 Bot · 打开配置文件
            ▏「启动全部 ▾」· 停止全部

        保留 ``self.toolbar`` 指向同一条，避免外部引用失效。
        """
        self._build_bot_bar()
        self.toolbar = self.bot_bar

    def _build_bot_bar(self) -> None:
        """顶部工具条（唯一一条）：机器人/窗口操作 + 全局动作。

        为什么需要（真机需求）："在左侧栏关闭时，把打开全部窗口和自定义打开的窗口等
        简略地放在图片那一栏" —— 左栏一折叠，那些按钮就没了，用户只能靠快捷键或菜单。
        这里复用**同一批 QAction**（QAction 可以被多处引用，不会出现两份状态，
        菜单/快捷键/禁用态自动同步），顺序按"用得多 → 全局"排：
            启动 / 停止 / 重启 ▏打开全部窗口 / 查看已有 bot…（=自定义打开）
            ▏新建 / 编辑 / 打开配置文件 ▏「启动全部 ▾」/ 停止全部

        它**始终可见**（见 ``_update_bot_tab_bar_visible``）；浏览器式窗口标签栏
        按"是否有窗口可切"显示。
        """
        bar = QToolBar("Bot 工具条", self)
        bar.setObjectName("botBar")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        # 分组按"用得多 → 用得少"排：
        #   ① 常用操作：启动 / 停止 / 重启当前 Bot
        #   ② 窗口：打开全部窗口 · 查看已有 bot…（=按需挑一个打开）
        #   ③ 管理：新建 · 编辑当前 Bot · 打开配置文件
        # 「当前 Bot ▾」：把 启动/停止/重启当前 Bot 三个按钮合并成一个下拉，
        # 菜单里还有"对当前程序"的三个操作（见 build_current_bot_menu）
        self.current_bot_button = QPushButton("当前 Bot  ▾", bar)
        self.current_bot_button.setToolTip(
            "对当前 Bot / 当前程序：启动 · 停止 · 重启（快捷键 F5 / Shift+F5 / Ctrl+R）")
        self.current_bot_button.clicked.connect(self._on_current_bot_button)
        #  真机要求（2026-10-06）："给当前 bot 左边隔开一点" ——
        #  左边留白，别贴着窗口边（右边也留一点，见下面的 gap）。
        gap_before = QWidget(bar)
        gap_before.setFixedWidth(10)
        bar.addWidget(gap_before)
        bar.addWidget(self.current_bot_button)
        #  真机要求（2026-10-06）："给当前 bot 这边隔开一点" ——
        #  「当前 Bot ▾」是"对当前实例操作"的入口，和后面的窗口/管理两组之间留点
        #  气口（纯间隔控件，不占逻辑），免得按钮和分隔线糊在一起。
        gap = QWidget(bar)
        gap.setFixedWidth(10)
        bar.addWidget(gap)
        bar.addSeparator()
        gap_after = QWidget(bar)
        gap_after.setFixedWidth(6)
        bar.addWidget(gap_after)
        bar.addAction(self.action_open_all_windows)
        bar.addAction(self.action_bot_list)
        bar.addSeparator()
        bar.addAction(self.action_new_bot)
        bar.addAction(self.action_edit_bot)
        bar.addAction(self.action_open_config)
        bar.addSeparator()
        # 全局动作放最后（用得最少，且影响面最大）
        # 「启动全部」+ 紧跟一个独立的小箭头按钮。
        # 两个都用**普通 QPushButton**（跟窗格标题栏上的「布局 ▾」同一套做法）：
        # QToolButton 只要带菜单，Qt 就会自己在按钮里画一个 menu-indicator（小箭头），
        # 关掉它又得写样式表；普通按钮根本不会画 —— 真机反馈了三次"多余的勾/箭头"，
        # 这里从根上避免。穿透问题也不再有副作用：菜单关掉那一下最多把菜单再打开。
        self.start_all_button = QPushButton("启动全部", bar)
        self.start_all_button.setToolTip("一键启动所有启用的机器人")
        self.start_all_button.clicked.connect(self.action_start_all.trigger)
        bar.addWidget(self.start_all_button)

        self.start_pick_button = QPushButton("▾", bar)
        self.start_pick_button.setFixedWidth(28)
        self.start_pick_button.setToolTip("勾选这次要启动哪些程序（只启动勾上的）")
        self.start_pick_button.clicked.connect(self._on_start_pick_button)
        bar.addWidget(self.start_pick_button)
        bar.addAction(self.action_stop_all)
        # 始终可用：它就是"那一排操作按钮"，折叠左栏后更离不开它
        bar.setVisible(True)
        self.addToolBar(bar)
        self.bot_bar = bar

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        # N2.6：一建好就脱离系统原生菜单栏。
        # Windows 的原生菜单栏按**系统明暗**绘制，不跟随我们用 setPalette 设的主题，
        # 于是"深色模式黑字/浅色模式白字"，而且会残留上一次切换的颜色。
        # 关掉原生绘制后，菜单栏与菜单都由 Qt 画，永远与窗口配色一致。
        try:
            if menu_bar.isNativeMenuBar():
                menu_bar.setNativeMenuBar(False)
        except (AttributeError, RuntimeError):
            pass

        file_menu = menu_bar.addMenu("文件(&F)")
        file_menu.addAction(self.action_new_bot)
        file_menu.addAction(self.action_edit_bot)
        file_menu.addAction(self.action_bot_list)
        file_menu.addSeparator()
        file_menu.addAction(self.action_open_config)
        file_menu.addAction(self.action_reload_config)
        file_menu.addSeparator()
        file_menu.addAction(self.action_close_tab)
        file_menu.addAction(self.action_close_all_windows)

        bot_menu = menu_bar.addMenu("机器人(&B)")
        bot_menu.addAction(self.action_bot_list)
        bot_menu.addSeparator()
        bot_menu.addAction(self.action_start_bot)
        bot_menu.addAction(self.action_stop_bot)
        bot_menu.addAction(self.action_restart_bot)
        bot_menu.addSeparator()
        bot_menu.addAction(self.action_start_all)
        bot_menu.addAction(self.action_stop_all)

        # 视图菜单（Phase D：加「第 1..9 个机器人」与状态图例）
        self.view_menu = menu_bar.addMenu("视图(&V)")
        self.view_menu.aboutToShow.connect(self._refresh_view_bot_actions)
        self.view_menu.addAction(self.action_next_window)
        self.view_menu.addAction(self.action_prev_window)
        self.view_menu.addSeparator()
        self._build_view_bot_actions()
        self.view_menu.addSeparator()
        # 步骤 4：分屏布局子菜单（每次展开时按当前窗口重建，勾选状态才是最新的）
        self.layout_menu = self.view_menu.addMenu("分屏布局")
        self.layout_menu.aboutToShow.connect(self._rebuild_layout_menu)
        self._rebuild_layout_menu()
        # R2：外观（浅色 / 深色 / 跟随系统）子菜单
        self.theme_menu = self.view_menu.addMenu("外观")
        self.theme_menu.aboutToShow.connect(self._refresh_theme_actions)
        self._build_theme_actions()
        self.view_menu.addAction(self.action_toggle_bot_tab_bar)
        self.view_menu.addAction(self.action_toggle_nav)
        self.view_menu.addAction(self.action_reset_nav_width)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.action_force_stop_on_close)
        self.view_menu.addAction(self.action_settings)

        help_menu = menu_bar.addMenu("帮助(&H)")
        help_menu.addAction(self.action_status_legend)
        help_menu.addSeparator()
        help_menu.addAction(self.action_about)

    # ------------------------------------------------------------------
    # 步骤 4：分屏布局菜单
    # ------------------------------------------------------------------

    def _layout_kinds(self) -> List[str]:
        """可选的布局模板（顺序即菜单顺序）。"""
        return [
            layout_model.KIND_V,
            layout_model.KIND_H,
            layout_model.KIND_H_LEFT_V,
            layout_model.KIND_H_RIGHT_V,
            layout_model.KIND_TABS,
            layout_model.KIND_SINGLE,
        ]

    def _current_layout_tab(self) -> Optional[BotTab]:
        """当前窗口对应的 BotTab（没有打开窗口时返回 None）。"""
        return self.current_tab()

    def _layout_target_bot_id(self) -> str:
        """布局操作的目标机器人：优先当前窗口，其次左栏选中项。"""
        return self._current_bot_id() or self._nav_selected_bot_id() or ""

    def _rebuild_layout_menu(self) -> None:
        """重建「视图 → 分屏布局」子菜单（aboutToShow 的槽）。

        每次展开菜单都会跑，里面要问当前窗口的布局 —— 窗口可能正好在重建
        （控件已 deleteLater）。异常不许冒出去：PyQt6 对槽里的未捕获异常会
        直接终止进程。所以整段包在 _rebuild_layout_menu_impl() 里兜底。
        """
        try:
            self._rebuild_layout_menu_impl()
        except (RuntimeError, AttributeError, TypeError, ValueError):
            return

    def _rebuild_layout_menu_impl(self) -> None:
        """真正干活的版本（只由上面的槽调用）。"""
        menu = getattr(self, "layout_menu", None)
        if menu is None:
            return
        menu.clear()

        bot_id = self._layout_target_bot_id()
        bot = self.config.get_bot(bot_id) if bot_id else None
        tab = self.tab_for(bot_id) if bot_id else None
        current = tab.layout_kind() if tab is not None else ""

        header = QAction(
            "目标：{}".format(bot.name if bot is not None else "（未选中机器人）"), menu
        )
        header.setEnabled(False)
        menu.addAction(header)
        if bot is not None:
            status = self.bot_status_of(bot.id)
            info = QAction(
                "{} 个程序　{}".format(status.total, "窗口已打开" if status.opened else "窗口未打开"),
                menu,
            )
            info.setEnabled(False)
            menu.addAction(info)
        menu.addSeparator()

        for kind in self._layout_kinds():
            action = QAction(layout_model.LAYOUT_KIND_LABELS.get(kind, kind), menu)
            action.setCheckable(True)
            action.setChecked(bool(current) and kind == current)
            action.setEnabled(bool(bot_id))
            action.triggered.connect(lambda _checked=False, k=kind: self.apply_layout(k))
            menu.addAction(action)

        menu.addSeparator()
        custom = QAction("自定义（拖出来的布局）", menu)
        custom.setEnabled(False)
        custom.setCheckable(True)
        custom.setChecked(current == layout_model.KIND_CUSTOM)
        menu.addAction(custom)

        reset = QAction("恢复默认布局", menu)
        reset.setEnabled(bool(bot_id))
        reset.triggered.connect(self.reset_layout)
        menu.addAction(reset)

        menu.addSeparator()
        apply_all = QAction("应用到全部机器人", menu)
        apply_all.setEnabled(bool(self.config.bots))
        apply_all.setToolTip("把当前模板应用到配置里的每个机器人（并记住，下次打开窗口即生效）")
        apply_all.triggered.connect(lambda: self.apply_layout_to_all(current or layout_model.KIND_V))
        menu.addAction(apply_all)

        if tab is not None:
            describe = QAction("当前：{}".format(tab.layout_description()), menu)
            describe.setEnabled(False)
            menu.addAction(describe)

    def apply_layout(self, kind: str) -> bool:
        """切换当前机器人的分屏布局（窗口已打开时立即生效并记住）。"""
        bot_id = self._layout_target_bot_id()
        if not bot_id:
            self.statusBar().showMessage("请先打开或选中一个机器人。", 4000)
            return False
        tab = self.tab_for(bot_id)
        if tab is not None:
            ok = tab.set_layout(kind, notify=True)
            if ok:
                self._after_layout_changed(bot_id, kind)
            return ok
        # 窗口没打开：先记进 QSettings，下次打开该窗口时按此布局渲染
        self._remember_layout_kind(bot_id, kind)
        bot = self.config.get_bot(bot_id)
        self.statusBar().showMessage(
            "「{}」的布局已记录为：{}（打开窗口后生效）".format(
                bot.name if bot is not None else bot_id,
                layout_model.LAYOUT_KIND_LABELS.get(kind, kind),
            ),
            6000,
        )
        return True

    def reset_layout(self) -> bool:
        """恢复当前机器人的默认布局。"""
        bot_id = self._layout_target_bot_id()
        if not bot_id:
            return False
        tab = self.tab_for(bot_id)
        if tab is None:
            self._forget_layout(bot_id)
            self.statusBar().showMessage("已恢复默认布局（窗口未打开，下次打开生效）。", 5000)
            return True
        ok = tab.reset_layout()
        if ok:
            self._after_layout_changed(bot_id, tab.layout_kind())
        return ok

    def apply_layout_to_all(self, kind: str) -> int:
        """把一个布局模板应用到所有机器人（已打开的立即生效，未打开的记进设置）。"""
        count = 0
        for bot in self.config.bots:
            tab = self.tab_for(bot.id)
            if tab is not None:
                if tab.set_layout(kind, notify=True):
                    self._after_layout_changed(bot.id, kind)
                    count += 1
            else:
                self._remember_layout_kind(bot.id, kind)
                count += 1
        self.statusBar().showMessage(
            "已把布局「{}」应用到 {} 个机器人。".format(
                layout_model.LAYOUT_KIND_LABELS.get(kind, kind), count
            ),
            6000,
        )
        return count

    def _after_layout_changed(self, bot_id: str, kind: str) -> None:
        """布局切换后：记录模板名 + 刷新菜单/提示。"""
        if kind and kind != layout_model.KIND_CUSTOM:
            self._remember_layout_kind(bot_id, kind)
        else:
            self._remember_layout_tree(bot_id, self.tab_for(bot_id))
        self._rebuild_layout_menu()
        self._schedule_nav_refresh(bot_id)

    def _remember_layout_kind(self, bot_id: str, kind: str) -> None:
        """把布局模板名写进 QSettings（本机界面偏好）。"""
        if not bot_id:
            return
        try:
            self._settings.setValue(layout_model.settings_layout_key(bot_id), str(kind))
            # 模板布局不再需要自定义树
            if kind != layout_model.KIND_CUSTOM:
                self._settings.remove(layout_model.settings_tree_key(bot_id))
            self._settings.sync()
        except (TypeError, ValueError):
            pass

    def _remember_layout_tree(self, bot_id: str, tab: Optional[BotTab]) -> None:
        """把自定义布局树写进 QSettings。"""
        if not bot_id or tab is None:
            return
        try:
            self._settings.setValue(
                layout_model.settings_layout_key(bot_id), layout_model.KIND_CUSTOM
            )
            self._settings.setValue(
                layout_model.settings_tree_key(bot_id),
                layout_model.tree_to_json(tab.layout_tree()),
            )
            self._settings.sync()
        except (TypeError, ValueError):
            pass

    def _forget_layout(self, bot_id: str) -> None:
        """清掉某个机器人的布局偏好（恢复默认 / 删除机器人时用）。"""
        if not bot_id:
            return
        try:
            for prefix in layout_model.SETTINGS_PANE_PREFIXES:
                if prefix == layout_model.SETTINGS_PANE_SIZES:
                    # 尺寸键带子路径，需要按前缀清理
                    for key in list(self._settings.allKeys()):
                        if key.startswith(prefix + bot_id):
                            self._settings.remove(key)
                    continue
                self._settings.remove(prefix + bot_id)
            self._settings.sync()
        except (TypeError, ValueError):
            pass

    def _restore_pane_layout(self, bot_id: str, tab: BotTab) -> bool:
        """打开窗口时套用 QSettings 里的布局偏好（模板 / 自定义树 / 比例 / 焦点）。"""
        restored = False
        try:
            kind = str(
                self._settings.value(layout_model.settings_layout_key(bot_id), "") or ""
            ).strip()
            if kind == layout_model.KIND_CUSTOM:
                tree = layout_model.tree_from_json(
                    self._settings.value(layout_model.settings_tree_key(bot_id), "")
                )
                if tree is not None:
                    restored = tab.apply_layout_tree(tree, notify=False)
            elif kind:
                restored = tab.set_layout(kind, notify=False)

            sizes = self._settings.value(layout_model.settings_sizes_key(bot_id), None)
            if isinstance(sizes, dict) and sizes:
                tab.set_pane_sizes(sizes)
                restored = True
            elif sizes:
                # QSettings 有时把字典读成字符串，这里忽略即可（比例退回默认）
                pass

            focus_key = str(
                self._settings.value(layout_model.settings_focus_key(bot_id), "") or ""
            ).strip()
            if focus_key:
                tab.focus_program(focus_key, scroll=False)
        except (RuntimeError, AttributeError, TypeError, ValueError):
            return restored
        return restored

    def _save_pane_layout(self, bot_id: str, tab: Optional[BotTab] = None) -> None:
        """把当前布局（模板 / 自定义树 / 比例 / 焦点）写进 QSettings。"""
        target = tab if tab is not None else self.tab_for(bot_id)
        if target is None or not bot_id:
            return
        kind = target.layout_kind()
        if kind == layout_model.KIND_CUSTOM:
            self._remember_layout_tree(bot_id, target)
        else:
            self._remember_layout_kind(bot_id, kind)
        try:
            sizes = target.pane_sizes()
            if sizes:
                self._settings.setValue(layout_model.settings_sizes_key(bot_id), sizes)
            focused = target.focused_program()
            if focused:
                self._settings.setValue(layout_model.settings_focus_key(bot_id), focused)
            self._settings.sync()
        except (TypeError, ValueError):
            pass

    def _on_tab_layout_changed(self, bot_id: str, _kind: str) -> None:
        """BotTab 报告布局或比例变化：立刻持久化。"""
        self._save_pane_layout(bot_id)
        self._rebuild_layout_menu()

    def _on_tab_program_action(self, key: str, action: str) -> None:
        """窗格上的 启动/停止/重启 按钮。"""
        bot_id, _program = self._resolve_key(key)
        if not bot_id:
            return
        if action == "start":
            self._on_tab_start_requested([key])
        elif action == "stop":
            self._on_tab_stop_requested([key])
        elif action == "restart":
            self._on_tab_restart_requested([key])
        self._schedule_nav_refresh(bot_id)

    def _on_tab_focus_changed(self, bot_id: str, key: str) -> None:
        """BotTab 报告焦点程序变化：记住它，并把 ▸ 标记刷到左栏（R1-2）。"""
        if not bot_id or not key:
            return
        try:
            self._settings.setValue(layout_model.settings_focus_key(bot_id), key)
        except (TypeError, ValueError):
            pass
        # 确认 1：▸ 全局唯一，只标记"当前窗口正在显示的程序"；
        # 非当前窗口发来的焦点变化只记偏好，不动标记。
        if bot_id == (self._current_bot_id() or ""):
            self._apply_nav_focus_marker()
        else:
            self._nav_debug_log(
                "忽略后台窗口的焦点变化：bot={!r}（当前窗口是 {!r}）".format(
                    bot_id, self._current_bot_id()
                )
            )

    # ------------------------------------------------------------------
    # R2：外观（浅色 / 深色 / 跟随系统）
    # ------------------------------------------------------------------

    def _build_theme_actions(self) -> None:
        """构造「视图 → 外观」里的三个单选项（每次构建只做一次）。"""
        menu = getattr(self, "theme_menu", None)
        if menu is None:
            return
        self._theme_actions: Dict[str, QAction] = {}
        for mode in theme_tokens.THEME_MODES:
            action = QAction(theme_tokens.mode_label(mode), self)
            action.setCheckable(True)
            action.setToolTip(theme_tokens.MODE_HINTS.get(mode, ""))
            action.triggered.connect(
                lambda _checked=False, m=mode: self.set_theme_mode(m)
            )
            menu.addAction(action)
            self._theme_actions[mode] = action

        menu.addSeparator()
        self.action_toggle_theme = QAction("在浅色/深色之间切换", self)
        self.action_toggle_theme.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.action_toggle_theme.setToolTip(
            "快捷键 Ctrl+Shift+D：浅色 ⇄ 深色（切到显式模式后会记住，不再跟随系统）"
        )
        self.action_toggle_theme.triggered.connect(self.toggle_theme)
        menu.addAction(self.action_toggle_theme)

        self.action_reset_theme = QAction("恢复为跟随系统", self)
        self.action_reset_theme.triggered.connect(
            lambda: self.set_theme_mode(theme_tokens.MODE_SYSTEM)
        )
        menu.addAction(self.action_reset_theme)

        menu.addSeparator()
        self.action_palette_studio = QAction("配色工作台…", self)
        self.action_palette_studio.setToolTip(
            "逐个调整界面颜色与日志颜色（深浅各一套）：改一下立即生效，"
            "点「保存」写进设置并留下一条配色记录"
        )
        self.action_palette_studio.triggered.connect(self.open_palette_studio)
        menu.addAction(self.action_palette_studio)

        self._refresh_theme_actions()

    def open_palette_studio(self):
        """打开配色工作台（改了**立即生效**，不用重启）。

        延迟 import：这个对话框只在用户点菜单时才需要，
        没必要让它参与启动路径（少一个启动期依赖）。
        """
        from app.ui.palette_dialog import PaletteDialog

        dialog = getattr(self, "_palette_dialog", None)
        if dialog is None:
            dialog = PaletteDialog(parent=self, dark=theme_tokens.is_dark(self))
            dialog.colorsChanged.connect(self._on_palette_colors_changed)
            dialog.colorsSaved.connect(self._on_palette_colors_saved)
            dialog.finished.connect(self._on_palette_dialog_closed)
            self._palette_dialog = dialog
        try:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        except RuntimeError:
            self._palette_dialog = None
            return None
        return dialog

    def _on_palette_colors_changed(self) -> None:
        """配色工作台改了颜色：**立刻**重刷整个界面。

        `_apply_theme()` 已经覆盖了外壳（菜单栏/工具栏/状态栏）、左栏、
        标签栏、空状态页、所有已打开的实例窗口以及活着的对话框 ——
        所以这里只要调它一次，用户就能看到"改一下马上变"。
        """
        try:
            self._apply_theme()
        except (RuntimeError, AttributeError):
            return
        try:
            self.statusBar().showMessage("配色已更新（配色工作台）", 2500)
        except (RuntimeError, AttributeError):
            pass

    def _on_palette_colors_saved(self) -> None:
        """工作台保存/清除/套用记录之后：提示一句，并刷新状态栏里的配色信息。"""
        try:
            self._apply_theme()
        except (RuntimeError, AttributeError):
            pass
        try:
            custom = theme_tokens.custom_colors()
            count = len(custom)
            self.statusBar().showMessage(
                "配色已保存：{} 项自定义（重启后依然生效）".format(count)
                if count else "已恢复内置配色", 4000)
        except (RuntimeError, AttributeError):
            pass

    def _on_palette_dialog_closed(self, _result: int = 0) -> None:
        """对话框关掉时把引用放掉（下次再开是全新的，避免读到过期状态）。"""
        self._palette_dialog = None

    def current_theme_mode(self) -> str:
        """当前主题模式（来自 app.ui.theme 的进程内记忆）。"""
        return theme_tokens.current_mode()

    def _refresh_theme_actions(self) -> None:
        """刷新勾选状态（动作可能是在别处被改的，例如命令行或快捷键）。"""
        mode = self.current_theme_mode()
        for key, action in getattr(self, "_theme_actions", {}).items():
            try:
                action.setChecked(key == mode)
            except RuntimeError:
                continue

    def sync_theme_actions(self) -> None:
        """供外部（main.py）在应用完启动外观后同步勾选状态。"""
        self._refresh_theme_actions()

    def set_theme_mode(self, mode: str, remember: bool = True) -> str:
        """切换主题模式：立即生效 + 记住偏好（写 QSettings 的 ui/theme）。

        返回规范化之后的模式。remember=False 时只生效、不写偏好
        （命令行 --theme 临时覆盖用）。
        """
        normalized = theme_tokens.normalize_mode(mode)
        self._theme_applying = True
        try:
            normalized = theme_tokens.apply_theme(QApplication.instance(), normalized)
            if remember:
                theme_tokens.save_mode(self._settings, normalized)
            # apply_theme 已经 unpolish/polish 过原生控件，这里补齐自定义 QSS
            self._apply_theme()
        finally:
            self._theme_applying = False
            # apply_theme 期间排队的 paletteChanged 刷新可以丢掉了（已经刷过）
            self._theme_refresh_pending = False
        self._refresh_theme_actions()
        self._rebuild_layout_menu()
        self.statusBar().showMessage(
            "外观已切换为「{}」{}".format(
                theme_tokens.mode_label(normalized),
                "" if remember else "（本次运行生效，不写入偏好）",
            ),
            4000,
        )
        return normalized

    def toggle_theme(self) -> str:
        """在浅色 / 深色之间快切（Ctrl+Shift+D）。"""
        target = (
            theme_tokens.MODE_LIGHT
            if theme_tokens.is_dark(self)
            else theme_tokens.MODE_DARK
        )
        return self.set_theme_mode(target)

    def _apply_dialog_theme(self, dialog: object) -> None:
        """把当前主题刷到已打开的对话框（对话框不是主窗口的子控件树）。"""
        if dialog is None:
            return
        apply_theme = getattr(dialog, "apply_theme", None)
        if callable(apply_theme):
            try:
                apply_theme()
                return
            except (RuntimeError, AttributeError, TypeError):
                pass
        hint = getattr(dialog, "hint_label", None)
        if hint is not None:
            try:
                hint.setStyleSheet(theme_tokens.dialog_hint_qss(hint))
            except (RuntimeError, AttributeError):
                pass

    def register_dialog(self, dialog: object) -> None:
        """登记一个打开的对话框，主题切换时一起刷新。"""
        if dialog is None:
            return
        dialogs = getattr(self, "_open_dialogs", None)
        if dialogs is None:
            dialogs = []
            self._open_dialogs = dialogs
        if dialog not in dialogs:
            dialogs.append(dialog)

    def unregister_dialog(self, dialog: object) -> None:
        """取消登记（对话框关闭且即将销毁时调用）。"""
        dialogs = getattr(self, "_open_dialogs", None)
        if not dialogs or dialog is None:
            return
        try:
            dialogs.remove(dialog)
        except ValueError:
            pass

    def _live_dialogs(self) -> List[object]:
        """还活着的对话框列表（顺手清掉已经被 Qt 销毁的那些）。"""
        dialogs = getattr(self, "_open_dialogs", None)
        if not dialogs:
            return []
        alive: List[object] = []
        for dialog in dialogs:
            try:
                # 已销毁的 QObject 访问任何属性都会抛 RuntimeError
                dialog.isVisible()
            except RuntimeError:
                continue
            except AttributeError:
                pass
            alive.append(dialog)
        if len(alive) != len(dialogs):
            self._open_dialogs = alive
        return alive

    def _build_view_bot_actions(self) -> None:
        """「视图」菜单里动态生成"第 N 个机器人"条目（Ctrl+1..Ctrl+9）。

        这些条目每次显示菜单时按当前配置重建，因此增删机器人后不需要重启程序。
        """
        self._bot_jump_actions: List[QAction] = []
        for index in range(9):
            action = QAction("第 {} 个机器人".format(index + 1), self)
            action.setShortcut(QKeySequence("Ctrl+{}".format(index + 1)))
            action.setEnabled(False)
            action.triggered.connect(lambda _checked=False, i=index: self._jump_to_bot(i))
            self.view_menu.addAction(action)
            self._bot_jump_actions.append(action)

    def _refresh_view_bot_actions(self) -> None:
        """按当前配置刷新「第 N 个机器人」条目的文字与可用状态。"""
        actions = getattr(self, "_bot_jump_actions", None)
        if not actions:
            return
        for index, action in enumerate(actions):
            if index < len(self.config.bots):
                bot = self.config.bots[index]
                status = self.bot_status(bot)
                action.setText(
                    "第 {} 个：{} ［{}］".format(index + 1, bot.name, status.counter_text)
                )
                action.setEnabled(True)
            else:
                action.setText("第 {} 个：（无）".format(index + 1))
                action.setEnabled(False)

    def _jump_to_bot(self, index: int) -> None:
        """Ctrl+1..9：打开（必要时）并显示第 index 个机器人（高亮落到主程序行）。"""
        if index < 0 or index >= len(self.config.bots):
            return
        bot = self.config.bots[index]
        self.show_bot_view(bot.id, focus=True, focus_first=True)

    def _build_statusbar(self) -> None:
        """状态栏（必须早于中央区域构建，见 __init__ 里的注释）。

        右侧除配置文件路径外，还常驻一个**权限标记**：
        需要提权的 bot（例如消防栓的 HttpListener 要绑定 http 前缀）在
        "管理器非管理员"时每次启动都会弹 UAC；管理器本身是管理员则一次都不弹。
        把状态摆在明面上，省得每次都要猜"为什么又弹窗了"。

        权限标志由 `main.py` 在启动时放到 QApplication 上（属性名见
        `ADMIN_FLAG_ATTR`）—— 这样做是为了不让 main_window 反过来 import main
        （会形成循环导入）。
        """
        bar = self.statusBar()
        self.status_label = QLabel("就绪", self)
        self.admin_label = QLabel("", self)
        is_admin = self._is_admin_process()
        if is_admin:
            # 用纯文本 + ASCII 标记，避免彩色 emoji 在部分字体下显示成方块/豆腐块
            self.admin_label.setText("[管理员] 提权程序不再弹 UAC")
            self.admin_label.setToolTip(
                "管理器以管理员身份运行：启动需要提权的程序时，子进程会直接继承"
                "管理员令牌，不会再弹 UAC。\n"
                "想回到普通权限，用不带 --elevate 的方式启动即可。"
            )
        else:
            self.admin_label.setText("[普通权限] 提权程序启动时会弹 UAC")
            self.admin_label.setToolTip(
                "管理器是普通权限：启动需要提权的程序（如消防栓的 HttpListener）"
                "会先弹一次 UAC。\n"
                "想避免每次弹窗，用「python main.py --elevate」启动管理器 —— "
                "只在启动时弹一次，之后所有子进程都继承管理员令牌。"
            )
        self.path_label = QLabel(str(self.config.path), self)
        self.path_label.setToolTip("配置文件路径：{}".format(self.config.path))
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.admin_label)
        bar.addPermanentWidget(self.path_label)

    def _is_admin_process(self) -> bool:
        """当前进程是否带管理员令牌。

        优先读 `main.py` 放在 QApplication 上的标志（属性名见 ADMIN_FLAG_ATTR）；
        没有时（例如自检脚本直接构造 MainWindow）退回自带的 Windows 检测，
        避免为了一个布尔值把 main 反向 import 进来。
        """
        app = QApplication.instance()
        if app is not None:
            flag = getattr(app, ADMIN_FLAG_ATTR, None)
            if isinstance(flag, bool):
                return flag
        if os.name != "nt":
            return False
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (ImportError, AttributeError, OSError):
            return False

    def is_admin_mode(self) -> bool:
        """对外暴露的只读查询（自检与状态提示用）。"""
        return self._is_admin_process()

    # ------------------------------------------------------------------
    # Phase A：Bot 状态公共层（左侧导航 / 对话框 / 标题 / 状态栏共用）
    # ------------------------------------------------------------------

    def bot_status(self, bot: Bot) -> BotStatus:
        """计算一个机器人的状态快照。

        只读：查询 ProcessManager 与已打开的 BotTab，不修改任何状态、不发信号。
        调用方（左侧导航栏、查看已有 bot 对话框、状态栏）拿到的是同一份数据，
        因此颜色与文案天然一致。
        """
        status = BotStatus(
            bot_id=getattr(bot, "id", "") or "",
            name=getattr(bot, "name", "") or "",
            enabled=bool(getattr(bot, "enabled", True)),
            total=0,
            enabled_total=0,
            opened=self._tabs.get(getattr(bot, "id", "")) is not None,
        )

        for program in getattr(bot, "programs", []) or []:
            key = build_manager_key(status.bot_id, getattr(program, "id", "") or "")
            status.total += 1
            if getattr(program, "enabled", True):
                status.enabled_total += 1
            status.roles[key] = getattr(program, "role", "") or ""
            status.program_names[key] = getattr(program, "name", "") or key

            state = self._program_state(key)
            status.program_states[key] = state
            if state == ProcessManager.STATE_RUNNING:
                status.running += 1
            elif state in (ProcessManager.STATE_STARTING, ProcessManager.STATE_STOPPING):
                if state == ProcessManager.STATE_STARTING:
                    status.starting += 1
                else:
                    status.stopping += 1
            elif state == ProcessManager.STATE_FAILED:
                status.failed += 1

        return status

    def _program_state(self, key: str) -> str:
        """单个程序的原始状态（无管理器时视为未运行）。"""
        if self.manager is None:
            return ProcessManager.STATE_IDLE
        try:
            state = self.manager.state(key)
        except (RuntimeError, AttributeError):
            return ProcessManager.STATE_IDLE
        if state in BotStatusFlags.STOPPED_ALIASES:
            return BotStatusFlags.STOPPED
        return state

    def bot_status_of(self, bot_id: Optional[str]) -> Optional[BotStatus]:
        """按 id 计算状态；找不到该机器人时返回 None。"""
        if not bot_id:
            return None
        bot = self.config.get_bot(bot_id)
        if bot is None:
            return None
        return self.bot_status(bot)

    def bot_status_list(self) -> List[BotStatus]:
        """按配置顺序返回所有机器人的状态列表。"""
        return [self.bot_status(bot) for bot in self.config.bots]

    def bot_status_row(self, bot: Bot) -> Tuple[BotStatus, str]:
        """返回 (状态快照, 一行摘要文字)，供列表类界面直接使用。"""
        status = self.bot_status(bot)
        return status, format_bot_status(status)

    def bot_status_tooltip(self, bot: Bot) -> str:
        """生成悬停提示：逐条列出程序及其状态，便于在侧栏/对话框里看细节。"""
        status = self.bot_status(bot)
        lines: List[str] = [
            "{}　{}".format(bot.name, format_bot_status(status)),
            "程序 {} 个（启用 {} 个）".format(status.total, status.enabled_total),
            "",
        ]
        for key in status.program_names:
            role = "主程序" if status.roles.get(key) == ROLE_PRIMARY else "副程序"
            lines.append("  {} · {} · {}".format(
                role,
                format_program_status_line(status, key),
                "运行中" if status.program_states.get(key) == ProcessManager.STATE_RUNNING
                else BOT_STATUS_TEXTS.get(status.program_states.get(key, ""), "未运行"),
            ))
        lines.append("")
        lines.append("窗口：{}".format("已打开" if status.opened else "已关闭"))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 中央区域：左侧竖栏导航 + 右侧实例区（Phase C）
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 浏览器式标签栏（左栏折叠时的切换入口）
    # ------------------------------------------------------------------

    def _safe_sync_bot_tab_bar(self) -> None:
        """同步标签栏 + 可见性，吞掉异常。

        标签栏是"锦上添花"的辅助入口，一旦它抛异常就会顺着调用栈把
        ``open_bot_tab`` 打断 —— 真机后果是**管理器窗口打不开、日志界面进不去**。
        所以这里统一兜底：标签出问题最多是标签不对，窗口必须照常打开。
        """
        try:
            self._sync_bot_tab_bar()
        except (RuntimeError, AttributeError, TypeError, ValueError):
            pass
        try:
            self._update_bot_tab_bar_visible()
        except (RuntimeError, AttributeError, TypeError, ValueError):
            pass

    def _build_bot_tab_bar(self) -> QTabBar:
        """构建实例区顶部的浏览器式标签栏（默认隐藏，左栏折叠时自动出现）。

        为什么需要（真机反馈）：关掉左侧栏之后，切换 bot 实例只剩 Ctrl+Tab，
        没有"点得着"的入口。这里把"添加左侧栏之前"的那种横向标签页还回来。

        行为对齐浏览器：可点选、可关闭（中间键/关闭按钮）、可拖动排序、
        名字太长时省略号。
        """
        bar = QTabBar(self.views_container)
        bar.setObjectName("botTabBar")
        bar.setDocumentMode(True)
        bar.setDrawBase(False)
        bar.setExpanding(False)
        bar.setMovable(True)
        bar.setTabsClosable(True)
        bar.setUsesScrollButtons(True)
        bar.setElideMode(Qt.TextElideMode.ElideRight)
        bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bar.setVisible(False)
        bar.currentChanged.connect(self._on_bot_tab_bar_changed)
        bar.tabCloseRequested.connect(self._on_bot_tab_bar_close_requested)
        # 空白处右键也给一个关闭菜单（与浏览器习惯一致，可选）
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._on_bot_tab_bar_context_menu)
        return bar

    def _sync_bot_tab_bar(self) -> None:
        """让标签栏与"已打开的机器人窗口"保持一致（顺序、文字、当前项）。

        以 ``self.stack`` 的顺序为准（它才是真正的页面顺序），
        标签栏只是它的一个视图，不做独立状态。
        """
        bar = getattr(self, "bot_tab_bar", None)
        stack = getattr(self, "stack", None)
        if bar is None or stack is None:
            return

        ordered = [
            stack.widget(index)
            for index in range(stack.count())
            if isinstance(stack.widget(index), BotTab)
        ]

        previous = bar.blockSignals(True)
        try:
            # 数目或顺序不一致时整体重建（标签数量少，重建成本可忽略）
            same = bar.count() == len(ordered) and all(
                bar.tabData(index) == widget.bot_id
                for index, widget in enumerate(ordered)
            )
            if not same:
                while bar.count():
                    bar.removeTab(0)
                for widget in ordered:
                    bar.addTab(widget.bot_name)
                    bar.setTabData(bar.count() - 1, widget.bot_id)
            else:
                for index, widget in enumerate(ordered):
                    if bar.tabText(index) != widget.bot_name:
                        bar.setTabText(index, widget.bot_name)
                    bar.setTabToolTip(index, self._bot_tab_tooltip(widget))

            current = stack.currentWidget()
            if isinstance(current, BotTab):
                for index in range(bar.count()):
                    if bar.tabData(index) == current.bot_id:
                        bar.setCurrentIndex(index)
                        break
        finally:
            bar.blockSignals(previous)

    def _bot_tab_tooltip(self, tab: "BotTab") -> str:
        """标签的悬停提示：机器人名 + 程序数 + 运行中的程序数。

        状态常量走 `ProcessManager.STATE_*`（本模块没有直接导入 STATE_* 名字，
        `check_names.py` 会抓这种未定义引用）。
        """
        try:
            running = sum(
                1 for key in tab._views
                if self.manager.state(key) in (
                    ProcessManager.STATE_STARTING, ProcessManager.STATE_RUNNING
                )
            )
            total = len(tab._views)
        except (AttributeError, RuntimeError):
            running, total = 0, 0
        return "{}（{} 个程序，{} 个运行中）".format(tab.bot_name, total, running)

    def _on_bot_tab_bar_changed(self, index: int) -> None:
        """点标签 → 切到对应页面（等价于左栏点那个机器人）。"""
        bar = getattr(self, "bot_tab_bar", None)
        stack = getattr(self, "stack", None)
        if bar is None or stack is None or index < 0:
            return
        bot_id = bar.tabData(index)
        if not bot_id:
            return
        tab = self._tabs.get(bot_id)
        if tab is None:
            return
        stack.setCurrentWidget(tab)
        # 与左栏保持一致：高亮落到"这个机器人正在看的程序"
        self._select_nav_for_bot(bot_id)
        self._apply_nav_focus_marker(notify=False)

    def _on_bot_tab_bar_close_requested(self, index: int) -> None:
        """点标签上的关闭按钮 → 与「关闭窗口」同一条路径（会问是否停程序）。"""
        bar = getattr(self, "bot_tab_bar", None)
        if bar is None or index < 0:
            return
        bot_id = bar.tabData(index)
        if bot_id:
            self.close_bot_window(bot_id, confirm=True)

    def _on_bot_tab_bar_context_menu(self, pos) -> None:
        """标签栏空白处右键：关闭其它 / 关闭全部（与浏览器习惯一致）。"""
        bar = getattr(self, "bot_tab_bar", None)
        if bar is None:
            return
        index = bar.tabAt(pos)
        menu = QMenu(self)
        if index >= 0:
            bot_id = bar.tabData(index)
            if bot_id:
                action = menu.addAction("关闭这个窗口")
                action.triggered.connect(
                    lambda _checked=False, bid=bot_id: self.close_bot_window(bid, confirm=True)
                )
                menu.addSeparator()
        others = menu.addAction("关闭其它窗口")
        others.setEnabled(bar.count() > 1)
        others.triggered.connect(self._close_other_bot_tabs)
        all_action = menu.addAction("关闭全部窗口")
        all_action.setEnabled(bar.count() > 0)
        all_action.triggered.connect(lambda: self.close_all_windows(confirm=True))
        menu.exec(bar.mapToGlobal(pos))

    def _close_other_bot_tabs(self) -> None:
        """关闭除当前标签以外的所有机器人窗口。"""
        bar = getattr(self, "bot_tab_bar", None)
        if bar is None:
            return
        keep = bar.tabData(bar.currentIndex())
        for bot_id in list(self._tabs.keys()):
            if bot_id != keep:
                self.close_bot_window(bot_id, confirm=True)

    def _update_bot_tab_bar_visible(self) -> None:
        """决定"标签栏 + Bot 工具条"是否显示。

        规则（真机反馈：折叠左栏后就没有"点得着"的入口了）：
          · **两者都**要求"已经应用过折叠状态"（避免启动还原过程中闪一下）；
          · Bot 工具条（新建/启停/打开全部窗口…）：左栏折叠 或 手动勾选 就显示
            —— 它替代的是左栏那些按钮，不依赖"已经打开过窗口"；
          · 标签栏：还要"有窗口可切"（一个都没有时显示整条空栏没意义）。
        """
        bar = getattr(self, "bot_tab_bar", None)
        if bar is None:
            return
        nav_collapsed = bool(getattr(self, "_nav_collapsed", False))
        forced = bool(getattr(self, "_bot_tab_bar_forced", False))
        ready = bool(getattr(self, "_nav_state_applied", False))

        # Bot 工具条：**始终可用**（与左栏折叠无关）。它是那一排操作按钮，
        # 左栏折叠后尤其离不开；主工具栏里已不再重复这些动作。
        bot_bar = getattr(self, "bot_bar", None)
        if bot_bar is not None and not bot_bar.isVisible():
            bot_bar.setVisible(True)

        # 标签栏：必须有窗口可切才显示（空栏没意义）
        has_windows = bool(getattr(self, "_tabs", None))
        visible = has_windows and ready and (nav_collapsed or forced)
        bar.setVisible(visible)

    def toggle_bot_tab_bar(self, visible: Optional[bool] = None) -> bool:
        """手动显示/隐藏标签栏（返回最终是否显示）。

        自动规则（左栏折叠时出现）依然生效；这个手动开关用于"左栏展开时也想
        用标签栏切换"的场景 —— 属于 setChecked/triggered 混用时常见的坑：
        `setChecked(True)` 不会发 `triggered`，所以这里手动调用而不是依赖信号。
        """
        current = bool(getattr(self, "_bot_tab_bar_forced", False))
        target = (not current) if visible is None else bool(visible)
        self._bot_tab_bar_forced = target
        self._settings.setValue(SETTINGS_BOT_TAB_BAR, target)
        action = getattr(self, "action_toggle_bot_tab_bar", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(target)
            action.blockSignals(False)
        self._update_bot_tab_bar_visible()
        return target

    def set_force_stop_on_close(self, enabled: bool, persist: bool = True) -> None:
        """设置"关闭管理器时强制停止所有程序"（菜单与运行参数两处共用）。"""
        self._force_stop_on_close = bool(enabled)
        action = getattr(self, "action_force_stop_on_close", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(self._force_stop_on_close)
            action.blockSignals(False)
        if persist:
            try:
                self._settings.setValue(
                    SETTINGS_FORCE_STOP_ON_CLOSE, self._force_stop_on_close
                )
                self._settings.sync()
            except (AttributeError, TypeError):
                pass

    def _on_force_stop_on_close_toggled(self, checked: bool) -> None:
        """菜单勾选变化 → 记住设置并提示当前状态。"""
        self.set_force_stop_on_close(checked)
        self.statusBar().showMessage(
            "关闭管理器时将{}所有程序".format(
                "强制停止" if checked else "先询问再停止"
            ),
            4000,
        )

    def _on_toggle_bot_tab_bar_action(self, checked: bool) -> None:
        """菜单项触发：按勾选状态显示/隐藏标签栏。"""
        self.toggle_bot_tab_bar(checked)

    def _build_central(self) -> None:
        """中央区域 = 水平 QSplitter(左导航栏, 右实例区)。

        左侧竖栏（模仿"竖着的 bot 标签页"）：
            顶部按钮行 -> bot/程序两级树 -> 底部按钮行 -> 提示行
        右侧实例区（顶部浏览器式标签栏 + QStackedWidget）：
            标签栏只在需要时显示（见 ``_update_bot_tab_bar_visible``）——
            左栏一折叠，它立刻出现，这样不用 Ctrl+Tab 也能点着切机器人。
            QStackedWidget 的 0 号是"空状态占位页"，之后每个已打开的机器人一页。
        """
        self.central_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.central_splitter.setObjectName("centralSplitter")
        self.central_splitter.setChildrenCollapsible(False)
        self.central_splitter.setHandleWidth(5)
        self.central_splitter.setOpaqueResize(True)

        self.nav_panel = self._build_nav()
        self.central_splitter.addWidget(self.nav_panel)

        # 实例区容器：上面一条浏览器式标签栏，下面才是页面栈。
        self.views_container = QWidget(self.central_splitter)
        self.views_container.setObjectName("viewsContainer")
        views_layout = QVBoxLayout(self.views_container)
        views_layout.setContentsMargins(0, 0, 0, 0)
        views_layout.setSpacing(0)

        self.bot_tab_bar = self._build_bot_tab_bar()
        views_layout.addWidget(self.bot_tab_bar)

        self.stack = QStackedWidget(self.views_container)
        self.stack.setObjectName("botViews")
        self.stack.currentChanged.connect(self._on_current_page_changed)
        self.placeholder_page = self._build_placeholder_page()
        self.stack.addWidget(self.placeholder_page)
        views_layout.addWidget(self.stack, 1)

        self.central_splitter.addWidget(self.views_container)

        self.central_splitter.setStretchFactor(0, 0)
        self.central_splitter.setStretchFactor(1, 1)
        self.central_splitter.setSizes([DEFAULT_NAV_WIDTH, 900])
        self.setCentralWidget(self.central_splitter)

        # 恢复上次的宽度比例（QSettings）；折叠状态在列表构建完之后再应用
        saved = self._settings.value(SETTINGS_NAV_SPLIT)
        if saved:
            try:
                sizes = [int(value) for value in saved]
                if len(sizes) == 2 and sizes[0] > 0 and sizes[1] > 0:
                    self.central_splitter.setSizes(sizes)
            except (TypeError, ValueError):
                pass
        self._refresh_nav()
        # N5：左侧栏状态（折叠/展开 + 宽度）统一按"默认展开、跟随上次记录"还原。
        # 只在这里设一次不够 —— 后面 _restore_settings()/show()/_restore_open_tabs()
        # 都会重新布局，所以再排几次延迟复算（幂等）。
        self._restore_nav_state()
        self._schedule_nav_restore()

    def _build_nav(self) -> QWidget:
        """左侧竖栏：按钮行 + 机器人/程序两级树 + 底部按钮 + 提示。"""
        panel = QWidget(self.central_splitter)
        panel.setObjectName("navPanel")
        #  先用常量兜底；真正的下限在 _nav_min_width()（按顶部两个按钮算），
        #  由 _restore_nav_state() 应用（真机 2026-10-06：按钮不能左右被遮）
        panel.setMinimumWidth(MIN_NAV_WIDTH)
        panel.setStyleSheet(self._nav_style_sheet())
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ---- 顶部按钮行（与简图一致）----
        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        self.nav_list_button = QPushButton("查看已有 bot", panel)
        self.nav_list_button.setToolTip("打开「查看已有 bot」对话框（Ctrl+B），管理全部机器人")
        self.nav_list_button.clicked.connect(self.open_bot_list_dialog)

        self.nav_new_button = QPushButton("编辑或新建 bot", panel)
        self.nav_new_button.setToolTip("新建机器人；选中某个机器人时可直接编辑它")
        self.nav_new_button.clicked.connect(self._on_nav_new_or_edit)

        top_row.addWidget(self.nav_list_button)
        top_row.addWidget(self.nav_new_button)
        layout.addLayout(top_row)
        #  真机要求（2026-10-06）："左侧栏的两个按钮至少能完全显示" ——
        #  按两个按钮的**实际需要宽度**算出左栏的最小宽度（按钮并排 + 间距 + 边距），
        #  和 MIN_NAV_WIDTH 取较大者交给面板：分隔条再也压不到"按钮被裁"的程度，
        #  窗口不够宽时会被自动撑到这个下限（高度不变）。
        try:
            #  真机 2026-10-06："【编辑和新建 bot】左右两边都有一点被遮住了" ——
            #  之前只算了按钮本身的宽度，漏掉了**布局边距**与按钮内边距，
            #  所以左右各少一点点。这里把面板边距、行间距、按钮自身的内边距
            #  全都算进去，再留 24px 余量。
            margins = layout.contentsMargins()
            buttons_need = (self.nav_list_button.sizeHint().width()
                            + self.nav_new_button.sizeHint().width()
                            + top_row.spacing()
                            + margins.left() + margins.right()
                            + 24)
        except (RuntimeError, AttributeError, TypeError, ValueError):
            buttons_need = 0
        panel.setProperty("navButtonsWidth", int(buttons_need))

        # ---- 机器人 / 程序两级树 ----
        self.nav_tree = QTreeWidget(panel)
        self.nav_tree.setObjectName("navTree")
        self.nav_tree.setHeaderHidden(True)
        self.nav_tree.setColumnCount(1)
        self.nav_tree.setIndentation(14)
        self.nav_tree.setRootIsDecorated(True)
        self.nav_tree.setUniformRowHeights(True)
        self.nav_tree.setAnimated(False)
        self.nav_tree.setExpandsOnDoubleClick(False)
        self.nav_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        #  真机事故（2026-10-05）：左栏被拖窄之后，列表里带长名字的条目**横向滚动**了，
        #  看上去只剩每行的尾巴（"……序 · 未运行"），底部按钮也被切。这里关掉横向滚动，
        #  超长名字改用右侧省略号显示 —— 条目永远从左边开始，看得懂。
        self.nav_tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.nav_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.nav_tree.customContextMenuRequested.connect(self._on_nav_context_menu)
        self.nav_tree.itemSelectionChanged.connect(self._on_nav_selection_changed)
        # 步骤 3：左键单击任意条目即打开对应窗口（Ctrl/Shift 单击只选中不打开）
        self.nav_tree.currentItemChanged.connect(self._on_nav_current_changed)
        self.nav_tree.itemDoubleClicked.connect(self._on_nav_double_clicked)
        self.nav_tree.itemExpanded.connect(lambda _item: self._save_nav_expanded())
        self.nav_tree.itemCollapsed.connect(lambda _item: self._save_nav_expanded())
        layout.addWidget(self.nav_tree, 1)

        # ---- 底部按钮行（作用于选中项）----
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(4)
        self.nav_open_button = QPushButton("打开", panel)
        self.nav_open_button.setToolTip("打开选中机器人的窗口")
        self.nav_open_button.clicked.connect(lambda: self._nav_action("open"))

        self.nav_stop_button = QPushButton("停止", panel)
        self.nav_stop_button.setToolTip("停止选中机器人正在运行的程序")
        self.nav_stop_button.clicked.connect(lambda: self._nav_action("stop"))

        #  标签只用两个字：三个按钮并排时，"关闭窗口"四个字会把左栏的**最小宽度**
        #  撑到 230px 左右 —— 于是左栏拖不窄，被切的时候按钮也跟着缺一截
        #  （真机 2026-10-05："左侧栏无法调整宽度，然后出现显示错误"）。完整含义放提示里。
        self.nav_close_button = QPushButton("关闭", panel)
        self.nav_close_button.setToolTip("关闭选中机器人的窗口（程序继续在后台运行）")
        self.nav_close_button.clicked.connect(lambda: self._nav_action("close_window"))

        bottom_row.addWidget(self.nav_open_button)
        bottom_row.addWidget(self.nav_stop_button)
        bottom_row.addWidget(self.nav_close_button)
        # 允许被压窄：不给按钮设最小宽度，QHBoxLayout 才不会把面板顶住
        for button in (self.nav_open_button, self.nav_stop_button, self.nav_close_button):
            button.setMinimumWidth(0)
        layout.addLayout(bottom_row)

        # ---- 提示行 ----
        self.nav_hint_label = QLabel("单击机器人打开窗口", panel)
        self.nav_hint_label.setStyleSheet(theme_tokens.dialog_hint_qss(panel))
        self.nav_hint_label.setWordWrap(True)
        layout.addWidget(self.nav_hint_label)
        return panel

    def _build_placeholder_page(self) -> QWidget:
        """右侧实例区的空状态页：没有任何窗口打开时显示。"""
        page = QWidget(self)
        page.setObjectName("placeholderPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addStretch(1)

        title = QLabel("还没有打开的机器人窗口", page)
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        hint = QLabel(
            "在左侧列表里单击一个机器人即可打开它的窗口；\n"
            "也可以点「查看已有 bot」查看全部实例与运行状态（Ctrl+B）。",
            page,
        )
        hint.setObjectName("placeholderHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: {};".format(self._muted_color()))
        self.placeholder_hint_label = hint
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.placeholder_new_button = QPushButton("新建 bot…", page)
        self.placeholder_new_button.clicked.connect(self.new_bot)
        self.placeholder_list_button = QPushButton("查看已有 bot…", page)
        self.placeholder_list_button.clicked.connect(self.open_bot_list_dialog)
        self.placeholder_all_button = QPushButton("打开全部窗口", page)
        self.placeholder_all_button.clicked.connect(self.open_all_windows)
        buttons.addWidget(self.placeholder_new_button)
        buttons.addWidget(self.placeholder_list_button)
        buttons.addWidget(self.placeholder_all_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(2)
        return page

    # ------------------------------------------------------------------
    # 左侧导航栏：刷新与交互
    # ------------------------------------------------------------------

    def _refresh_nav(self) -> None:
        """按配置重建左侧列表：每个机器人一个节点，下面挂程序状态。

        修 1：重建会 ``tree.clear()`` 掉所有行，因此**先记下当前高亮的是哪一行**，
        重建完立刻恢复（程序行优先，其次机器人行）。否则任何一次全量重建
        （例如状态去抖触发的刷新）都会把用户刚点出来的高亮抹掉 ——
        这正是"单击程序行后高亮又跳回去/消失"的根因。
        """
        tree = getattr(self, "nav_tree", None)
        if tree is None:
            return

        # 重建前：记下当前高亮行与应有的高亮行
        previous_program = self._nav_active_program_key or self._nav_selected_program_key()
        previous_bot = (
            self._nav_active_bot_id or self._nav_selected_bot_id() or self._current_bot_id()
        )
        self._nav_rebuild_count += 1
        self._nav_debug_log(
            "refresh_nav 开始：上次高亮 program={!r} bot={!r}".format(
                previous_program, previous_bot
            )
        )

        self._nav_guard = True
        try:
            tree.blockSignals(True)
            expanded = set(self._nav_expanded_state())
            tree.clear()
            self._nav_items = {}

            focused_key = self._nav_focus_marker_key()
            opened_ids = set(self._tabs.keys())

            for status in self.bot_status_list():
                opened = status.bot_id in opened_ids
                bot_item = QTreeWidgetItem(tree)
                bot_item.setData(0, ROLE_NAV_BOT_ID, status.bot_id)
                bot_item.setData(0, ROLE_NAV_PROGRAM_KEY, "")
                bot_item.setData(0, ROLE_NAV_FOCUSED, False)
                bot_item.setData(0, ROLE_NAV_OPENED, opened)
                bot_item.setText(0, self._nav_bot_text(status))
                bot_item.setToolTip(0, self._nav_bot_tooltip(status))
                font = QFont(tree.font())
                font.setBold(True)
                bot_item.setFont(0, font)
                color = bot_status_color(status)
                if color:
                    bot_item.setForeground(0, QBrush(QColor(color)))
                self._nav_items[status.bot_id] = bot_item

                for key in self._nav_program_keys(status):
                    child = QTreeWidgetItem(bot_item)
                    is_focused = bool(focused_key) and key == focused_key
                    child.setData(0, ROLE_NAV_BOT_ID, status.bot_id)
                    child.setData(0, ROLE_NAV_PROGRAM_KEY, key)
                    child.setData(0, ROLE_NAV_FOCUSED, is_focused)
                    child.setData(0, ROLE_NAV_OPENED, opened)
                    child.setText(0, self._nav_program_text(status, key))
                    child.setToolTip(0, self._nav_program_tooltip(status, key))
                    self._apply_nav_item_roles(child, status, key, is_focused, opened)

                # 默认：窗口已打开的展开；否则沿用上次状态
                item_expanded = status.bot_id in expanded or status.opened
                bot_item.setExpanded(item_expanded)
                if not item_expanded:
                    bot_item.setChildIndicatorPolicy(
                        QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
                    )

            # 修 1：恢复高亮行（这才是"重建后选中项应当是什么"的唯一判据）
            self._restore_nav_selection(previous_bot, previous_program)
        finally:
            tree.blockSignals(False)
            self._nav_guard = False

        if not self._nav_selected_program_key() and not self._nav_selected_bot_id():
            self._nav_rebuild_miss_count += 1
            # 出错时强制记一次日志（不依赖 --nav-debug），便于事后排查
            previous_force = getattr(self, "_nav_debug_force", False)
            self._nav_debug_force = True
            try:
                self._nav_debug_log(
                    "refresh_nav 结束：重建后没有任何选中项"
                    "（rebuild #{}, 目标 bot={!r} program={!r}）".format(
                        self._nav_rebuild_count, previous_bot, previous_program
                    )
                )
            finally:
                self._nav_debug_force = previous_force
        else:
            self._nav_debug_log(
                "refresh_nav 结束：program={!r} bot={!r}".format(
                    self._nav_selected_program_key(), self._nav_selected_bot_id()
                )
            )

        self._update_nav_buttons()
        self._update_nav_hint()

    def _restore_nav_selection(self, bot_id: str, program_key: str) -> bool:
        """把高亮恢复到指定行：程序行优先，其次机器人行（修 1）。

        必须在 ``tree.blockSignals(True)`` 期间调用，避免恢复动作又触发一遍
        打开窗口的逻辑。
        """
        self._nav_active_program_key = ""
        self._nav_active_bot_id = ""
        if not bot_id:
            # 没有目标机器人：至少把"当前窗口"的那一行选出来
            current = self._current_bot_id()
            if current and current in self._nav_items:
                return self._restore_nav_selection(current, "")
            return False

        item = self._nav_items.get(bot_id)
        if item is None:
            return False

        target = None
        if program_key:
            target = self._nav_child_item(item, program_key)
            if target is None and program_key != self._current_focused_key():
                # 记的是旧窗口的程序：换成这个机器人当前的焦点程序行
                target = self._nav_child_item(item, self._nav_program_for_bot(bot_id))
        if target is None:
            # 退一步：至少落到主程序行（第一个程序行），保证"重建 != 丢失高亮"
            keys = [
                str(item.child(index).data(0, ROLE_NAV_PROGRAM_KEY) or "")
                for index in range(item.childCount())
            ]
            keys = [key for key in keys if key]
            if keys:
                target = self._nav_child_item(item, keys[0])

        tree = getattr(self, "nav_tree", None)
        try:
            item.setExpanded(True)
            if target is not None:
                target.setSelected(True)
                if tree is not None:
                    tree.setCurrentItem(
                        target, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect
                    )
                self._nav_active_program_key = str(
                    target.data(0, ROLE_NAV_PROGRAM_KEY) or ""
                )
                self._nav_active_bot_id = bot_id
                return True
            item.setSelected(True)
            if tree is not None:
                tree.setCurrentItem(
                    item, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect
                )
            self._nav_active_bot_id = bot_id
            return False
        except (RuntimeError, AttributeError):
            return False

    def _nav_program_keys(self, status: BotStatus) -> List[str]:
        """左侧程序节点顺序：★主程序在最前，其余按配置顺序。"""
        keys = list(status.program_names.keys())
        primary = [key for key in keys if status.roles.get(key) == ROLE_PRIMARY]
        rest = [key for key in keys if key not in primary]
        return primary + rest

    def _nav_bot_text(self, status: BotStatus) -> str:
        """机器人节点文字，例如 "▌ ● ATRI ［2/3］"（▌ 表示窗口已打开）。"""
        dot = bot_status_dot(status)
        name = status.name or status.bot_id
        opened = self._tabs.get(status.bot_id) is not None
        prefix = nav_bot_prefix(opened)
        if not status.total:
            return "{}{} {} ［无程序］".format(prefix, dot, name)
        return "{}{} {} ［{}］".format(prefix, dot, name, status.counter_text)

    def _nav_program_text(
        self, status: BotStatus, key: str, focused: Optional[bool] = None
    ) -> str:
        """程序节点文字，例如 "▸ ★ 主程序 · 运行中"（▸ 表示当前正在显示的程序）。

        focused 为 None 时按"全局唯一 ▸"规则现算（整个左栏只会有一行带 ▸）。
        """
        name = status.program_names.get(key, key)
        state = status.program_states.get(key, "")
        state_text = BOT_STATUS_TEXTS.get(state, "未运行")
        marker = "★ " if status.roles.get(key) == ROLE_PRIMARY else ""
        if focused is None:
            focused = bool(self._nav_focus_marker_key()) and key == self._nav_focus_marker_key()
        return "{}{}{} · {}".format(nav_program_prefix(focused), marker, name, state_text)

    def _nav_program_tooltip(self, status: BotStatus, key: str) -> str:
        """程序条目提示：状态 + 角色 + 哪个窗口正在看它。"""
        program = self._programs_of_bot(status.bot_id).get(key)
        lines = [
            format_program_status_line(status, key),
            "角色：{}".format("主程序" if status.roles.get(key) == ROLE_PRIMARY else "副程序"),
        ]
        if program is not None and program.command.strip():
            lines.append("命令：{}".format(program.command.strip()))
        marker = self._nav_focus_marker_key()
        if marker and key == marker:
            lines.append("← 当前正在显示这个程序")
        lines.append("单击＝切到它　右键＝更多操作")
        return "\n".join(lines)

    def _programs_of_bot(self, bot_id: str) -> Dict[str, Program]:
        """bot_id -> {manager key: Program}（来自配置，供提示文本用）。"""
        bot = self.config.get_bot(bot_id)
        if bot is None:
            return {}
        return {build_manager_key(bot.id, program.id): program for program in bot.programs}

    def _current_focused_key(self) -> str:
        """当前窗口正在看的程序 key（没有窗口时为空串）。"""
        tab = self.current_tab()
        if tab is None:
            return ""
        try:
            return tab.focused_program()
        except (RuntimeError, AttributeError):
            return ""

    def _apply_nav_item_roles(
        self,
        item: QTreeWidgetItem,
        status: BotStatus,
        key: str,
        focused: bool,
        opened: bool,
    ) -> None:
        """把一个程序条目的三态视觉刷上去（前缀 / 字重 / 状态色）。"""
        item.setText(0, self._nav_program_text(status, key, focused=focused))
        item.setData(0, ROLE_NAV_FOCUSED, bool(focused))
        item.setData(0, ROLE_NAV_OPENED, bool(opened))
        tree = getattr(self, "nav_tree", None)
        base_font = QFont(tree.font()) if tree is not None else QFont(item.font(0))
        base_font.setBold(bool(focused))
        item.setFont(0, base_font)
        item.setForeground(
            0, QBrush(QColor(format_program_status_color(status, key)))
        )

    # ------------------------------------------------------------------
    # 焦点标记 ▸（R1-2 起；确认 1+2 之后改为"全局唯一，且绑定当前窗口显示的程序"）
    #
    #   规则：整个左栏任何时刻最多一个 ▸；它所在的程序行 =
    #         ① 当前窗口正在显示的程序（有窗口时）
    #         ② 否则 = 当前高亮行所在的程序（Y：标记跟着高亮走）
    # ------------------------------------------------------------------

    def _nav_focus_marker_key(self) -> str:
        """应当带 ▸ 标记的程序 key（全局唯一）。"""
        tab = self.current_tab()
        if tab is not None:
            try:
                shown = tab.focused_program()
            except (RuntimeError, AttributeError):
                shown = ""
            if shown and tab.log_view(shown) is not None:
                return shown
        # 没有打开窗口（或窗口还没定焦点）：跟当前高亮行走
        return self._nav_selected_program_key()

    def _nav_focus_marker_bot_id(self, program_key: str) -> str:
        """▸ 所在程序行属于哪个机器人（找不到返回空串）。"""
        if not program_key:
            return ""
        for bot_id, item in getattr(self, "_nav_items", {}).items():
            if self._nav_child_item(item, program_key) is not None:
                return bot_id
        return ""

    def _apply_nav_focus_marker(self, notify: bool = True) -> bool:
        """按"全局唯一"规则刷新 ▸ 标记，返回是否有变化。

        - 有打开窗口时：标记 = 当前窗口正在显示的程序
        - 没有窗口时：标记 = 当前高亮行（Y：标记跟着高亮）
        - 先把所有机器人、所有程序行的标记清掉，只留命中的那一行
        """
        marker = self._nav_focus_marker_key()
        changed = False
        tree = getattr(self, "nav_tree", None)
        try:
            if tree is not None:
                tree.blockSignals(True)
            for bot_id, item in list(getattr(self, "_nav_items", {}).items()):
                status = self.bot_status_of(bot_id)
                if status is None:
                    continue
                opened = bot_id in self._tabs
                for index in range(item.childCount()):
                    child = item.child(index)
                    key = str(child.data(0, ROLE_NAV_PROGRAM_KEY) or "")
                    if not key:
                        continue
                    focused = bool(marker) and key == marker
                    if bool(child.data(0, ROLE_NAV_FOCUSED)) == focused:
                        continue
                    self._apply_nav_item_roles(child, status, key, focused, opened)
                    changed = True
        except (RuntimeError, AttributeError):
            return False
        finally:
            if tree is not None:
                try:
                    tree.blockSignals(False)
                except RuntimeError:
                    pass
        if changed:
            self._nav_debug_log("focus 标记 -> {!r}".format(marker))
            if notify:
                self._update_nav_hint()
        return changed

    def _mark_nav_focus(
        self, bot_id: str = "", program_key: str = "", notify: bool = True
    ) -> bool:
        """刷新 ▸ 标记（保留旧签名：bot_id 只用于日志，标记本身是全局唯一的）。"""
        if bot_id:
            self._nav_debug_log(
                "mark_nav_focus(bot={!r}, program={!r})".format(bot_id, program_key)
            )
        return self._apply_nav_focus_marker(notify=notify)

    def _nav_bot_tooltip(self, status: BotStatus) -> str:
        """机器人节点提示（含每个程序的状态）。"""
        lines = [format_bot_status(status, with_opened=True), ""]
        for key in self._nav_program_keys(status):
            lines.append("  {}".format(format_program_status_line(status, key)))
        lines.append("")
        lines.append("单击＝打开/切到这个机器人　右键＝更多操作")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 导航时序诊断（修 1 起：--nav-debug 或"重建后没有选中项"时启用）
    # ------------------------------------------------------------------

    def set_nav_debug(self, enabled: bool) -> None:
        """打开/关闭导航时序诊断日志。"""
        self._nav_debug = bool(enabled)
        if self._nav_debug:
            self._nav_debug_log("=== 导航诊断开始 ===")

    def _nav_debug_log(self, message: str) -> None:
        """记录一条导航时序日志。

        默认只在出错（重建后没有选中项）时写，避免日常运行产生噪音；
        用 ``--nav-debug`` 可以打开完整时序，用于排查"高亮被抹掉"这类竞态。
        """
        if not (self._nav_debug or getattr(self, "_nav_debug_force", False)):
            return
        try:
            from datetime import datetime

            line = "{}  {}".format(
                datetime.now().strftime("%H:%M:%S.%f")[:-3], message
            )
        except (ImportError, ValueError):
            line = str(message)
        self._nav_debug_lines.append(line)
        if len(self._nav_debug_lines) > 400:
            del self._nav_debug_lines[:200]
        try:
            log_path = Path(PROJECT_ROOT) / "nav_debug.log"
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except (OSError, TypeError):
            pass

    def nav_debug_dump(self) -> str:
        """返回已记录的导航时序日志（自检用）。"""
        return "\n".join(self._nav_debug_lines)

    def nav_diagnostics(self) -> Dict[str, int]:
        """导航健壮性计数（自检用）：全量重建次数、重建后丢选中项的次数。"""
        return {
            "rebuild_count": int(getattr(self, "_nav_rebuild_count", 0)),
            "rebuild_miss_count": int(getattr(self, "_nav_rebuild_miss_count", 0)),
        }

    def _nav_expanded_state(self) -> List[str]:
        """上次记录为展开的机器人 id 列表。"""
        raw = self._settings.value(SETTINGS_NAV_EXPANDED, [])
        if isinstance(raw, str):
            return [item for item in raw.split(",") if item]
        if isinstance(raw, (list, tuple)):
            return [str(item) for item in raw]
        return []

    def _save_nav_expanded(self) -> None:
        """记录展开状态。"""
        if self._nav_guard:
            return
        try:
            expanded = [
                bot_id for bot_id, item in self._nav_items.items() if item.isExpanded()
            ]
        except RuntimeError:
            return
        self._settings.setValue(SETTINGS_NAV_EXPANDED, expanded)

    def _nav_item_bot_id(self, item: Optional[QTreeWidgetItem]) -> str:
        if item is None:
            return ""
        return str(item.data(0, ROLE_NAV_BOT_ID) or "")

    def _nav_item_program_key(self, item: Optional[QTreeWidgetItem]) -> str:
        if item is None:
            return ""
        return str(item.data(0, ROLE_NAV_PROGRAM_KEY) or "")

    def _nav_selected_bot_id(self) -> str:
        """当前选中的机器人 id（程序节点会归到它所属的机器人）。"""
        tree = getattr(self, "nav_tree", None)
        if tree is None:
            return ""
        items = tree.selectedItems()
        if not items:
            return ""
        return self._nav_item_bot_id(items[0])

    def _nav_selected_program_key(self) -> str:
        """当前选中的程序 key（选中的是机器人行时返回空串）。"""
        tree = getattr(self, "nav_tree", None)
        if tree is None:
            return ""
        items = tree.selectedItems()
        if not items:
            return ""
        return self._nav_item_program_key(items[0])

    def _on_nav_selection_changed(self) -> None:
        """选中项变化：只更新底部按钮与提示（打开动作由 _on_nav_current_changed 负责）。"""
        if self._nav_guard:
            return
        self._update_nav_buttons()
        self._update_nav_hint()

    def _nav_modifier_held(self) -> bool:
        """是否按住了 Ctrl/Shift（按住时单击只选中、不打开窗口）。"""
        try:
            modifiers = QApplication.keyboardModifiers()
        except (AttributeError, RuntimeError):
            return False
        return bool(
            modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        )

    def _on_nav_current_changed(
        self, current: Optional[QTreeWidgetItem], _previous: Optional[QTreeWidgetItem] = None
    ) -> None:
        """左键单击左侧条目：打开对应窗口并把焦点落到对应程序。

        - 程序条目（如"Ollama 服务"）-> 打开窗口并聚焦该程序的窗格/标签
        - 机器人条目（如"ATRI ［0/3］"）-> 打开窗口并聚焦主程序窗格
        - 按住 Ctrl/Shift -> 只选中，不打开窗口（方便浏览列表）

        **必须延后到鼠标事件处理完成之后再落位**（2026-10 真机 bug）：
        `currentItemChanged` 是在鼠标事件处理**中途**发出的，我们在那一刻调用
        `setSelected()` / `setCurrentItem()` 设好的高亮，会被 Qt 在事件收尾时
        按"被点的那一行"重新覆盖 —— 真机表现就是"点机器人行后高亮仍停在机器人行"，
        而 currentItem 已经是程序行（内部状态对、画面不对）。
        用 `QTimer.singleShot(0, ...)` 把动作排到事件处理之后即可。
        （这也解释了为什么双击与直接调用函数都正常：它们不在鼠标事件中途。）
        """
        if self._nav_guard or current is None:
            return
        self._update_nav_buttons()
        self._update_nav_hint()
        if self._nav_modifier_held():
            self.statusBar().showMessage(
                "已选中（按住 Ctrl/Shift 单击只选中，不打开窗口）", 2500
            )
            return

        # 记下这次要打开的条目：延后执行时若用户又点了别的行，就放弃这次
        target_bot = self._nav_item_bot_id(current)
        target_program = self._nav_item_program_key(current)

        def _deferred() -> None:
            # 期间又发生了一次点击/或列表被重建 -> 交给那次处理
            if self._nav_guard:
                return
            tree = getattr(self, "nav_tree", None)
            if tree is not None:
                node = tree.currentItem()
                if node is not None:
                    now_bot = self._nav_item_bot_id(node)
                    now_program = self._nav_item_program_key(node)
                    if (now_bot, now_program) != (target_bot, target_program):
                        return
            self.open_nav_item(current)

        QTimer.singleShot(0, _deferred)

    def open_nav_item(self, item: Optional[QTreeWidgetItem], scroll: bool = True) -> bool:
        """打开左栏条目对应的窗口（程序条目聚焦该程序，机器人条目聚焦主程序）。

        R1-1：两种情况下橙色高亮都会落到"当前正在看的程序行"（机器人条目＝主程序行）。
        """
        bot_id = self._nav_item_bot_id(item)
        if not bot_id:
            return False
        program_key = self._nav_item_program_key(item)
        if program_key:
            return self.show_program(program_key, scroll=scroll)
        tab = self.show_bot_view(bot_id, focus=True, focus_first=True)
        return tab is not None

    def show_program(self, program_key: str, scroll: bool = True) -> bool:
        """打开某程序所在机器人的窗口，并把焦点切到该程序的窗格/标签。"""
        bot_id, _program = self._resolve_key(program_key)
        if not bot_id:
            return False
        tab = self.show_bot_view(bot_id, focus=True)
        if tab is None:
            return False
        ok = tab.focus_program(program_key, scroll=scroll)
        if ok:
            # 让选中项停在被点的程序上（而不是跳回机器人行）
            self._select_nav_bot(bot_id, program_key=program_key)
            # R1-2：▸ 焦点标记同步（选中与焦点是两个独立信息）
            self._mark_nav_focus(bot_id, program_key, notify=False)
            program = tab.program_of(program_key)
            name = program.name if program is not None else program_key
            self.statusBar().showMessage("已切换到「{}」".format(name), 3000)
        return ok

    def _on_nav_double_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        """双击 = **只展开/折叠该节点**，不再打开窗口（2026-10 定稿）。

        背景：早期版本是"双击打开窗口"，后来单击也接上了打开逻辑，于是双击变成
        "打开两次"的冗余行为（幂等所以无害，但看起来像 bug）。现在职责单一：

          · 单击条目      -> 打开窗口 + 聚焦对应程序 + 高亮落位（`_on_nav_current_changed`）
          · 双击条目      -> 展开 / 折叠该机器人节点（只有机器人行有子项）
          · Ctrl/Shift+单击 -> 只选中，不打开

        注意 `setExpandsOnDoubleClick(False)` 已经关掉了 Qt 的默认双击展开，
        所以这里显式切换，行为完全由我们控制。
        """
        if self._nav_guard or item is None:
            return
        if item.childCount() <= 0:
            # 程序行没有子项：双击不做事（避免"双击程序行把窗口关掉"这类误操作）
            return
        item.setExpanded(not item.isExpanded())

    def _nav_action(self, action: str) -> None:
        """左侧底部按钮：作用于选中的机器人（没选中则提示）。"""
        bot_id = self._nav_selected_bot_id() or self._current_bot_id()
        if not bot_id:
            self.statusBar().showMessage("请先在左侧列表里选中一个机器人。", 4000)
            return
        if action == "open":
            # R1-1：左栏底部「打开」也把高亮落到主程序行
            self.show_bot_view(bot_id, focus=True, focus_first=True)
        elif action == "stop":
            self.stop_bot(bot_id)
        elif action == "close_window":
            self.close_bot_window(bot_id, confirm=False)
        elif action == "restart":
            self.restart_bot(bot_id)
        elif action == "start":
            self.start_bot(bot_id)
        self._update_nav_buttons()

    def _on_nav_new_or_edit(self) -> None:
        """「编辑或新建 bot」：选中了某个机器人就编辑它，否则新建。"""
        bot_id = self._nav_selected_bot_id()
        if bot_id and self.config.get_bot(bot_id) is not None:
            self.edit_bot(bot_id)
        else:
            self.new_bot()

    def _update_nav_buttons(self) -> None:
        """按选中项启用/禁用左侧底部按钮。"""
        if getattr(self, "nav_open_button", None) is None:
            return
        bot_id = self._nav_selected_bot_id() or self._current_bot_id()
        has_bot = bool(bot_id) and self.config.get_bot(bot_id) is not None
        opened = bool(bot_id) and bot_id in self._tabs
        running = self._nav_selected_running(bot_id)
        self.nav_open_button.setEnabled(has_bot and not opened)
        self.nav_stop_button.setEnabled(running > 0)
        self.nav_close_button.setEnabled(opened)

    def _nav_selected_running(self, bot_id: str) -> int:
        """选中机器人当前运行中的程序数。"""
        if not bot_id:
            return 0
        status = self.bot_status_of(bot_id)
        return status.running if status is not None else 0

    def _update_nav_hint(self) -> None:
        """左栏底部提示：选中机器人的状态 + 三态符号说明（R1-2）。"""
        if getattr(self, "nav_hint_label", None) is None:
            return
        legend = "{}＝正在看　{}＝窗口已打开".format(NAV_FOCUS_MARK, NAV_OPEN_MARK)
        bot_id = self._nav_selected_bot_id()
        status = self.bot_status_of(bot_id) if bot_id else None
        if status is None:
            self.nav_hint_label.setText(
                "共 {} 个机器人；单击即打开窗口　|　{}".format(
                    len(self.config.bots), legend
                )
            )
            self.nav_hint_label.setToolTip(
                "单击机器人＝打开窗口并把焦点放到主程序\n"
                "单击程序＝切到该程序的日志窗格\n"
                "Ctrl/Shift+单击＝只选中不打开\n"
                "{}＝当前正在看的程序　{}＝该机器人窗口已打开".format(
                    NAV_FOCUS_MARK, NAV_OPEN_MARK
                )
            )
            return
        self.nav_hint_label.setText(
            "{}　|　{}　|　{}".format(
                format_bot_status(status, with_opened=False),
                "窗口已打开" if status.opened else "单击可打开窗口",
                legend,
            )
        )
        self.nav_hint_label.setToolTip(self._nav_bot_tooltip(status))

    def _saved_nav_width(self, fallback: int = DEFAULT_NAV_WIDTH) -> int:
        """上次记忆的左栏宽度（已按 MIN/MAX 夹住；不可用时用 fallback）。

        N5：``nav/split`` 里存的可能是"折叠中的 [0, 宽度]"，直接把 0 拿来用会让
        左栏下次启动后"看起来还在、其实只有一条缝"——所以这里做夹取。
        """
        try:
            saved = self._settings.value(SETTINGS_NAV_SPLIT, None)
            if saved:
                width = int(list(saved)[0])
                if width >= MIN_NAV_WIDTH:
                    return min(max(width, MIN_NAV_WIDTH), MAX_NAV_WIDTH)
        except (TypeError, ValueError, IndexError):
            pass
        return max(MIN_NAV_WIDTH, min(int(fallback), MAX_NAV_WIDTH))

    def _restore_nav_state(self, visible: Optional[bool] = None) -> bool:
        """按 QSettings 还原左栏的"折叠 / 展开 + 宽度"（N5）。

        规则：
          · **默认展开**（没有记录时展开，宽度用默认值）；
          · 上次是折叠的就还原成折叠，并把动作文案同步好；
          · 展开时按记忆宽度恢复，且宽度一定落在 [MIN_NAV_WIDTH, MAX_NAV_WIDTH]。

        本方法是**幂等**的，而且会被延迟调用若干次（见 `_schedule_nav_restore`）：
        构造期间还有 `_restore_settings()`（还原窗口几何）、`show()`、
        `_restore_open_tabs()` 等步骤会重新布局，任何"只在构造时设一次"的可见性
        都可能被后续布局覆盖 —— 真机上出现过"注册表写着展开、启动却折叠"就是这个原因。
        """
        if visible is None:
            try:
                # 注意：注册表里存的是 'true'/'false' 字符串，必须用 settings_bool
                # 解析（bool('false') 是 True —— 这正是"展开记录失效"的根因）。
                visible = not settings_bool(
                    self._settings.value(SETTINGS_NAV_COLLAPSED, False), False
                )
            except (TypeError, ValueError):
                visible = True
        visible = bool(visible)

        panel = getattr(self, "nav_panel", None)
        splitter = getattr(self, "central_splitter", None)
        self._nav_collapsed = not visible

        if panel is not None:
            try:
                if visible:
                    # 展开前先恢复最小宽度，否则 setVisible(True) 时会被压成 0 宽
                    panel.setMinimumWidth(self._nav_min_width())
                panel.setVisible(visible)
            except RuntimeError:
                pass

        if splitter is not None:
            try:
                if visible:
                    width = self._saved_nav_width()
                    total = max(width + 200, splitter.width() or (width + 800))
                    splitter.setSizes([width, max(200, total - width)])
                else:
                    splitter.setSizes(
                        [0, max(200, splitter.width() or 900)]
                    )
            except (RuntimeError, TypeError, ValueError):
                pass

        action = getattr(self, "action_toggle_nav", None)
        if action is not None:
            try:
                action.setText(
                    "展开左侧列表" if self._nav_collapsed else "折叠左侧列表"
                )
            except RuntimeError:
                pass
        # 折叠状态已应用 —— 标签栏要等这一刻之后再决定显示（避免启动时闪一下）
        self._nav_state_applied = True
        self._update_bot_tab_bar_visible()
        return visible

    def _schedule_nav_restore(self) -> None:
        """在窗口显示前/后各复算几次左栏状态（N5）。

        构造期设一次是不够的：`_restore_settings()` 还原窗口几何、`show()` 触发布局、
        `_restore_open_tabs()` 又会改中央区域 —— 任何一步都可能把"展开"覆盖掉。
        这里用几次单次定时器把状态**复算到稳定**，代价极小且幂等。
        """
        self._restore_bot_tab_bar_preference()
        for delay in (0, 50, 250):
            try:
                QTimer.singleShot(delay, self._reapply_nav_state)
            except (AttributeError, TypeError, RuntimeError):
                continue

    def _reapply_nav_state(self) -> None:
        """按注册表重新套一次左栏状态（供延迟复算调用，幂等）。"""
        try:
            self._restore_nav_state()
        except (RuntimeError, AttributeError, TypeError, ValueError):
            pass

    def _restore_bot_tab_bar_preference(self) -> None:
        """按注册表还原"标签栏是否手动显示"（默认关闭，靠左栏折叠自动出现）。"""
        try:
            forced = settings_bool(
                self._settings.value(SETTINGS_BOT_TAB_BAR, False), False
            )
        except (TypeError, ValueError):
            forced = False
        self._bot_tab_bar_forced = forced
        action = getattr(self, "action_toggle_bot_tab_bar", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(forced)
            action.blockSignals(False)

    def reset_nav_width(self) -> None:
        """把左栏宽度恢复成默认值（视图菜单里的出口，防止记录里的宽度不可用）。"""
        splitter = getattr(self, "central_splitter", None)
        if splitter is None:
            return
        total = max(DEFAULT_NAV_WIDTH + 200, splitter.width() or (DEFAULT_NAV_WIDTH + 800))
        splitter.setSizes([DEFAULT_NAV_WIDTH, total - DEFAULT_NAV_WIDTH])
        try:
            self._settings.setValue(SETTINGS_NAV_SPLIT, [DEFAULT_NAV_WIDTH, total - DEFAULT_NAV_WIDTH])
        except (TypeError, ValueError):
            pass
        # 顺手把折叠状态也掰回"展开"，否则点了没反应会让人以为坏了
        if getattr(self, "_nav_collapsed", False):
            self.toggle_nav(True)
        self.statusBar().showMessage("左栏宽度已恢复为 {} 像素。".format(DEFAULT_NAV_WIDTH), 5000)

    def _nav_min_width(self) -> int:
        """左栏的最小宽度：至少放得下顶部那两个按钮（真机 2026-10-06 要求）。

        两个按钮（"查看已有 bot" / "编辑或新建 bot"）并排，比 MIN_NAV_WIDTH 宽 ——
        所以取两者较大者，分隔条再也不能把按钮裁掉。
        """
        panel = getattr(self, "nav_panel", None)
        need = MIN_NAV_WIDTH
        if panel is not None:
            try:
                need = max(need, int(panel.property("navButtonsWidth") or 0))
            except (RuntimeError, TypeError, ValueError):
                need = MIN_NAV_WIDTH
        return need

    def toggle_nav(self, visible: Optional[bool] = None) -> bool:
        """折叠 / 展开左侧导航栏，返回折叠后的可见状态。

        - 折叠前会把当前宽度记进 nav/split，展开时按记忆宽度恢复
        - 动作文案随状态在「折叠左侧列表 / 展开左侧列表」之间切换
        """
        panel = getattr(self, "nav_panel", None)
        if panel is None:
            return True
        if visible is None:
            visible = self._nav_collapsed
        visible = bool(visible)
        was_visible = not self._nav_collapsed

        if visible:
            width = DEFAULT_NAV_WIDTH
            saved = self._settings.value(SETTINGS_NAV_SPLIT)
            if saved:
                try:
                    width = max(MIN_NAV_WIDTH, int(list(saved)[0]))
                except (TypeError, ValueError, IndexError):
                    width = DEFAULT_NAV_WIDTH
            total = max(width + 200, self.central_splitter.width() or (width + 800))
            self.central_splitter.setSizes([width, total - width])
        else:
            # 先记住折叠前的宽度，否则再次展开会没有参考值
            if was_visible and self.central_splitter.width() > 0:
                sizes = self.central_splitter.sizes()
                if sizes and sizes[0] >= MIN_NAV_WIDTH:
                    self._settings.setValue(SETTINGS_NAV_SPLIT, list(sizes))
            self.central_splitter.setSizes([0, max(200, self.central_splitter.width() or 900)])

        self._nav_collapsed = not visible
        self._settings.setValue(SETTINGS_NAV_COLLAPSED, self._nav_collapsed)
        panel.setVisible(visible)
        # 折叠 → 标签栏出现；展开 → 按"是否手动勾选"决定是否保留
        self._nav_state_applied = True
        self._update_bot_tab_bar_visible()
        if visible:
            # 展开时确保最小宽度已恢复，否则可能被压成 0 宽
            panel.setMinimumWidth(self._nav_min_width())
        if getattr(self, "action_toggle_nav", None) is not None:
            self.action_toggle_nav.setText(
                "展开左侧列表" if self._nav_collapsed else "折叠左侧列表"
            )
        # 折叠后给一句可发现的提示（否则"左栏消失"看着像 bug）
        try:
            if self._nav_collapsed:
                self.statusBar().showMessage(
                    "左侧列表已折叠（Ctrl+L 或「视图 → 展开左侧列表」可恢复）", 6000
                )
            else:
                self.statusBar().showMessage("左侧列表已展开。", 4000)
        except (AttributeError, RuntimeError):
            pass
        return visible

    def _on_toggle_nav_action(self) -> None:
        visible = self.toggle_nav()
        self.statusBar().showMessage(
            "已{}左侧列表".format("展开" if visible else "折叠（Ctrl+L 可恢复）"), 3000
        )

    def show_status_legend(self) -> None:
        """状态图例与快捷键说明。"""
        QMessageBox.information(
            self,
            "状态图例与快捷键",
            "【左侧列表状态符号】\n"
            "  ●  运行中        ◐  启动中 / 停止中 / 部分运行\n"
            "  ○  未运行        !  异常（启动失败或非 0 退出）\n"
            "  ［2/3］＝ 该机器人 3 个程序中有 2 个在运行\n"
            "  ★  主程序（primary），其余为副程序\n\n"
            "【常用快捷键】\n"
            "  Ctrl+N 新建 Bot          Ctrl+E 编辑当前 Bot\n"
            "  Ctrl+B 查看已有 bot      F5 / Shift+F5 启动 / 停止当前 Bot\n"
            "  Ctrl+R 重启当前 Bot      Ctrl+W 关闭当前窗口\n"
            "  Ctrl+Shift+W 关闭全部窗口\n"
            "  Ctrl+L 折叠/展开左侧列表  Ctrl+Tab 切换窗口\n"
            "  Ctrl+1..9 跳到第 N 个机器人   F6 重新载入配置\n\n"
            "【记住】关闭窗口不会停止程序，程序会继续在后台运行。",
        )

    def _maybe_show_first_run_hint(self) -> None:
        """第一次运行（从未保存过界面状态）时，给一条操作提示。"""
        if settings_bool(self._settings.value("ui/hint_shown", False), False):
            return
        self._settings.setValue("ui/hint_shown", True)
        self.statusBar().showMessage(
            "提示：左侧列表单击机器人＝打开窗口；启动/停止请用右键菜单或工具栏；"
            "关闭窗口不会停止程序（Ctrl+L 可折叠左侧列表）。",
            15000,
        )

    def _is_dark_theme(self) -> bool:
        """当前是否深色主题（R2 起统一由 app.ui.theme 判定）。"""
        return theme_tokens.is_dark(self)

    def _nav_style_sheet(self) -> str:
        """左侧竖栏样式：跟随当前主题自动切换浅色 / 深色。

        R2 起样式表由 app.ui.theme 统一生成（调色板取色 + 深色兜底），
        这样"热切换主题"只要重刷一次就够，不用担心这里漏了某个控件。
        """
        return theme_tokens.nav_tree_qss(self)

    def _muted_color(self) -> str:
        """次要文字颜色（跟随主题）。"""
        return theme_tokens.muted_text_color(self)

    def _apply_theme(self) -> None:
        """把当前主题应用到左侧竖栏、提示文字、占位页与所有日志窗口。

        R2 起所有颜色都来自 app.ui.theme，因此这里只需要"重新取一次样式表"：
        深色/浅色、以及运行中切换模式，走的都是同一条路。
        """
        self._theme_refresh_count = int(getattr(self, "_theme_refresh_count", 0)) + 1

        # N2.5：把"外壳"控件（菜单栏/工具栏/按钮/标签/输入）的配色**一次显式指定**。
        # 这些控件不读我们的调色板，而是走 Qt 原生样式 —— 真机症状就是
        # "浅色界面里工具栏/按钮文字发白""深色界面里菜单栏看不清"。
        # 设在主窗口上可以覆盖整棵控件树（带样式表的子控件会与它合并）。
        try:
            self.setStyleSheet(theme_tokens.chrome_qss(self))
        except (RuntimeError, AttributeError):
            pass

        # N2.6 / N2.8：菜单栏走统一入口 —— 关掉系统原生绘制、套 QSS、
        # 并把文字色直接设进菜单栏调色板（只写 QSS 会被 Qt 缓存住不再重取，
        # 真机症状："一旦某刻是浅色，之后深色模式下菜单栏永远是黑字"）。
        self._sync_menubar_theme()

        panel = getattr(self, "nav_panel", None)
        if panel is not None:
            try:
                panel.setStyleSheet(theme_tokens.nav_tree_qss(panel))
            except RuntimeError:
                pass

        # 实例区顶部的浏览器式标签栏：QTabBar 也是原生绘制，必须显式给色
        tab_bar = getattr(self, "bot_tab_bar", None)
        if tab_bar is not None:
            try:
                tab_bar.setStyleSheet(theme_tokens.bot_tab_bar_qss(tab_bar))
            except (RuntimeError, AttributeError):
                pass

        muted_qss = theme_tokens.dialog_hint_qss(self)
        for name in ("placeholder_hint_label", "nav_hint_label"):
            label = getattr(self, name, None)
            if label is not None:
                try:
                    label.setStyleSheet(muted_qss)
                except RuntimeError:
                    pass

        # 空状态页的提示区也跟着换色（深色下不要留白块）
        for name in ("placeholder_page", "placeholder_hint_label"):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                widget.setStyleSheet(theme_tokens.pane_qss(widget))
            except (RuntimeError, AttributeError):
                pass

        for tab in list(self._tabs.values()):
            try:
                tab.apply_theme()
            except (RuntimeError, AttributeError):
                continue

        # N2.5：菜单栏/工具栏是 Qt "原生绘制"的部件，换了样式表后可能沿用旧样式
        # 直到下一次 polish —— 这里主动重刷一次，避免"第一行颜色停在启动时"。
        for chrome_widget in (self.menuBar(), self.statusBar(),
                              getattr(self, "toolbar", None)):
            if chrome_widget is None:
                continue
            try:
                style = chrome_widget.style()
                style.unpolish(chrome_widget)
                style.polish(chrome_widget)
                chrome_widget.update()
            except (RuntimeError, AttributeError):
                pass
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu is None:
                continue
            try:
                menu.style().unpolish(menu)
                menu.style().polish(menu)
                menu.update()
            except (RuntimeError, AttributeError):
                pass

        # 已打开的对话框也要刷（它们不在主窗口的控件树里）
        for dialog in self._live_dialogs():
            self._apply_dialog_theme(dialog)

    def theme_refresh_count(self) -> int:
        """_apply_theme 实际执行过多少次（自检用；判断合并刷新是否成立）。"""
        return int(getattr(self, "_theme_refresh_count", 0))

    def sync_system_theme(self) -> bool:
        """「跟随系统」时检查系统深浅是否变了，变了就重套主题（N3.0）。

        返回是否真的发生了切换。因为现在"跟随系统"会被解析成实色方案
        （深/浅二选一），所以系统的深浅变化等价于一次普通的模式切换 ——
        走的是同一条已验证的路径，不会出现"三套调色板打架"。

        检测手段有两个，任一可用即可：
          · `QApplication.paletteChanged`（系统改主题时 Qt 会上报）
          · 定时轮询注册表（兜底：部分 Windows 版本不上报）
        """
        if theme_tokens.current_mode() != theme_tokens.MODE_SYSTEM:
            return False
        before = theme_tokens.resolved_mode()
        after = "dark" if theme_tokens.os_scheme() == "dark" else "light"
        if before == after:
            return False
        # 系统真的换了深浅：重套主题（apply_theme 会重新解析并刷新所有控件）
        self.set_theme_mode(theme_tokens.MODE_SYSTEM, remember=False)
        return True

    def _on_palette_changed(self, _palette=None) -> None:
        """系统深色/浅色切换时重新套用样式（由 QApplication.paletteChanged 触发）。

        一次切换往往触发多次 paletteChanged（Qt 会分批刷子控件），这里用
        单次定时器合并成一次 _apply_theme，避免同一帧里重刷很多遍。
        """
        # 显式浅色/深色模式下，调色板是我们自己设的，不需要跟着系统再刷一遍
        if theme_tokens.current_mode() != theme_tokens.MODE_SYSTEM:
            return
        # set_theme_mode() 自己会刷，避免"刷两次"
        if getattr(self, "_theme_applying", False):
            return
        if getattr(self, "_theme_refresh_pending", False):
            return
        self._theme_refresh_pending = True

        def _flush() -> None:
            self._theme_refresh_pending = False
            # N3.0：系统真的换了深浅 → 走完整的"重套主题"（与手动切换同一条路），
            # 否则只刷一次样式（系统可能只是重发了调色板）
            if not self.sync_system_theme():
                self._apply_theme()
            self._rebuild_layout_menu()

        QTimer.singleShot(0, _flush)

    def _start_os_theme_watch(self) -> None:
        """定时检查系统深浅（N3.0 兜底）。

        `paletteChanged` 在部分 Windows 版本上不会因为"系统切换深色"而触发
        （因为我们自己设过调色板），所以用一个低频定时器直接读注册表。
        只在「跟随系统」模式下才真正比较，开销极低。
        """
        timer = QTimer(self)
        timer.setInterval(2000)
        timer.timeout.connect(self.sync_system_theme)
        timer.start()
        self._os_theme_timer = timer

    # ------------------------------------------------------------------
    # 左侧导航栏：右键菜单
    # ------------------------------------------------------------------

    def _on_nav_context_menu(self, pos) -> None:
        tree = getattr(self, "nav_tree", None)
        if tree is None:
            return
        item = tree.itemAt(pos)
        if item is not None:
            tree.setCurrentItem(item)
            item.setSelected(True)
        bot_id = self._nav_item_bot_id(item) or self._nav_selected_bot_id()
        program_key = self._nav_item_program_key(item)
        bot = self.config.get_bot(bot_id) if bot_id else None

        menu = QMenu(self)
        header = QAction(
            "机器人：{}".format(bot.name if bot is not None else "（未选中）"), menu
        )
        header.setEnabled(False)
        menu.addAction(header)
        menu.addSeparator()

        for text, action, enabled in (
            ("打开窗口", "open", bool(bot_id)),
            ("启动", "start", bool(bot_id)),
            ("停止", "stop", bool(bot_id)),
            ("重启", "restart", bool(bot_id)),
            ("编辑配置…", "edit", bool(bot_id)),
            ("打开工作目录…", "open_dir", self._nav_has_directory(bot)),
            ("关闭窗口（不停程序）", "close_window", bool(bot_id) and bot_id in self._tabs),
            ("删除…", "remove", bool(bot_id)),
            ("上移", "up", bool(bot_id)),
            ("下移", "down", bool(bot_id)),
        ):
            entry = QAction(text, menu)
            entry.setEnabled(enabled)
            entry.triggered.connect(
                lambda _checked=False, a=action, b=bot_id, k=program_key: self._nav_run(a, b, k)
            )
            menu.addAction(entry)

        menu.addSeparator()

        # 步骤 4：左栏右键里的布局入口（与「视图 → 分屏布局」等价）
        layout_menu = menu.addMenu("分屏布局")
        tab = self.tab_for(bot_id) if bot_id else None
        current_kind = tab.layout_kind() if tab is not None else ""
        for kind in self._layout_kinds():
            item = QAction(layout_model.LAYOUT_KIND_LABELS.get(kind, kind), layout_menu)
            item.setCheckable(True)
            item.setChecked(bool(current_kind) and kind == current_kind)
            item.setEnabled(bool(bot_id))
            item.triggered.connect(
                lambda _checked=False, k=kind, b=bot_id: self._apply_layout_for_bot(b, k)
            )
            layout_menu.addAction(item)
        layout_menu.addSeparator()
        reset_item = QAction("恢复默认布局", layout_menu)
        reset_item.setEnabled(bool(bot_id))
        reset_item.triggered.connect(
            lambda _checked=False, b=bot_id: self._reset_layout_for_bot(b)
        )
        layout_menu.addAction(reset_item)
        if current_kind == layout_model.KIND_CUSTOM:
            custom_item = QAction("当前：自定义布局", layout_menu)
            custom_item.setEnabled(False)
            layout_menu.addAction(custom_item)

        menu.addSeparator()
        copy_item = QAction("复制该程序日志", menu)
        copy_item.setEnabled(bool(bot_id) and bool(program_key))
        copy_item.triggered.connect(lambda: self._nav_copy_program_log(bot_id, program_key))
        menu.addAction(copy_item)

        list_item = QAction("查看已有 bot…", menu)
        list_item.triggered.connect(self.open_bot_list_dialog)
        menu.addAction(list_item)

        refresh_item = QAction("刷新列表", menu)
        refresh_item.triggered.connect(self._refresh_nav)
        menu.addAction(refresh_item)

        menu.exec(tree.viewport().mapToGlobal(pos))

    def _nav_has_directory(self, bot: Optional[Bot]) -> bool:
        if bot is None:
            return False
        for program in bot.programs:
            if os.path.isdir(program.working_dir(self.config.base_dir)):
                return True
        return False

    def _nav_run(self, action: str, bot_id: str, program_key: str = "") -> None:
        """执行左侧右键菜单里的动作。"""
        if not bot_id:
            self.statusBar().showMessage("请先选中一个机器人。", 4000)
            return
        bot = self.config.get_bot(bot_id)
        if action == "open":
            if program_key:
                self.show_program(program_key, scroll=False)
            else:
                # R1-1：右键「打开窗口」也落到主程序行
                self.show_bot_view(bot_id, focus=True, focus_first=True)
        elif action == "start":
            self.start_bot(bot_id)
        elif action == "stop":
            self.stop_bot(bot_id)
        elif action == "restart":
            self.restart_bot(bot_id)
        elif action == "edit":
            self.edit_bot(bot_id)
        elif action == "close_window":
            self.close_bot_window(bot_id, confirm=False)
        elif action == "remove":
            self.remove_bot(bot_id, ask=True)
        elif action == "up":
            self.move_bot_up(bot_id)
        elif action == "down":
            self.move_bot_down(bot_id)
        elif action == "open_dir" and bot is not None:
            for program in bot.programs:
                path = program.working_dir(self.config.base_dir)
                if os.path.isdir(path):
                    self.open_directory(path)
                    break
        self._refresh_nav()
        self._update_nav_buttons()

    def _apply_layout_for_bot(self, bot_id: str, kind: str) -> bool:
        """对指定机器人套用布局模板（左栏右键菜单用）。"""
        if not bot_id:
            return False
        tab = self.tab_for(bot_id)
        if tab is not None:
            ok = tab.set_layout(kind, notify=True)
            if ok:
                self._after_layout_changed(bot_id, kind)
            return ok
        self._remember_layout_kind(bot_id, kind)
        bot = self.config.get_bot(bot_id)
        self.statusBar().showMessage(
            "「{}」的布局已记录：{}（打开窗口后生效）".format(
                bot.name if bot is not None else bot_id,
                layout_model.LAYOUT_KIND_LABELS.get(kind, kind),
            ),
            5000,
        )
        return True

    def _reset_layout_for_bot(self, bot_id: str) -> bool:
        """对指定机器人恢复默认布局（左栏右键菜单用）。"""
        if not bot_id:
            return False
        tab = self.tab_for(bot_id)
        if tab is None:
            self._forget_layout(bot_id)
            return True
        ok = tab.reset_layout()
        if ok:
            self._forget_layout(bot_id)
            self._save_pane_layout(bot_id, tab)
            self._rebuild_layout_menu()
        return ok

    def _nav_copy_program_log(self, bot_id: str, program_key: str) -> None:
        """把某个程序的日志复制到剪贴板。"""
        if not bot_id:
            return
        tab = self._tabs.get(bot_id)
        if tab is None:
            self.statusBar().showMessage("该机器人窗口还没打开，先单击左侧条目打开窗口。", 4000)
            return
        view = tab.log_view(program_key)
        if view is None:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(view.text())
            self.statusBar().showMessage("已复制该程序日志到剪贴板。", 3000)

    # ------------------------------------------------------------------
    # 右侧实例区：页面管理
    # ------------------------------------------------------------------

    def show_bot_view(
        self, bot_id: str, focus: bool = True, focus_first: bool = False
    ) -> Optional[BotTab]:
        """显示某个机器人的窗口（必要时先创建），并把左栏高亮同步到焦点程序。

        focus_first=True 时额外把焦点落到主程序窗格（左栏单击机器人条目用）。
        **两种情况都会让高亮停在"当前正在看的程序"那一行**（R1-1）：
        以前只有 ``focus_first_program()`` 返回 True 才移动高亮，返回 False 时
        高亮留在机器人行，而窗口里显示的其实是主程序 —— 高亮与 ▸ 不一致。
        现在无论焦点是否移动成功，都按"焦点程序 → 主程序 → 机器人行"的顺序落位。
        """
        tab = self.open_bot_tab(bot_id, focus=focus)
        if tab is None:
            return None
        if focus_first:
            tab.focus_first_program(scroll=False)
        if not focus:
            return tab

        # 高亮目标：窗口里真正聚焦的程序 → 主程序 → 该机器人行（最后兜底）
        target_key = tab.focused_program() or self._primary_key_for_bot(bot_id)
        if target_key and self._select_nav_bot(bot_id, program_key=target_key):
            self._mark_nav_focus(bot_id, target_key, notify=False)
        else:
            self._select_nav_bot(bot_id)
            self._mark_nav_focus(bot_id, "", notify=False)
        return tab

    def _nav_program_for_bot(self, bot_id: str) -> str:
        """该机器人"当前正在看的程序"的 manager key（用于把左栏高亮落到程序行）。

        取值顺序（R1-1，每一步都是**确定性**的，不依赖窗口是否已创建）：
          1. 窗口里真正聚焦的程序（窗口已打开时最准确）
          2. **配置里的主程序**（没有 primary 时取第一个）—— 关键兜底：
             窗口没打开、或 BotTab 还没定焦点时，以前会返回空串，
             于是高亮退回机器人行，而窗口其实显示着主程序 → 高亮与 ▸ 不一致
             （真机症状："点击 ATRI[0/3] 时高亮依旧在 ATRI 上"）
          3. 按状态里的程序顺序取第一个
          都没有则返回空串（此时高亮只能落在机器人行）
        """
        if not bot_id:
            return ""
        tab = self._tabs.get(bot_id)
        if tab is not None:
            focused = tab.focused_program()
            if focused and tab.log_view(focused) is not None:
                return focused
        primary_key = self._primary_key_for_bot(bot_id)
        if primary_key:
            return primary_key
        status = self.bot_status_of(bot_id)
        if status is None:
            return ""
        keys = self._nav_program_keys(status)
        return keys[0] if keys else ""

    def _select_nav_bot(self, bot_id: str, program_key: str = "") -> bool:
        """把左侧列表的高亮切到指定机器人，或它的某个程序行。

        传了 program_key 且能找到该程序条目时，橙色落在**程序行**上（R1-1：
        "点机器人就跳到主程序"的观感）；找不到则退回落在这个机器人行上。
        返回是否成功定位到程序行。

        修 1：这里同时把"当前高亮行"记进 ``_nav_active_program_key`` /
        ``_nav_active_bot_id``，供 ``_refresh_nav()`` 重建后恢复（重建不再抹掉高亮）。
        """
        item = getattr(self, "_nav_items", {}).get(bot_id)
        tree = getattr(self, "nav_tree", None)
        if item is None or tree is None:
            self._nav_debug_log(
                "select_nav_bot({!r}, {!r}) 失败：条目或树不存在".format(bot_id, program_key)
            )
            return False
        target = self._nav_child_item(item, program_key) if program_key else None
        on_program = target is not None
        if target is None:
            target = item
        try:
            tree.blockSignals(True)
            item.setExpanded(True)
            target.setSelected(True)
            tree.setCurrentItem(target, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        except (RuntimeError, AttributeError):
            return False
        finally:
            tree.blockSignals(False)
        if on_program:
            self._nav_active_program_key = str(
                target.data(0, ROLE_NAV_PROGRAM_KEY) or ""
            )
            self._nav_active_bot_id = bot_id
        else:
            self._nav_active_program_key = ""
            self._nav_active_bot_id = bot_id
        self._nav_debug_log(
            "select_nav_bot({!r}, {!r}) -> {}".format(
                bot_id, program_key, "程序行" if on_program else "机器人行"
            )
        )
        # 确认 2（Y）：▸ 标记永远跟着高亮走，所以每次改高亮都重算一次标记
        if self.current_tab() is None:
            self._apply_nav_focus_marker(notify=False)
        self._update_nav_buttons()
        self._update_nav_hint()
        return on_program

    def _select_nav_for_bot(self, bot_id: str) -> bool:
        """把左栏高亮落到"该机器人当前正在看的程序行"（R1-1 的统一入口）。"""
        return self._select_nav_bot(bot_id, program_key=self._nav_program_for_bot(bot_id))

    def _primary_key_for_bot(self, bot_id: str) -> str:
        """该机器人的"主程序" key（没有 primary 时取配置里的第一个程序）。

        **只按配置算，不看窗口状态** —— 这是"点机器人行 → 高亮落到主程序行"
        这条规则的唯一依据。之前靠 `tab.focused_program()` / `bot_status_of()`
        推断，窗口没打开或取值时机不同就会返回空串，于是高亮退回机器人行
        （真机症状："点击 ATRI[0/3] 时高亮依旧在 ATRI 上，不在其下的主程序"）。

        注意：这里**不能**加 `@staticmethod`（曾经误加过）—— 它会吃掉第一个参数，
        使 `self` 变成传入的 bot_id、`self.config` 抛 AttributeError 被下方的
        except 静默吞掉，导致本函数**永远返回空串**，高亮于是退回机器人行。
        """
        bot = self.config.get_bot(bot_id) if bot_id else None
        if bot is None:
            return ""
        programs = list(getattr(bot, "programs", []) or [])
        if not programs:
            return ""
        primary = [p for p in programs if getattr(p, "role", "") == ROLE_PRIMARY]
        target = primary[0] if primary else programs[0]
        program_id = str(getattr(target, "id", "") or "")
        if not program_id:
            return ""
        return build_manager_key(bot_id, program_id)

    def _nav_child_item(
        self, bot_item: QTreeWidgetItem, program_key: str
    ) -> Optional[QTreeWidgetItem]:
        """在机器人节点下找某个程序条目（找不到返回 None）。"""
        if not program_key:
            return None
        for index in range(bot_item.childCount()):
            child = bot_item.child(index)
            if str(child.data(0, ROLE_NAV_PROGRAM_KEY) or "") == program_key:
                return child
        return None

    def _stack_has_bot(self, bot_id: str) -> bool:
        return bot_id in self._tabs and self.stack.indexOf(self._tabs[bot_id]) > 0

    # ------------------------------------------------------------------
    # 状态显示
    # ------------------------------------------------------------------

    def refresh_status(self) -> None:
        """刷新状态栏并更新动作可用性。

        该函数会在构造期间被 QStackedWidget.currentChanged 等信号间接触发，
        因此对"还没建好的控件"一律跳过，避免半初始化状态下崩溃。
        """
        running = self.manager.running_count
        opened = len(self._tabs)
        statuses = self.bot_status_list()
        active_bots = sum(1 for item in statuses if item.any_running)

        status_label = getattr(self, "status_label", None)
        if status_label is not None:
            status_label.setText(
                "运行中的程序：{}　|　有进程的机器人：{}　|　已打开窗口：{}　|　配置内机器人：{}".format(
                    running, active_bots, opened, len(statuses)
                )
            )

        has_tab = opened > 0
        for name, enabled in (
            ("action_edit_bot", has_tab),
            ("action_stop_bot", has_tab and running > 0),
            ("action_restart_bot", has_tab),
            ("action_start_bot", self._current_bot_id() is not None),
            ("action_close_tab", has_tab),
        ):
            action = getattr(self, name, None)
            if action is not None:
                action.setEnabled(enabled)

        self._update_placeholder_visible()
        self._refresh_title()

    def _refresh_title(self) -> None:
        """窗口标题：当前机器人 + 运行中的程序数（无窗口时只显示程序名）。"""
        bot_id = self._current_bot_id()
        status = self.bot_status_of(bot_id)
        if status is not None:
            self.setWindowTitle(
                "{} - {}　运行中 {} 个程序".format(
                    APP_NAME, status.name, self.manager.running_count
                )
            )
        else:
            self.setWindowTitle(
                "{} - 已打开 {} 个窗口".format(APP_NAME, len(self._tabs))
            )

    def _sync_manager_log_flag(self) -> None:
        """把「把管理器自身日志也写入窗口」这个开关推给所有已打开的窗口。

        以前这里是 `_on_manager_log()`：把 `log_message` 再 append 一遍 ——
        而 `ProcessManager._log()` 早就通过 `output_text` 发过同样的文本了，
        于是**每条管理器日志显示两遍**，开关也关不掉（真机事故 2026-10-03）。
        现在只保留 `output_text` 一条路径，开关按 channel 在 BotTab 过滤。
        """
        for tab in self._tabs.values():
            try:
                tab.set_show_manager_log(self._show_manager_log)
            except (RuntimeError, AttributeError):
                pass

    def _on_state_changed(self, key: str, _state: str, _message: str = "") -> None:
        """状态变化：刷新状态栏，并合并刷新左侧列表里对应的那一项。

        只刷新受影响的机器人节点（局部更新），避免高频状态下整棵树 clear() 重建，
        既省性能也不会让用户的选中/展开状态跳掉。
        """
        self.refresh_status()
        bot_id = ""
        if key and ":" in key:
            bot_id = key.split(":", 1)[0]
        self._schedule_nav_refresh(bot_id)

    def _schedule_nav_refresh(self, bot_id: str = "") -> None:
        """把密集的状态变化合并成一次导航栏刷新（局部更新优先）。"""
        if getattr(self, "nav_tree", None) is None:
            return
        if bot_id:
            self._nav_dirty_bots.add(bot_id)
        else:
            self._nav_dirty_all = True
            self._nav_dirty_bots.clear()
        if getattr(self, "_nav_refresh_timer", None) is None:
            self._nav_refresh_timer = QTimer(self)
            self._nav_refresh_timer.setSingleShot(True)
            self._nav_refresh_timer.setInterval(250)
            self._nav_refresh_timer.timeout.connect(self._flush_nav_refresh)
        self._nav_refresh_timer.start()

    def _flush_nav_refresh(self) -> None:
        """定时器到点：把累积的脏项刷新掉。

        修 1（关键）：**先复位脏标记再动手刷新**。
        原实现在 ``_nav_dirty_all`` 为真时直接 ``_refresh_nav(); return``，
        既没把 ``_nav_dirty_all`` 清掉、也没清 ``_nav_dirty_bots`` ——
        于是一旦出现过一次"无 bot_id 的刷新请求"，此后每次定时器到点都会再做
        一次**整树重建**，用户刚点出来的高亮就被反复抹掉
        （真机表现：单击程序行后高亮又消失/跳回）。
        """
        dirty_all = bool(self._nav_dirty_all)
        dirty_bots = set(self._nav_dirty_bots)
        # 复位：本次的刷新请求已经"消费"掉了
        self._nav_dirty_all = False
        self._nav_dirty_bots.clear()

        self._nav_debug_log(
            "flush：dirty_all={} dirty_bots={}".format(dirty_all, sorted(dirty_bots))
        )
        if dirty_all:
            self._refresh_nav()
            return

        missing = False
        for bot_id in dirty_bots:
            item = getattr(self, "_nav_items", {}).get(bot_id)
            if item is None:
                missing = True
                continue
            self._update_nav_item(bot_id)
        # 配置变了（有机器人不在列表里）时补一次全量刷新
        if missing:
            self._refresh_nav()

    def _update_nav_item(self, bot_id: str) -> None:
        """只更新某一个机器人的左侧条目（文字 / 颜色 / 提示 / 子节点状态）。"""
        item = self._nav_items.get(bot_id)
        if item is None:
            self._refresh_nav()
            return
        status = self.bot_status_of(bot_id)
        if status is None:
            self._refresh_nav()
            return

        # 子节点数量与配置不一致（增删程序）：属于结构性变化，交给全量刷新
        if item.childCount() != len(status.program_names):
            self._refresh_nav()
            return

        tree = getattr(self, "nav_tree", None)
        try:
            if tree is not None:
                tree.blockSignals(True)
            # 机器人行：文字（含 ▌ 前缀）+ 提示 + 状态色
            item.setText(0, self._nav_bot_text(status))
            item.setToolTip(0, self._nav_bot_tooltip(status))
            item.setData(0, ROLE_NAV_OPENED, status.bot_id in self._tabs)
            color = bot_status_color(status)
            if color:
                item.setForeground(0, QBrush(QColor(color)))

            focused_key = self._nav_focus_marker_key()
            opened = status.bot_id in self._tabs
            keys = self._nav_program_keys(status)
            for index in range(item.childCount()):
                child = item.child(index)
                key = keys[index] if index < len(keys) else ""
                if not key:
                    continue
                child.setData(0, ROLE_NAV_PROGRAM_KEY, key)
                child.setToolTip(0, self._nav_program_tooltip(status, key))
                self._apply_nav_item_roles(
                    child, status, key,
                    bool(focused_key) and key == focused_key, opened,
                )
        except (RuntimeError, AttributeError):
            if tree is not None:
                try:
                    tree.blockSignals(False)
                except RuntimeError:
                    pass
            return
        if tree is not None:
            tree.blockSignals(False)
        self._update_nav_hint()

    # ------------------------------------------------------------------
    # 窗口（页面）管理
    # ------------------------------------------------------------------

    def _current_bot_id(self) -> Optional[str]:
        widget = self.stack.currentWidget() if getattr(self, "stack", None) else None
        if isinstance(widget, BotTab):
            return widget.bot_id
        return None

    def current_tab(self) -> Optional[BotTab]:
        widget = self.stack.currentWidget() if getattr(self, "stack", None) else None
        return widget if isinstance(widget, BotTab) else None

    def tab_for(self, bot_id: str) -> Optional[BotTab]:
        return self._tabs.get(bot_id)

    def open_bot_tab(self, bot_id: str, focus: bool = True) -> Optional[BotTab]:
        """打开（或取得）某个机器人的窗口，并接入所有信号。"""
        existing = self._tabs.get(bot_id)
        if existing is not None:
            if focus:
                self.stack.setCurrentWidget(existing)
                # R1-1：高亮落到"这个机器人当前正在看的程序"那一行
                # （show_bot_view 会在返回后再定位一次，这里只是兜底）
                self._select_nav_for_bot(bot_id)
            return existing

        bot = self.config.get_bot(bot_id)
        if bot is None:
            self.statusBar().showMessage("找不到机器人：{}".format(bot_id), 4000)
            return None

        tab = BotTab(bot, self.config, self.manager, self)
        # 重新打开窗口时补一次订阅，避免复用旧对象导致收不到日志/状态
        try:
            tab.subscribe_process_manager(self.manager)
        except AttributeError:
            pass
        tab.startRequested.connect(self._on_tab_start_requested)
        tab.stopRequested.connect(self._on_tab_stop_requested)
        tab.restartRequested.connect(self._on_tab_restart_requested)
        tab.editRequested.connect(self.edit_bot)
        tab.closeRequested.connect(self.close_bot_tab)
        tab.openDirectoryRequested.connect(self.open_directory)
        # 步骤 4：窗格按钮 / 布局变化 / 焦点变化
        tab.programActionRequested.connect(self._on_tab_program_action)
        tab.layoutChanged.connect(lambda _kind, bid=bot_id: self._on_tab_layout_changed(bid, _kind))
        tab.focusChanged.connect(lambda key, bid=bot_id: self._on_tab_focus_changed(bid, key))

        self.stack.addWidget(tab)
        # 标签栏只是"辅助入口"：它出问题绝不能连累打开窗口本身
        # （真机事故：bot_name 被当方法调用 → 整个窗口打不开、日志界面进不去）
        self._safe_sync_bot_tab_bar()
        self._tabs[bot_id] = tab
        try:
            tab.set_show_manager_log(self._show_manager_log)   # 新窗口沿用当前开关
        except (RuntimeError, AttributeError):
            pass

        # 布局偏好（模板 + 比例 + 焦点）优先，其次才是历史遗留的 split/<bot_id>
        if not self._restore_pane_layout(bot_id, tab):
            sizes = self._settings.value(SETTINGS_SPLIT_PREFIX + bot_id)
            if sizes:
                try:
                    tab.set_split_sizes([int(value) for value in sizes])
                except (TypeError, ValueError):
                    pass

        if focus:
            self.stack.setCurrentWidget(tab)
        self._refresh_nav()
        if focus:
            # R1-1：高亮落到**窗口里真正聚焦的程序**（新窗口的焦点由 BotTab 初始化为主程序）。
            # 注意：这里**不能**再调 _select_nav_for_bot() —— 它在 show_bot_view()
            # 之后执行，会把已经落到主程序行的高亮拽回机器人行（真机 bug）。
            target_key = tab.focused_program() or self._primary_key_for_bot(bot_id)
            if not (target_key and self._select_nav_bot(bot_id, program_key=target_key)):
                self._select_nav_bot(bot_id)
        self.refresh_status()
        return tab

    def _tab_title(self, bot: Bot) -> str:
        """窗口标题（左侧列表里已显示计数，这里保持纯名称）。"""
        return bot.name

    def _update_placeholder_visible(self) -> None:
        """没有窗口时显示空状态占位页，否则隐藏它（不占位置）。"""
        placeholder = getattr(self, "placeholder_page", None)
        stack = getattr(self, "stack", None)
        if placeholder is None or stack is None:
            return
        should_show = stack.count() <= 1
        try:
            placeholder.setVisible(should_show)
        except RuntimeError:
            return
        if should_show:
            stack.setCurrentWidget(placeholder)

    def _on_current_page_changed(self, index: int) -> None:
        """实例区切换页面：记住当前机器人，把左栏高亮落到它正在看的程序行，
        并重算 ▸ 标记（确认 1：标记只挂在"当前窗口正在显示的程序"上）。
        """
        widget = self.stack.widget(index) if getattr(self, "stack", None) else None
        if isinstance(widget, BotTab):
            self._settings.setValue(SETTINGS_CURRENT_BOT, widget.bot_id)
            self._select_nav_for_bot(widget.bot_id)
            self._apply_nav_focus_marker(notify=False)
        self._safe_sync_bot_tab_bar()
        self.refresh_status()

    def open_bot_tabs_in_order(self) -> List[str]:
        """已打开的窗口，按**左栏从上到下**的顺序返回 bot id。

        顺序判据只有一个来源：``self.config.bots``（左栏、Ctrl+1..9 用的也是它）。
        因此"在左栏右键上移/下移某个机器人"之后，Ctrl+Tab 的轮转顺序会跟着变。

        ``self.stack`` 的顺序是"窗口被创建的先后"（恢复会话时尤其容易与左栏不一致），
        绝不能拿它当轮转顺序 —— 那正是 Ctrl+Tab 出现 1→3→4→2 的原因。
        """
        ordered: List[str] = []
        seen = set()
        for bot in self.config.bots:
            if bot.id in self._tabs and bot.id not in seen:
                ordered.append(bot.id)
                seen.add(bot.id)
        # 兜底：万一有窗口的机器人已不在配置里（例如刚被删除），也排到末尾，
        # 保证"打开着的窗口一个都不会被漏掉"。
        for bot_id in self._tabs.keys():
            if bot_id not in seen:
                ordered.append(bot_id)
                seen.add(bot_id)
        return ordered

    def _cycle_page(self, offset: int) -> None:
        """在已打开的窗口之间循环切换（Ctrl+Tab / Ctrl+Shift+Tab）。

        N1：轮转顺序 = **左栏从上到下的顺序**（= 配置顺序），而不是窗口打开顺序。
        """
        order = self.open_bot_tabs_in_order()
        if not order:
            self.statusBar().showMessage("还没有打开任何机器人窗口。", 3000)
            return

        current_id = self._current_bot_id() or ""
        try:
            position = order.index(current_id)
        except ValueError:
            # 当前页面不是已登记的机器人窗口（例如占位页）：下一跳从第一个开始
            position = -1
        target_id = order[(position + offset) % len(order)]
        target = self._tabs.get(target_id)
        if target is None:
            return
        self._nav_debug_log(
            "cycle_page({}) {} -> {}".format(offset, current_id or "（占位页）", target_id)
        )
        self.stack.setCurrentWidget(target)

    def close_bot_tab(self, bot_id: str) -> None:
        """关闭标签页：若仍有程序在运行，询问是否一并停止。"""
        self.close_bot_window(bot_id, confirm=True)

    def close_bot_window(self, bot_id: str, confirm: bool = False) -> bool:
        """关闭某个机器人的窗口。

        confirm=True  ：仍有程序运行时弹三选一（停止并关闭 / 只关窗口 / 取消）
        confirm=False ：直接关闭窗口，程序继续在后台运行

        返回是否真的关闭了窗口；顺带给出"关闭窗口 ≠ 停止程序"的语义区分。
        """
        tab = self._tabs.get(bot_id)
        if tab is None:
            return False

        running_keys = [key for key in tab.all_keys() if self.manager.is_running(key)]
        if running_keys and confirm:
            stop_button = QMessageBox.StandardButton.Yes
            keep_button = QMessageBox.StandardButton.No
            box = QMessageBox(self)
            box.setWindowTitle("关闭窗口")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText("「{}」还有 {} 个程序在运行。".format(tab.bot_name, len(running_keys)))
            box.setInformativeText(
                "「停止程序并关闭」会结束这些进程；「只关闭窗口」会让它们继续在后台运行，"
                "之后可在「机器人 → 查看已有 bot…」里重新打开窗口或停止。"
            )
            yes = box.addButton("停止程序并关闭", stop_button)
            no = box.addButton("只关闭窗口", keep_button)
            box.addButton("取消", QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(no)
            box.exec()
            clicked = box.clickedButton()
            if clicked is None or clicked == box.button(QMessageBox.StandardButton.Cancel):
                return False
            if clicked is yes:
                for key in running_keys:
                    self.manager.stop(key, timeout_ms=self.stop_timeout_ms)

        self._remove_tab(bot_id)
        self.refresh_status()
        return True

    def _remove_tab(self, bot_id: str) -> bool:
        """关闭某个机器人的窗口：先记住布局偏好，再从实例区移除并销毁。

        关窗后要重算 ▸ 标记 —— 否则关掉的那个机器人的程序行会一直挂着 ▸
        （确认 1：标记只属于"当前窗口正在显示的程序"）。
        """
        tab = self._tabs.get(bot_id)
        if tab is None:
            return False
        self._save_pane_layout(bot_id, tab)
        self._save_split_state(bot_id)
        self._tabs.pop(bot_id, None)
        self.stack.removeWidget(tab)
        tab.setParent(None)
        tab.deleteLater()
        self._refresh_nav()
        self._apply_nav_focus_marker(notify=False)
        self._update_placeholder_visible()
        self._safe_sync_bot_tab_bar()
        return True

    def close_current_tab(self) -> None:
        """关闭当前窗口。"""
        bot_id = self._current_bot_id()
        if bot_id:
            self.close_bot_tab(bot_id)

    def close_all_windows(self, confirm: bool = False) -> int:
        """关闭全部窗口（默认不停程序），返回关闭的数量。"""
        if confirm and self._tabs:
            answer = QMessageBox.question(
                self,
                "关闭全部窗口",
                "将关闭 {} 个机器人窗口，程序继续在后台运行。\n\n确定吗？".format(len(self._tabs)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return 0
        count = 0
        for bot_id in list(self._tabs.keys()):
            if self._remove_tab(bot_id):
                count += 1
        self.refresh_status()
        return count

    def open_all_windows(self) -> int:
        """为配置里所有启用的机器人打开窗口（不启动程序），返回打开的数量。"""
        count = 0
        first = True
        for bot in self.config.bots:
            if not bot.enabled:
                continue
            if self.open_bot_tab(bot.id, focus=first) is not None:
                count += 1
                first = False
        self.statusBar().showMessage("已打开 {} 个机器人窗口（未启动程序）。".format(count), 5000)
        self.refresh_status()
        return count

    def _save_split_state(self, bot_id: str) -> None:
        tab = self._tabs.get(bot_id)
        if tab is None:
            return
        sizes = tab.split_sizes()
        if sizes:
            self._settings.setValue(SETTINGS_SPLIT_PREFIX + bot_id, sizes)

    # ------------------------------------------------------------------
    # Phase B：配置级操作（供「查看已有 bot」对话框调用）
    # ------------------------------------------------------------------

    def move_bot(self, bot_id: str, offset: int) -> bool:
        """调整机器人在配置中的顺序（offset 为 -1 上移、+1 下移）。

        顺序变化直接反映到左侧列表，并写回 bots_config.json。
        """
        index = self.config.index_of(bot_id)
        if index < 0:
            return False
        target = index + offset
        if target < 0 or target >= len(self.config.bots):
            return False
        if not self.config.move_bot(bot_id, offset):
            return False

        ok, message = self.config.save()
        bot = self.config.get_bot(bot_id)
        self.statusBar().showMessage(
            "「{}」已{}。".format(
                bot.name if bot is not None else bot_id,
                "上移" if offset < 0 else "下移",
            ) if ok else (message or "保存失败"),
            5000,
        )
        self._refresh_nav()
        self.refresh_status()
        return True

    def move_bot_up(self, bot_id: str) -> bool:
        """上移一个机器人在配置中的位置。"""
        return self.move_bot(bot_id, -1)

    def move_bot_down(self, bot_id: str) -> bool:
        """下移一个机器人在配置中的位置。"""
        return self.move_bot(bot_id, 1)

    def remove_bot(self, bot_id: str, ask: bool = True, stop_running: bool = True) -> bool:
        """从 bots_config.json 中删除一个机器人，并关闭它的窗口。

        - ask=True 时先确认（默认按钮为"否"）
        - stop_running=True 时同时停止它正在运行的程序
        """
        bot = self.config.get_bot(bot_id)
        if bot is None:
            return False

        tab = self._tabs.get(bot_id)
        running_keys: List[str] = []
        if tab is not None:
            running_keys = [key for key in tab.all_keys() if self.manager.is_running(key)]

        if ask:
            extra = ""
            if running_keys:
                extra = "\n\n它还有 {} 个程序在运行，将一并停止。".format(len(running_keys))
            answer = QMessageBox.question(
                self,
                "删除机器人",
                "确定要从 bots_config.json 中删除「{}」吗？{}".format(bot.name, extra),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False

        if stop_running:
            for key in running_keys:
                self.manager.stop(key, timeout_ms=self.stop_timeout_ms)

        self._remove_tab(bot_id)
        if not self.config.remove_bot(bot_id):
            return False
        ok, message = self.config.save()
        # 机器人没了，它的界面偏好（布局/比例/焦点/旧的 split 键）一并清掉
        self._forget_layout(bot_id)
        try:
            self._settings.remove(SETTINGS_SPLIT_PREFIX + bot_id)
            self._settings.sync()
        except (TypeError, ValueError):
            pass
        self._refresh_view_bot_actions()
        self._rebuild_layout_menu()
        self.statusBar().showMessage(
            "已删除「{}」{}".format(bot.name, "" if ok else "（{}）".format(message)), 6000
        )
        self.refresh_status()
        return True

    def open_bot_list_dialog(self) -> Optional[object]:
        """打开「查看已有 bot」对话框（Phase B）。

        对话框是一个不依赖已打开窗口的控制台：列出全部机器人及其程序状态，
        并支持打开窗口 / 启动 / 停止 / 重启 / 编辑 / 删除 / 调整顺序。
        """
        from app.ui.bot_list_dialog import BotListDialog

        dialog = BotListDialog(self, self)
        self._bot_list_dialog = dialog
        self.register_dialog(dialog)
        dialog.exec()
        self.refresh_status()
        return dialog

    # ------------------------------------------------------------------
    # 启动 / 停止 / 重启
    # ------------------------------------------------------------------

    def _find_program(self, program_id: str) -> Optional[Program]:
        """回调：按程序 id 在配置中查找 Program（供重启时读取最新配置）。"""
        for bot in self.config.bots:
            for program in bot.programs:
                if program.id == program_id:
                    return program
        return None

    def start_bot(self, bot_id: Optional[str], focus: bool = True) -> bool:
        """启动一个机器人的所有程序（自动创建 Tab）。"""
        if not bot_id:
            self.statusBar().showMessage("当前没有可启动的机器人，请先新建或选择。", 5000)
            return False
        bot = self.config.get_bot(bot_id)
        if bot is None:
            return False
        if not bot.enabled:
            self.statusBar().showMessage("机器人「{}」已被禁用。".format(bot.name), 5000)
            return False
        if not bot.programs:
            QMessageBox.information(self, "无法启动", "机器人「{}」没有配置任何程序。".format(bot.name))
            return False

        tab = self.open_bot_tab(bot_id, focus=focus)
        if tab is None:
            return False

        started = 0
        failed = 0
        for program in bot.programs:
            if not program.enabled:
                continue
            key = build_manager_key(bot.id, program.id)
            if self.manager.is_running(key):
                continue
            try:
                self.manager.start(
                    key, program, self.config.base_dir,
                    "{} / {}".format(bot.name, program.name),
                )
                started += 1
            except Exception as exc:
                # 单个程序出错不应影响同一机器人的其它程序
                failed += 1
                self._report_program_failure(bot, program, exc)

        tab.refresh_statuses()
        self._refresh_nav()
        if failed:
            self.statusBar().showMessage(
                "「{}」已发起启动 {} 个程序，{} 个失败（详见日志与 launcher_error.log）。".format(
                    bot.name, started, failed
                ),
                8000,
            )
        else:
            self.statusBar().showMessage(
                "已启动「{}」的 {} 个程序。".format(bot.name, started), 5000
            )
        self.refresh_status()
        return True

    def _report_program_failure(self, bot: Bot, program: Program, exc: BaseException) -> None:
        """报告单个程序启动失败：写日志窗口 + 控制台 + launcher_error.log。"""
        key = build_manager_key(bot.id, program.id)
        text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        try:
            self.manager.log_message.emit(
                key, "启动「{}」时发生异常：{}".format(program.name, exc)
            )
        except Exception:
            pass
        tab = self._tabs.get(bot.id)
        if tab is not None:
            tab.append_log(key, "[错误] 启动失败：{}\n".format(exc))
            tab.refresh_statuses()
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except Exception:
            pass
        try:
            log_path = PROJECT_ROOT / "launcher_error.log"
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write("\n" + "=" * 60 + "\n[启动 {} / {}]\n".format(
                    bot.name, program.name))
                handle.write(text)
        except Exception:
            pass

    def stop_bot(self, bot_id: Optional[str]) -> bool:
        """停止一个机器人的所有程序。"""
        if not bot_id:
            return False
        bot = self.config.get_bot(bot_id)
        if bot is None:
            return False
        stopped = 0
        failures: List[str] = []
        for program in reversed(list(bot.programs)):
            key = build_manager_key(bot.id, program.id)
            try:
                if self.manager.is_running(key) and self.manager.stop(
                    key, timeout_ms=self.stop_timeout_ms
                ):
                    stopped += 1
            except Exception as exc:
                failures.append("{}：{}".format(program.name, exc))
        tab = self._tabs.get(bot_id)
        if tab is not None:
            tab.refresh_statuses()
        self._refresh_nav()
        if failures:
            self.statusBar().showMessage(
                "停止「{}」时有 {} 个程序报错：{}".format(
                    bot.name, len(failures), "；".join(failures)
                ),
                10000,
            )
        else:
            self.statusBar().showMessage(
                "正在停止「{}」的 {} 个程序…".format(bot.name, stopped), 5000
            )
        self.refresh_status()
        return stopped > 0

    def restart_bot(self, bot_id: Optional[str]) -> bool:
        """重启一个机器人的所有程序。"""
        if not bot_id:
            return False
        bot = self.config.get_bot(bot_id)
        if bot is None:
            return False

        tab = self.open_bot_tab(bot_id, focus=True)
        failed = 0
        for program in bot.programs:
            if not program.enabled:
                continue
            key = build_manager_key(bot.id, program.id)
            try:
                if self.manager.has(key):
                    self.manager.restart(key, timeout_ms=self.stop_timeout_ms)
                else:
                    self.manager.start(
                        key, program, self.config.base_dir,
                        "{} / {}".format(bot.name, program.name),
                    )
            except Exception as exc:
                failed += 1
                self._report_program_failure(bot, program, exc)
        if tab is not None:
            tab.refresh_statuses()
        self.statusBar().showMessage(
            "正在重启「{}」…{}".format(
                bot.name, "（{} 个程序报错，见日志）".format(failed) if failed else ""
            ),
            5000,
        )
        self.refresh_status()
        return True

    def start_all_bots(self) -> int:
        """按设定间隔依次启动所有启用的机器人。"""
        targets = [bot for bot in self.config.bots if bot.enabled and bot.programs]
        if not targets:
            QMessageBox.information(self, "没有可启动的机器人", "配置中没有启用的机器人。")
            return 0

        self._starting_queue = [bot.id for bot in targets]
        # 先全部打开 Tab，这样启动日志立刻可见
        for index, bot in enumerate(targets):
            try:
                self.open_bot_tab(bot.id, focus=(index == 0))
            except Exception as exc:
                self.statusBar().showMessage(
                    "打开「{}」的标签页失败：{}".format(bot.name, exc), 8000
                )

        interval_ms = int(max(0.0, float(self.start_interval)) * 1000)
        if self._start_timer is None:
            self._start_timer = QTimer(self)
            self._start_timer.setSingleShot(True)
            self._start_timer.timeout.connect(self._start_next_queued)
        self._start_timer.start(max(0, interval_ms))
        self.statusBar().showMessage(
            "开始依次启动 {} 个机器人（间隔 {:.1f} 秒）…".format(len(targets), self.start_interval),
            8000,
        )
        return len(targets)

    def _start_next_queued(self) -> None:
        """启动队列中的下一个机器人，并按间隔安排后续。

        即使某个机器人启动时抛异常，也必须继续处理队列里剩下的机器人，
        否则「启动全部」会在第一个出错处就停住。
        """
        if not self._starting_queue:
            return
        bot_id = self._starting_queue.pop(0)
        try:
            self.start_bot(bot_id, focus=False)
        except Exception as exc:
            bot = self.config.get_bot(bot_id)
            name = bot.name if bot is not None else bot_id
            self.statusBar().showMessage("启动「{}」失败：{}".format(name, exc), 10000)
            try:
                sys.stderr.write(
                    "启动 {} 时发生异常：{}\n".format(name, traceback.format_exc())
                )
            except Exception:
                pass
        if self._starting_queue:
            interval_ms = int(max(0.0, float(self.start_interval)) * 1000)
            assert self._start_timer is not None
            self._start_timer.start(max(0, interval_ms))
        else:
            self.statusBar().showMessage("全部机器人已发出启动请求。", 5000)

    def stop_all_bots(self) -> int:
        """停止所有正在运行的程序。"""
        self._starting_queue = []
        running = self.manager.running_keys()
        if not running:
            self.statusBar().showMessage("当前没有正在运行的程序。", 4000)
            return 0
        for key in running:
            self.manager.stop(key, timeout_ms=self.stop_timeout_ms)
        for tab in self._tabs.values():
            tab.refresh_statuses()
        self._refresh_nav()
        self.statusBar().showMessage("正在停止 {} 个程序…".format(len(running)), 5000)
        self.refresh_status()
        return len(running)

    def build_current_bot_menu(self) -> QMenu:
        """「当前 Bot ▾」菜单：对当前机器人 / 对当前程序 的两组操作。

        真机需求（2026-10-05）：把顶部那三个按钮（启动/停止/重启当前 Bot）**合并成一个下拉**，
        下拉里既有"对当前 Bot 实例"的三个操作，也有"对当前程序"的三个操作。
        所以菜单每次展开时重建（标题要显示当前机器人名与当前程序名，能不能点也要现算）。

        复用已有的 QAction（`action_start_bot` 等）：快捷键、机器人菜单里的入口
        都还指着它们，行为只有一份。
        """
        menu = QMenu(self)
        tab = self.current_tab()
        bot = self.config.get_bot(tab.bot_id) if tab is not None else None

        head = QAction("对当前 Bot：{}".format(bot.name if bot is not None else "（没有打开的窗口）"),
                       menu)
        head.setEnabled(False)
        menu.addAction(head)
        for action in (self.action_start_bot, self.action_stop_bot, self.action_restart_bot):
            action.setEnabled(bot is not None)
            menu.addAction(action)

        menu.addSeparator()
        key = tab.focused_program() if tab is not None else ""
        program = None
        if key and bot is not None:
            for item in getattr(bot, "programs", []):
                if build_manager_key(bot.id, item.id) == key:
                    program = item
                    break
        sub_head = QAction("对当前程序：{}".format(
            program.name if program is not None else
            ("（这个机器人只有一个程序时不显示窗格名）" if bot is not None else "（没有打开的窗口）")),
            menu)
        sub_head.setEnabled(False)
        menu.addAction(sub_head)
        for label, name in (("启动", "start"), ("停止", "stop"), ("重启", "restart")):
            item = QAction(label, menu)
            item.setEnabled(bool(key))
            item.triggered.connect(
                lambda _checked=False, k=key, a=name: self._on_tab_program_action(k, a))
            menu.addAction(item)
        return menu

    def _on_current_bot_button(self) -> None:
        """顶部「当前 Bot ▾」：弹出上面那份菜单。"""
        button = getattr(self, "current_bot_button", None)
        if button is None:
            return
        self.build_current_bot_menu().exec(
            button.mapToGlobal(button.rect().bottomLeft()))

    def build_start_selection_menu(self) -> "StartSelectionMenu":
        """「启动全部 ▾」的树形勾选菜单（见 app/ui/start_menu.py）。"""
        from app.ui.start_menu import StartSelectionMenu

        bots = list(getattr(self.config, "bots", []) or [])
        menu = StartSelectionMenu(bots, current_bot_id=self._current_bot_id() or "", parent=self)
        # 选完复用 _on_tab_start_requested：启动路径只有一条，别写第二份
        menu.startRequested.connect(self._on_tab_start_requested)
        return menu

    def _on_start_pick_button(self) -> None:
        """「启动全部」右边的 ▾：弹出勾选菜单（每次现建，默认勾选跟着当前机器人走）。

        和窗格标题栏的「布局 ▾」一样手动 exec —— 不用 QToolButton.setMenu()，
        因为那样 Qt 会自己在按钮里画一个小箭头（真机反馈过三次"多余的勾"）。
        """
        button = getattr(self, "start_pick_button", None)
        if button is None:
            return
        try:
            menu = self.build_start_selection_menu()
        except (RuntimeError, AttributeError, TypeError) as exc:
            self.statusBar().showMessage("启动选择菜单打不开：{}".format(exc), 8000)
            return
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _on_start_menu_requested(self, bot_id: str = "", global_pos=None) -> None:
        """弹出「启动全部 ▾」的勾选菜单（默认只勾当前机器人）。"""
        bots = list(getattr(self.config, "bots", []) or [])
        if not bots:
            self.statusBar().showMessage("还没有配置任何机器人。", 6000)
            return
        menu = self.build_start_selection_menu()
        if global_pos is None:
            button = getattr(self, "start_all_button", None)
            global_pos = (button.mapToGlobal(button.rect().bottomLeft())
                          if button is not None else None)
        if global_pos is None:
            return
        menu.exec(global_pos)

    def _on_tab_start_requested(self, keys: List[str]) -> None:
        """Tab 内点击「启动」：按 key 反查程序并启动。"""
        for key in keys:
            bot_id, program = self._resolve_key(key)
            if bot_id is None or program is None:
                continue
            if self.manager.is_running(key):
                continue
            bot = self.config.get_bot(bot_id)
            name = "{} / {}".format(bot.name if bot else bot_id, program.name)
            try:
                self.manager.start(key, program, self.config.base_dir, name)
            except Exception as exc:
                if bot is not None:
                    self._report_program_failure(bot, program, exc)
                else:
                    self.statusBar().showMessage("启动失败：{}".format(exc), 8000)
        self.refresh_status()

    def _on_tab_stop_requested(self, keys: List[str]) -> None:
        for key in keys:
            try:
                self.manager.stop(key, timeout_ms=self.stop_timeout_ms)
            except Exception as exc:
                self.statusBar().showMessage("停止失败：{}".format(exc), 8000)
        self.refresh_status()

    def _on_tab_restart_requested(self, keys: List[str]) -> None:
        for key in keys:
            bot_id, program = self._resolve_key(key)
            if bot_id is None or program is None:
                continue
            bot = self.config.get_bot(bot_id)
            name = "{} / {}".format(bot.name if bot else bot_id, program.name)
            try:
                if self.manager.has(key):
                    self.manager.restart(key, timeout_ms=self.stop_timeout_ms)
                else:
                    self.manager.start(key, program, self.config.base_dir, name)
            except Exception as exc:
                if bot is not None:
                    self._report_program_failure(bot, program, exc)
                else:
                    self.statusBar().showMessage("重启失败：{}".format(exc), 8000)
        self.refresh_status()

    def _resolve_key(self, key: str):
        """key -> (bot_id, Program)；找不到返回 (None, None)。"""
        for bot in self.config.bots:
            for program in bot.programs:
                if build_manager_key(bot.id, program.id) == key:
                    return bot.id, program
        return None, None

    # ------------------------------------------------------------------
    # 配置编辑
    # ------------------------------------------------------------------

    def new_bot(self) -> None:
        """新建机器人：对话框内直接写入 bots_config.json。"""
        dialog = EditBotDialog(self, self.config, None)
        self.register_dialog(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_layout_from_dialog(dialog)
        # 对话框内已经落盘，这里重新读取以拿到最终状态
        self._reload_config_in_place()
        self._refresh_view_bot_actions()
        self.statusBar().showMessage("已新建机器人，可在工具栏点击「启动当前 Bot」。", 6000)
        self.refresh_status()

    def _apply_layout_from_dialog(self, dialog: "EditBotDialog") -> bool:
        """把编辑对话框里选的「布局模板」立即套到已打开的窗口上（N4）。

        布局写在 QSettings（`pane/layout/<bot_id>`），与「视图 → 分屏布局」
        菜单共用一份记忆；这里只做**实时生效**：窗口已打开就直接换布局，
        没打开则什么都不用做（下次打开时自然按新模板构建）。

        返回是否真的套用了（用于状态栏提示与自检）。
        """
        try:
            if not dialog.layout_template_applied():
                return False
            bot_id = dialog.bot_id()
            kind = dialog.selected_layout_kind()
        except (AttributeError, TypeError):
            return False
        if not bot_id:
            return False

        # 写记忆（空串 = 回到「自动」，交给 _forget_layout 清理）
        if kind:
            self._remember_layout_kind(bot_id, kind)
        else:
            self._forget_layout(bot_id)

        tab = self._tabs.get(bot_id)
        if tab is None:
            self._rebuild_layout_menu()
            return False
        try:
            if kind:
                changed = tab.set_layout(kind, notify=False)
            else:
                changed = tab.reset_layout()
        except (AttributeError, TypeError, RuntimeError):
            changed = False
        self._rebuild_layout_menu()
        self._schedule_nav_refresh(bot_id)
        return bool(changed)

    def edit_bot(self, bot_id: Optional[str]) -> None:
        """编辑当前机器人。"""
        if not bot_id:
            self.statusBar().showMessage("请先打开或选择一个机器人。", 4000)
            return
        bot = self.config.get_bot(bot_id)
        if bot is None:
            self.statusBar().showMessage("找不到机器人：{}".format(bot_id), 4000)
            return

        tab = self._tabs.get(bot_id)
        if tab is not None:
            self._save_split_state(bot_id)

        dialog = EditBotDialog(self, self.config, bot)
        self.register_dialog(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        layout_changed = self._apply_layout_from_dialog(dialog)
        self._reload_config_in_place()
        new_bot = self.config.get_bot(bot_id)
        tab = self._tabs.get(bot_id)
        if tab is not None and new_bot is not None:
            if any(self.manager.is_running(key) for key in tab.all_keys()):
                self.statusBar().showMessage("配置已保存；已运行的进程需要重启后才会应用新配置。", 8000)
            tab.apply_bot(new_bot)
            self._refresh_nav()
        if layout_changed:
            self.statusBar().showMessage(
                "配置已保存，布局模板已切换为「{}」。".format(
                    layout_model.LAYOUT_KIND_LABELS.get(
                        dialog.selected_layout_kind(), dialog.selected_layout_kind()
                    )
                    if dialog.selected_layout_kind()
                    else "自动"
                ),
                6000,
            )
        else:
            self.statusBar().showMessage("配置已保存到 {}".format(self.config.path), 6000)
        self.refresh_status()

    def _reload_config_in_place(self) -> None:
        """重新从磁盘读取配置，并把结果同步回已打开的窗口与左侧列表。"""
        reloaded = BotConfig.load(self.config.path, create_if_missing=True)
        self.config = reloaded
        for bot_id, tab in list(self._tabs.items()):
            bot = reloaded.get_bot(bot_id)
            if bot is None:
                # 机器人被删除：关闭对应窗口
                self._remove_tab(bot_id)
                continue
            tab.config = reloaded
            tab.apply_bot(bot)
        for message in reloaded.load_warnings:
            self.statusBar().showMessage(message, 8000)
        self._refresh_nav()
        self._refresh_view_bot_actions()
        self.refresh_status()

    def reload_config(self) -> None:
        """手动重新载入配置。"""
        self._reload_config_in_place()
        self.statusBar().showMessage("已重新载入 {}".format(self.config.path), 5000)

    def open_config_file(self) -> None:
        """用系统默认程序打开 bots_config.json。"""
        path = Path(self.config.path)
        if not path.exists():
            _ok, _message = self.config.save()
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as exc:  # pragma: no cover - 取决于系统关联
            QMessageBox.warning(self, "无法打开", "打开 {} 失败：{}".format(path, exc))
            return
        self.statusBar().showMessage("已请求打开 {}".format(path), 5000)

    def open_directory(self, path: str) -> None:
        """打开一个目录（由 BotTab 请求）。"""
        if not path or not os.path.isdir(path):
            self.statusBar().showMessage("目录不存在：{}".format(path), 5000)
            return
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:
            pass

    def show_about(self) -> None:
        """关于对话框。"""
        QMessageBox.information(
            self,
            "关于",
            "{}\n\n基于 PyQt6 + QProcess 的 QQ 机器人启动管理器。\n"
            "配置文件：{}\n"
            "命令中的 [LATEST_JAR] 会替换为工作目录下最新的 jar 包；\n"
            "停止进程时使用 taskkill /T 结束整棵进程树。".format(APP_NAME, self.config.path),
        )

    # ------------------------------------------------------------------
    # QSettings
    # ------------------------------------------------------------------

    def _restore_settings(self) -> None:
        geometry = self._settings.value(SETTINGS_GEOMETRY)
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
            except (TypeError, ValueError):
                pass
        state = self._settings.value(SETTINGS_WINDOW_STATE)
        if state is not None:
            try:
                self.restoreState(state)
            except (TypeError, ValueError):
                pass

    def _restore_open_tabs(self) -> None:
        """恢复上次打开的窗口（不自动启动程序）。

        优先使用上次的"当前机器人 id"，缺失时兼容旧版本保存的 Tab 序号。
        """
        raw = self._settings.value(SETTINGS_OPEN_BOTS, [])
        if isinstance(raw, str):
            bot_ids = [item for item in raw.split(",") if item]
        elif isinstance(raw, (list, tuple)):
            bot_ids = [str(item) for item in raw]
        else:
            bot_ids = []

        opened = 0
        for bot_id in bot_ids:
            if self.config.get_bot(bot_id) is None:
                continue
            if self.open_bot_tab(bot_id, focus=False) is not None:
                opened += 1

        # 决定当前显示哪个窗口
        current = str(self._settings.value(SETTINGS_CURRENT_BOT, "") or "")
        if current not in self._tabs:
            legacy_index = int(self._settings.value(SETTINGS_CURRENT_TAB, 0) or 0)
            restored = list(self._tabs.keys())
            if 0 <= legacy_index < len(restored):
                current = restored[legacy_index]
            elif restored:
                current = restored[0]
            else:
                current = ""

        if current in self._tabs:
            self.stack.setCurrentWidget(self._tabs[current])
        self._update_placeholder_visible()
        self._refresh_nav()
        # R1-1：恢复完窗口后，把橙色落到当前机器人正在看的程序行
        # （上一行 _refresh_nav 会重建整棵树，所以这里必须放在它之后）
        if current in self._tabs:
            self._select_nav_for_bot(current)
        self._refresh_view_bot_actions()
        self._maybe_show_first_run_hint()

        if opened:
            self.statusBar().showMessage(
                "已恢复 {} 个机器人窗口（未自动启动程序）。".format(opened), 6000
            )
        self.refresh_status()

    def _save_settings(self) -> None:
        for bot_id in list(self._tabs.keys()):
            self._save_split_state(bot_id)
        # 同步写入新的布局偏好键（模板 / 比例 / 焦点）
        for bot_id, tab in list(self._tabs.items()):
            try:
                self._save_pane_layout(bot_id, tab)
            except (RuntimeError, AttributeError):
                continue
        current = self._current_bot_id() or ""
        self._settings.setValue(SETTINGS_GEOMETRY, self.saveGeometry())
        self._settings.setValue(SETTINGS_WINDOW_STATE, self.saveState())
        self._settings.setValue(SETTINGS_CURRENT_BOT, current)
        self._settings.setValue(SETTINGS_CURRENT_TAB, int(self.stack.currentIndex()))
        self._settings.setValue(SETTINGS_OPEN_BOTS, list(self._tabs.keys()))
        # N5：只在"左栏可见"时记宽度。折叠状态下 sizes() 是 [0, W]，
        # 存进去会让下次启动的左栏变成一条缝（就是"左侧栏没恢复"的元凶）。
        if not self._nav_collapsed and self.central_splitter is not None:
            try:
                sizes = list(self.central_splitter.sizes())
                if sizes and sizes[0] >= MIN_NAV_WIDTH:
                    self._settings.setValue(SETTINGS_NAV_SPLIT, sizes)
            except (RuntimeError, TypeError):
                pass
        self._settings.setValue(SETTINGS_NAV_COLLAPSED, bool(self._nav_collapsed))
        self._save_nav_expanded()
        self._settings.setValue(SETTINGS_START_INTERVAL, float(self.start_interval))
        self._settings.setValue(SETTINGS_STOP_TIMEOUT, float(self.stop_timeout))
        self._settings.setValue(SETTINGS_LOG_LINES, int(self.log_max_lines))
        self._settings.sync()

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """关闭窗口：停止所有进程并保存界面状态。

        两种收摊方式（由「运行参数 → 关闭管理器时强制停止所有程序」决定）：

        · **不勾选**（默认）：先弹确认框，再按"停止超时"等程序自行退出，
          超时才 taskkill /T /F。稳妥，但遇到不响应关闭请求的程序（.NET、
          控制台宿主）会白等好几秒。
        · **勾选**（真机需求"关闭管理器时自动强制停止所有正在运行的程序"）：
          **不询问**，直接 `taskkill /T /F` 结束整棵进程树并就地兜底，
          关窗干净利落；适合"我关管理器就是要它全停"的用法。
        """
        running = self.manager.running_count
        force_close = bool(getattr(self, "_force_stop_on_close", False))
        if running and not self._closing and not force_close:
            answer = QMessageBox.question(
                self,
                "退出确认",
                "仍有 {} 个程序在运行。\n\n退出将停止它们，确定要退出吗？".format(running),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._closing = True
        self._starting_queue = []
        if self._start_timer is not None:
            self._start_timer.stop()

        self._close_log("关闭流程开始：running={}".format(running))
        try:
            self._save_settings()
        except Exception:
            pass
        self._close_log("界面状态已保存")

        if self.manager.running_count:
            if force_close:
                self.statusBar().showMessage("正在强制停止所有程序…")
                self._close_log("强制停止模式：跳过确认与优雅等待")
            else:
                self.statusBar().showMessage("正在停止所有程序…")
            QApplication.processEvents()
            # 强制模式：跳过优雅停止（force=True），超时压到 1.5 秒兜底
            self.manager.stop_all(
                timeout_ms=1500 if force_close else 3000,
                wait=True,
                force=force_close,
            )
            self._close_log("stop_all 返回（force={}），仍在运行={}".format(
                force_close, self.manager.running_count))

        self.manager.cleanup()
        self._close_log("cleanup 完成，准备关闭窗口")
        event.accept()

    def _close_log(self, message: str) -> None:
        """记录关闭流程的每一步（排查"关窗口卡住"用）。

        真机反馈过"关闭提权管理器时卡顿"。关闭流程里有两次同步等待
        （stop_all 3 秒 + cleanup 2 秒），最坏约 5 秒；把每一步带上时间戳写进
        close_debug.log，就能区分"只是慢"与"真卡死"。

        默认开启；设环境变量 QQBOT_CLOSE_DEBUG=0 可关闭。
        """
        if os.environ.get("QQBOT_CLOSE_DEBUG", "1") in ("0", "false", "no"):
            return
        try:
            from datetime import datetime

            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with open(Path(PROJECT_ROOT) / "close_debug.log", "a",
                      encoding="utf-8") as handle:
                handle.write("{}  {}\n".format(stamp, message))
        except (OSError, ValueError, TypeError):
            pass


# ---------------------------------------------------------------------------
# 自检：可用 QT_QPA_PLATFORM=offscreen 无界面运行
# ---------------------------------------------------------------------------

def _demo_config(path: Optional[str] = None) -> BotConfig:
    """构造一个用于自检的配置对象。"""
    from app.config import default_config_dict

    target = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_selftest_bots_config.json"
    )
    config = BotConfig.from_dict(default_config_dict(), path=target)
    return config


def _selftest() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    config = _demo_config()
    config.save()

    window = MainWindow(config)
    window.show()

    # 1) 初始状态：只有占位页，左侧列表已按配置生成
    print("[1] 已打开窗口 =", len(window._tabs),
          "| 实例区页数 =", window.stack.count(),
          "| 左侧节点 =", window.nav_tree.topLevelItemCount(),
          "| 机器人 =", len(config.bots))
    assert len(window._tabs) == 0
    assert window.stack.count() == 1
    assert window.nav_tree.topLevelItemCount() == len(config.bots)
    assert window.stack.currentWidget() is window.placeholder_page

    bot = config.bots[0]
    # 2) 打开窗口（不启动进程）
    tab = window.open_bot_tab(bot.id)
    assert tab is not None
    print("[2] 当前 bot =", window._current_bot_id(),
          "| 实例区页数 =", window.stack.count(),
          "| 视图数 =", len(tab.all_keys()))
    assert len(window._tabs) == 1
    assert window.stack.count() == 2
    assert window.stack.currentWidget() is tab
    assert tab.bot_id == bot.id
    assert window._nav_selected_bot_id() == bot.id, "左侧应同步选中"

    # 3) 日志写入与状态刷新
    key = tab.all_keys()[0]
    tab.append_log(key, "[自检] 主程序输出\n")
    window.refresh_status()
    print("[3] 状态栏 =", window.status_label.text())
    assert "运行中的程序" in window.status_label.text()

    # 4) 分割比例与左侧列表刷新
    tab.set_split_sizes([420, 210])
    window._save_split_state(bot.id)
    window._refresh_nav()
    nav_item = window._nav_items[bot.id]
    print("[4] 左侧节点文字 =", nav_item.text(0),
          "| 子节点 =", nav_item.childCount(),
          "| 提示首行 =", nav_item.toolTip(0).splitlines()[0])
    assert nav_item.childCount() == len(bot.programs)

    # 5) 单程序机器人仍然只有一个日志视图（由 BotTab 决定布局）
    single = Bot(name="单程序机器人", qq="1")
    single.add_program()
    config.add_bot(single)
    single_tab = window.open_bot_tab(single.id)
    assert single_tab is not None and len(single_tab.all_keys()) == 1
    window._refresh_nav()
    print("[5] 单程序窗口视图数 =", len(single_tab.all_keys()),
          "| 左侧节点 =", window._nav_items[single.id].text(0))
    assert window._nav_items[single.id].childCount() == 1

    # 6) 关闭窗口（无进程时不会弹确认），占位页恢复
    window.close_bot_window(single.id, confirm=False)
    print("[6] 关闭后窗口数 =", len(window._tabs),
          "| 实例区页数 =", window.stack.count())
    assert len(window._tabs) == 1
    assert window.stack.count() == 2
    # 修正（N5）：列表是**按配置**列出所有机器人的，关掉窗口后节点仍应在 ——
    # 只是"已打开"标记消失。旧断言 `single.id not in _nav_items` 是"列表只显示
    # 已打开机器人"时代的遗留，它让自检从第 6 组起就中断（后面的断言从未执行）。
    assert single.id in window._nav_items, "按配置列出的机器人节点不应因关窗而消失"
    closed_item = window._nav_items[single.id]
    assert bool(closed_item.data(0, ROLE_NAV_OPENED)) is False, "关窗后应清除「已打开」标记"
    assert NAV_OPEN_MARK not in closed_item.text(0), closed_item.text(0)
    print("    关窗后节点仍在、已打开标记已清除 =", repr(closed_item.text(0)))

    # 7) QSettings 读写
    window._save_settings()
    saved_open = window._settings.value(SETTINGS_OPEN_BOTS, [])
    print("[7] QSettings open/bots =", saved_open,
          "| current_bot =", window._settings.value(SETTINGS_CURRENT_BOT),
          "| nav/split =", window._settings.value(SETTINGS_NAV_SPLIT))
    assert saved_open
    assert window._settings.value(SETTINGS_CURRENT_BOT) == bot.id

    # 8) Phase A：Bot 状态公共层
    print("\n[8] Phase A：bot 状态公共层")
    status = window.bot_status(bot)
    print("    初始        :", format_bot_status(status))
    print("    富文本      :", format_bot_status_rich(status, with_opened=True))
    assert status.total == len(bot.programs)
    assert status.enabled_total == len(bot.programs)
    assert status.running == 0 and status.failed == 0
    assert status.status == BotStatusFlags.STOPPED
    assert status.opened is True, "该 bot 的窗口已打开"
    assert status.counter_text == "0/{}".format(len(bot.programs))

    # 用真实长命令占位程序，验证 status 的计数与归类
    running_bot = Bot(name="状态测试机器人", qq="2")
    program_a = running_bot.add_program()
    program_a.name = "测试程序A"
    program_a.role = ROLE_PRIMARY
    program_a.command = "cmd.exe /c ping -n 60 127.0.0.1"
    program_b = running_bot.add_program()
    program_b.name = "测试程序B"
    program_b.role = "secondary"
    program_b.command = "cmd.exe /c ping -n 60 127.0.0.1"
    config.add_bot(running_bot)

    key_a = build_manager_key(running_bot.id, program_a.id)
    key_b = build_manager_key(running_bot.id, program_b.id)
    window.manager.start(key_a, program_a, config.base_dir, "状态测试机器人 / A")
    window.manager.start(key_b, program_b, config.base_dir, "状态测试机器人 / B")
    for _ in range(400):
        app.processEvents()
        if window.manager.is_running(key_a) and window.manager.is_running(key_b):
            break
        QApplication.processEvents()

    status_running = window.bot_status(running_bot)
    print("    全部运行    :", format_bot_status(status_running))
    print("    段落摘要    :", format_program_status_line(status_running, key_a),
          "|", format_program_status_line(status_running, key_b))
    assert status_running.running == 2, status_running.running
    assert status_running.all_running
    assert status_running.status == BotStatusFlags.RUNNING
    assert status_running.opened is False

    window.manager.stop(key_b, timeout_ms=1000)
    for _ in range(200):
        app.processEvents()
        if not window.manager.is_running(key_b):
            break
        QApplication.processEvents()
    # 修正（自检自身的 bug）：stop() 之后状态会先经过「停止中…」，
    # 而这里断言的是**终态** PARTIAL。以前只等 is_running 变 False（那一刻仍是
    # 「停止中」）就断言，于是偶发失败并让自检从第 8 组起中断。
    # 现在额外等到"该程序确实已不在 starting/stopping"再断言。
    for _ in range(400):
        app.processEvents()
        if window.manager.state(key_b) not in (
            window.manager.STATE_STARTING, window.manager.STATE_STOPPING
        ):
            break
        QApplication.processEvents()
    status_partial = window.bot_status(running_bot)
    print("    停一个后    :", format_bot_status(status_partial),
          "| B 的 state =", window.manager.state(key_b))
    assert status_partial.running == 1, status_partial.running
    assert status_partial.status == BotStatusFlags.PARTIAL, (
        "一个运行 + 一个已停止 应为 PARTIAL，实际 status={} state_B={}".format(
            status_partial.status, window.manager.state(key_b)
        )
    )

    # 禁用程序不计入"应运行数"
    program_b.enabled = False
    status_disabled_program = window.bot_status(running_bot)
    print("    禁用B后     :", format_bot_status(status_disabled_program))
    assert status_disabled_program.enabled_total == 1
    assert status_disabled_program.all_running

    # 整个 bot 被禁用
    running_bot.enabled = False
    status_disabled_bot = window.bot_status(running_bot)
    print("    整个禁用    :", format_bot_status(status_disabled_bot))
    assert status_disabled_bot.status == BotStatusFlags.DISABLED
    running_bot.enabled = True

    # 提示文本与列表接口
    print("    悬停提示    :")
    for line in window.bot_status_tooltip(running_bot).splitlines():
        print("      " + line)
    all_status = window.bot_status_list()
    print("    全部机器人  :", [(item.name, item.status) for item in all_status])
    assert len(all_status) == len(config.bots)
    assert window.bot_status_of("不存在的id") is None

    # 清理：停止占位进程并移除测试机器人
    window.manager.stop(key_a, timeout_ms=1000)
    for _ in range(200):
        app.processEvents()
        if not window.manager.is_running(key_a):
            break
        QApplication.processEvents()
    window.manager.cleanup()
    config.remove_bot(running_bot.id)

    # 9) Phase B：查看已有 bot 对话框
    print("\n[9] Phase B：查看已有 bot 对话框")
    from app.ui.bot_list_dialog import BotListDialog

    dialog = BotListDialog(window, window, auto_refresh=False)
    dialog.show()
    print("    表格行数 =", dialog.table.rowCount(),
          "| 可见 =", dialog.visible_bot_ids())
    assert dialog.table.rowCount() == len(config.bots)
    assert set(dialog.visible_bot_ids()) == {item.id for item in config.bots}

    # 9.1 打开窗口 / 关闭窗口（不停止进程）
    target_bot = config.bots[0]
    window.close_all_windows()
    assert window.open_bot_tab(target_bot.id, True) is not None
    dialog.refresh()
    row = dialog.visible_bot_ids().index(target_bot.id)
    print("    窗口列 =", dialog.table.item(row, 4).text())
    assert dialog.table.item(row, 4).text() == "已打开"
    assert window.close_bot_window(target_bot.id, confirm=False) is True
    assert window.tab_for(target_bot.id) is None
    dialog.refresh()
    assert dialog.table.item(row, 4).text() == "已关闭"

    # 9.2 移动顺序 + 删除（跳过确认框）
    first_id = config.bots[0].id
    second_id = config.bots[1].id
    assert window.move_bot_down(first_id) is True
    assert config.bots[0].id == second_id, [b.id for b in config.bots]
    assert window.move_bot_up(first_id) is True
    assert config.bots[0].id == first_id

    extra = Bot(name="待删除机器人", qq="9")
    extra.add_program()
    config.add_bot(extra)
    config.save()
    dialog.refresh()
    before = dialog.table.rowCount()
    assert window.remove_bot(extra.id, ask=False) is True
    assert config.get_bot(extra.id) is None
    dialog.refresh()
    print("    删除前后行数 =", before, "->", dialog.table.rowCount())
    assert dialog.table.rowCount() == before - 1

    # 9.3 对话框动作分发到宿主（关闭窗口类的动作不会弹确认）
    window.open_bot_tab(target_bot.id, True)
    dialog.refresh()
    dialog.select_bot(target_bot.id)
    dialog._do("open")
    dialog._do("close_window")
    assert window.tab_for(target_bot.id) is None
    dialog.close()

    # 10) Phase C：左侧竖栏导航 + 右侧实例区
    print("\n[10] Phase C：左侧竖栏导航")
    window.close_all_windows()
    assert len(window._tabs) == 0
    assert window.stack.currentWidget() is window.placeholder_page
    assert window.nav_tree.topLevelItemCount() == len(config.bots)

    # 10.1 左侧列表结构：bot 节点 + 程序子节点（★ 标主程序）
    first_status = window.bot_status_list()[0]
    first_item = window._nav_items[first_status.bot_id]
    child_texts = [first_item.child(index).text(0) for index in range(first_item.childCount())]
    print("    bot 节点 =", first_item.text(0))
    for text in child_texts:
        print("      子节点 =", text)
    assert first_item.childCount() == first_status.total
    assert child_texts[0].startswith("★"), child_texts[0]
    assert first_item.toolTip(0)
    assert first_item.child(0).data(0, ROLE_NAV_PROGRAM_KEY)

    # 10.2 单击节点 = 打开窗口；双击 = 只展开/折叠（2026-10 起职责分离）
    #      单击路径就是用户实际走的那条：currentItemChanged -> _on_nav_current_changed
    #      （注意处理函数把落位动作延后到事件循环，所以要跑一次循环再断言）
    window._on_nav_current_changed(first_item.child(0), None)
    loop_nav = QEventLoop()
    QTimer.singleShot(80, loop_nav.quit)
    loop_nav.exec()
    assert window._current_bot_id() == first_status.bot_id, window._current_bot_id()
    assert window._nav_selected_bot_id() == first_status.bot_id
    print("    单击打开后当前 bot =", window._current_bot_id(),
          "| 已打开窗口 =", len(window._tabs))

    # 双击只切换展开状态，不再打开窗口
    expanded_before = first_item.isExpanded()
    window._on_nav_double_clicked(first_item, 0)
    assert first_item.isExpanded() is not expanded_before, "双击机器人行应切换展开状态"
    window._on_nav_double_clicked(first_item, 0)
    assert first_item.isExpanded() is expanded_before, "再双击应还原"
    # 程序行没有子项：双击不应改变展开状态，也不应打开/关闭窗口
    tabs_before_dbl = len(window._tabs)
    window._on_nav_double_clicked(first_item.child(0), 0)
    assert len(window._tabs) == tabs_before_dbl, "双击程序行不应开/关窗口"
    print("    双击机器人行：展开状态可切换；双击程序行：无副作用 = True")

    # 10.3 折叠/展开左侧列表
    assert window.toggle_nav(False) is False
    assert window.nav_panel.isVisible() is False
    assert window.toggle_nav(True) is True
    assert window.nav_panel.isVisible() is True
    print("    折叠状态（已保存） =", window._settings.value(SETTINGS_NAV_COLLAPSED))

    # 10.3.1 N5：左栏状态要能跨"关闭→重开"还原，且折叠时不许存宽度 0
    print("\n[10.3.1] 左侧栏状态持久化（N5）")
    key_split = SETTINGS_NAV_SPLIT
    key_collapsed = SETTINGS_NAV_COLLAPSED
    keep_split = window._settings.value(key_split)

    # ① tests 前置：settings_bool 必须能识破"字符串 false"（真机 bug 的根因）
    assert settings_bool("false") is False
    assert settings_bool("true") is True
    assert settings_bool("False") is False
    assert settings_bool("0") is False
    assert settings_bool("1") is True
    assert settings_bool("") is False
    assert settings_bool(False) is False
    assert settings_bool(True) is True
    assert settings_bool(None, True) is True
    assert settings_bool(None, False) is False
    assert bool("false") is True, "Python 原生语义：非空字符串恒为真（这正是坑）"
    print("    settings_bool('false') =", settings_bool("false"),
          "（而 bool('false') =", bool("false"), "）")

    # ① 展开状态：宽度大于 0 且落在 [MIN, MAX] 内
    window.toggle_nav(True)
    assert window.nav_panel.isVisible() is True
    assert not settings_bool(window._settings.value(key_collapsed, False), False)
    width_now = window._saved_nav_width()
    print("    展开时记录的宽度 =", width_now)
    assert MIN_NAV_WIDTH <= width_now <= MAX_NAV_WIDTH, width_now

    # ② 折叠 → 保存设置后，nav/split 不许变成 [0, W]（原 bug 就是它导致"一条缝"）
    window.toggle_nav(False)
    assert window.nav_panel.isVisible() is False
    assert settings_bool(window._settings.value(key_collapsed, False), False) is True
    window._save_settings()
    saved_after_collapse = window._settings.value(key_split)
    print("    折叠后 nav/split =", saved_after_collapse,
          "| nav/collapsed =", window._settings.value(key_collapsed))
    if saved_after_collapse:
        first = int(list(saved_after_collapse)[0])
        assert first >= MIN_NAV_WIDTH, "折叠时不该把 0 宽度写进 nav/split：{}".format(
            saved_after_collapse
        )

    # ③ 模拟"重启"：新建一个窗口读同一份 QSettings，应还原成折叠
    reopened_collapsed = MainWindow(config)
    reopened_collapsed.resize(1180, 760)
    print("    重开后 折叠 =", reopened_collapsed._nav_collapsed,
          "| 面板可见 =", reopened_collapsed.nav_panel.isVisible(),
          "| 文案 =", reopened_collapsed.action_toggle_nav.text())
    assert reopened_collapsed._nav_collapsed is True
    assert reopened_collapsed.nav_panel.isVisible() is False
    assert reopened_collapsed.action_toggle_nav.text() == "展开左侧列表"
    reopened_collapsed._closing = True
    reopened_collapsed.close()

    # ④ 记录"展开"之后再模拟重启，应还原成展开且宽度正常
    window.toggle_nav(True)
    window._settings.setValue(key_split, [333, 700])
    window._save_settings()
    reopened_visible = MainWindow(config)
    reopened_visible.resize(1180, 760)
    print("    重开后 折叠 =", reopened_visible._nav_collapsed,
          "| 面板可见 =", reopened_visible.nav_panel.isVisible(),
          "| 宽度 =", reopened_visible.central_splitter.sizes())
    assert reopened_visible._nav_collapsed is False
    assert reopened_visible.nav_panel.isVisible() is True
    assert reopened_visible.action_toggle_nav.text() == "折叠左侧列表"
    sizes_now = list(reopened_visible.central_splitter.sizes())
    assert sizes_now and sizes_now[0] >= MIN_NAV_WIDTH, sizes_now
    reopened_visible._closing = True
    reopened_visible.close()

    # ⑤ 没有记录时必须默认展开（你要的"默认打开"）
    window._settings.remove(key_collapsed)
    window._settings.remove(key_split)
    reopened_default = MainWindow(config)
    reopened_default.resize(1180, 760)
    print("    清空记录后重开：折叠 =", reopened_default._nav_collapsed,
          "| 面板可见 =", reopened_default.nav_panel.isVisible(),
          "| 宽度 =", reopened_default.central_splitter.sizes())
    assert reopened_default._nav_collapsed is False, "没有记录时应默认展开"
    assert reopened_default.nav_panel.isVisible() is True
    default_sizes = list(reopened_default.central_splitter.sizes())
    assert default_sizes and default_sizes[0] >= MIN_NAV_WIDTH, default_sizes
    reopened_default._closing = True
    reopened_default.close()

    # ⑥ 真机 bug 的回归断言：注册表里存的是**字符串** 'false'/'true'，
    #    必须按字符串语义解析（bool('false') 是 True —— 就是它让"展开"失效）。
    print("\n    -- 字符串 'false' / 'true' 的还原（真机场景）--")
    for raw_value, expect_collapsed in (("false", False), ("true", True),
                                        ("False", False), ("True", True)):
        window._settings.setValue(key_collapsed, raw_value)
        window._settings.setValue(key_split, ["268", "766"])
        restored = MainWindow(config)
        restored.resize(1180, 760)
        print("    registry {!r:<8} → 折叠={!r} 面板可见={!r}".format(
            raw_value, restored._nav_collapsed, restored.nav_panel.isVisible()))
        assert restored._nav_collapsed is expect_collapsed, (
            "注册表 {!r} 应按字符串语义解析，实际折叠={!r}".format(
                raw_value, restored._nav_collapsed
            )
        )
        assert restored.nav_panel.isVisible() is not expect_collapsed
        if not expect_collapsed:
            raw_sizes = list(restored.central_splitter.sizes())
            assert raw_sizes and raw_sizes[0] >= MIN_NAV_WIDTH, raw_sizes
        restored._closing = True
        restored.close()

    # 还原原本的设置，避免影响后面的断言
    if keep_split:
        window._settings.setValue(key_split, keep_split)
    window.toggle_nav(True)

    # 10.4 在窗口之间循环切换
    second_bot = config.bots[1]
    window.open_bot_tab(second_bot.id, True)
    window._cycle_page(1)
    window._cycle_page(-1)
    print("    循环切换后当前 =", window._current_bot_id())
    assert window._current_bot_id() in window._tabs

    # 10.5 导航按钮状态与提示
    window._select_nav_bot(first_status.bot_id)
    window._update_nav_buttons()
    print("    底部按钮：打开 =", window.nav_open_button.isEnabled(),
          "| 停止 =", window.nav_stop_button.isEnabled(),
          "| 关闭 =", window.nav_close_button.isEnabled())
    assert window.nav_close_button.isEnabled() is True
    assert window.nav_hint_label.text()

    # 10.6 QSettings：当前 bot、展开项、分隔比例
    window._save_settings()
    saved_current = window._settings.value(SETTINGS_CURRENT_BOT)
    saved_split = window._settings.value(SETTINGS_NAV_SPLIT)
    saved_expanded = window._settings.value(SETTINGS_NAV_EXPANDED)
    print("    current_bot =", saved_current, "| nav/split =", saved_split,
          "| nav/expanded =", saved_expanded)
    assert saved_current in config.bots[0].id + config.bots[1].id
    assert saved_split

    window.close_all_windows()
    assert window.stack.currentWidget() is window.placeholder_page

    # 11) Phase D：快捷键 / 折叠文案 / 关闭全部窗口 / 图例
    print("\n[11] Phase D：快捷键与收尾")

    # 11.1 Ctrl+1..9 条目随配置生成
    window._refresh_view_bot_actions()
    jump_texts = [action.text() for action in window._bot_jump_actions[:4]]
    print("    Ctrl+1..4 条目：")
    for text in jump_texts:
        print("      " + text)
    assert len(window._bot_jump_actions) == 9
    assert window._bot_jump_actions[0].isEnabled() is True
    assert window._bot_jump_actions[0].shortcut().toString() == "Ctrl+1"
    assert window._bot_jump_actions[8].isEnabled() is False, "只有 2 个机器人时第 9 项应禁用"
    assert config.bots[0].name in window._bot_jump_actions[0].text()

    # 11.2 跳到第 N 个机器人
    window._jump_to_bot(1)
    assert window._current_bot_id() == config.bots[1].id
    window._jump_to_bot(99)  # 越界应安全忽略
    assert window._current_bot_id() == config.bots[1].id
    print("    Ctrl+2 跳到 =", window._current_bot_id())

    # 11.3 折叠时文案与宽度记忆
    window.toggle_nav(True)
    window._settings.setValue(SETTINGS_NAV_SPLIT, [333, 700])
    window.toggle_nav(False)
    assert window.action_toggle_nav.text() == "展开左侧列表"
    assert int(list(window._settings.value(SETTINGS_NAV_SPLIT))[0]) >= 0
    window.toggle_nav(True)
    assert window.action_toggle_nav.text() == "折叠左侧列表"
    print("    折叠文案 =", window.action_toggle_nav.text(),
          "| 记忆宽度 =", window._settings.value(SETTINGS_NAV_SPLIT))

    # 11.4 关闭全部窗口（confirm=False，避免弹窗）
    window.open_bot_tab(config.bots[0].id, True)
    window.open_bot_tab(config.bots[1].id, False)
    assert len(window._tabs) == 2
    closed = window.close_all_windows()
    assert closed == 2 and len(window._tabs) == 0
    assert window.stack.currentWidget() is window.placeholder_page
    assert window.action_close_all_windows.shortcut().toString() == "Ctrl+Shift+W"
    print("    关闭全部窗口 =", closed, "个")

    # 11.5 图例文案与首次提示标记
    legend = window.action_status_legend.text()
    assert "图例" in legend
    window._maybe_show_first_run_hint()
    assert window._settings.value("ui/hint_shown") in (True, "true", 1)
    print("    图例动作 =", legend,
          "| 首次提示已保存 =", window._settings.value("ui/hint_shown"))

    # 11.6 N1：Ctrl+Tab 的轮转顺序 = 左栏从上到下的顺序（不是窗口打开顺序）
    print("\n[11.6] Ctrl+Tab 轮转顺序")
    window.close_all_windows()
    assert not window._tabs

    display_ids = [bot.id for bot in config.bots]
    display_names = [bot.name for bot in config.bots]
    print("    左栏顺序 =", display_names)

    # 故意按"反序"打开窗口（复现真机上 1→3→4→2 的乱序场景）
    open_sequence = list(reversed(display_ids))
    for bot_id in open_sequence:
        window.open_bot_tab(bot_id, focus=False)
    stack_order = [
        window.stack.widget(index).bot_id
        for index in range(window.stack.count())
        if isinstance(window.stack.widget(index), BotTab)
    ]
    print("    窗口打开顺序（stack）=", [config.get_bot(i).name for i in stack_order])
    assert stack_order == open_sequence, stack_order

    order = window.open_bot_tabs_in_order()
    print("    轮转顺序（= 左栏顺序）= ",
          [config.get_bot(i).name for i in order])
    assert order == display_ids, order

    # 从第一个窗口开始，Ctrl+Tab 应该严格按左栏顺序走一圈
    window.show_bot_view(display_ids[0], focus=True)
    visited: List[str] = []
    for _ in range(len(display_ids)):
        assert window._current_bot_id() == order[len(visited)]
        window._cycle_page(1)
        visited.append(window._current_bot_id() or "")
    print("    Ctrl+Tab 依次经过 =", [config.get_bot(i).name for i in visited])
    assert visited == display_ids[1:] + display_ids[:1], visited

    # 反向（Ctrl+Shift+Tab）应当是相反顺序
    back: List[str] = []
    for _ in range(len(display_ids)):
        window._cycle_page(-1)
        back.append(window._current_bot_id() or "")
    assert back == list(reversed(visited)), (back, visited)
    print("    Ctrl+Shift+Tab 依次经过 =", [config.get_bot(i).name for i in back])

    # 在左栏把最后一个机器人上移一位，轮转顺序必须跟着变
    moved_id = display_ids[-1]
    assert window.move_bot_up(moved_id) is True
    order_after_move = window.open_bot_tabs_in_order()
    expected_after_move = display_ids[:-2] + [moved_id, display_ids[-2]]
    print("    上移「{}」后轮转顺序 =".format(config.get_bot(moved_id).name),
          [config.get_bot(i).name for i in order_after_move])
    assert order_after_move == expected_after_move, order_after_move
    assert window.move_bot_down(moved_id) is True
    assert window.open_bot_tabs_in_order() == display_ids

    # 一个窗口都没打开时：给出提示而不是崩（占位页 → 下一跳落到第一个）
    window.close_all_windows()
    window._cycle_page(1)
    assert window._current_bot_id() is None
    print("    无窗口时 _cycle_page 安全返回 = True")
    for bot_id in display_ids:
        window.open_bot_tab(bot_id, focus=False)
    window.show_bot_view(display_ids[0], focus=True)

    # 12) 步骤 1~5：分屏布局（模板 / 单击跳转 / 窗格按钮 / 自定义布局 / 持久化）
    print("\n[12] 分屏布局（步骤 1~5）")

    # 12.0 程序对齐全：布局树覆盖配置里的每个程序
    all_ids = {program.id for bot in config.bots for program in bot.programs}
    tree_ids = set(layout_model.programs_in_tree(tab.layout_tree()))
    assert all_ids.issubset(tree_ids), sorted(all_ids - tree_ids)
    print("    布局树覆盖程序 =", len(tree_ids), "/", len(all_ids),
          "| 描述 =", tab.layout_description())

    # 12.1 六个模板的窗格数量与方向（ATRI：3 程序）
    expect = {
        layout_model.KIND_V: (2, ["v"]),
        layout_model.KIND_H: (2, ["h"]),
        layout_model.KIND_TABS: (1, []),
        layout_model.KIND_SINGLE: (1, []),
        layout_model.KIND_H_LEFT_V: (3, ["h", "v"]),
        layout_model.KIND_H_RIGHT_V: (3, ["h", "v"]),
    }
    for kind, (panes, orientations) in expect.items():
        assert window.apply_layout(kind) is True, kind
        got_orientations = collect_splitter_orientations(tab._splitter_host)
        print("    {} -> 窗格 {} 方向 {}".format(kind, tab.pane_count(), got_orientations))
        assert tab.pane_count() == panes, (kind, tab.pane_count())
        assert got_orientations == orientations, (kind, got_orientations)
        assert tab.layout_kind() == kind, (kind, tab.layout_kind())

    # 12.2 左右分屏时，主程序在左格（第一个窗格）
    window.apply_layout(layout_model.KIND_H)
    handles = tab.pane_handles()
    primary_key = tab.all_keys()[0]
    assert handles[0].keys == [primary_key], handles[0].keys
    print("    左右分屏第一格 =", handles[0].keys[0], "==", primary_key)

    # 12.3 单击程序条目 -> 切到该程序所在的窗格（含标签页切换）
    target_key = tab.all_keys()[-1]
    assert window.show_program(target_key) is True
    target_pane = tab.pane_of_key(target_key)
    assert target_pane is not None
    assert target_pane.current_key() == target_key, target_pane.current_key()
    assert target_pane.focused is True
    print("    show_program ->", target_key, "落在窗格", target_pane.path)

    # 12.4 单击机器人条目 -> 打开窗口、焦点在主程序，且橙色高亮也落到主程序行（R1-1）
    window._select_nav_bot(target_bot.id)
    window.toggle_nav(True)
    assert window.open_nav_item(window._nav_items[target_bot.id]) is True
    assert tab.focused_program() == primary_key, tab.focused_program()
    selected_key = window._nav_selected_program_key()
    print("    单击机器人条目 -> 焦点 =", tab.focused_program(),
          "| 左栏高亮 =", selected_key or "（机器人行）")
    assert window._nav_selected_bot_id() == target_bot.id
    assert selected_key == primary_key, selected_key

    # 12.4.1 切换窗格标签后，左栏高亮跟着走（R1-1）
    other_key = tab.all_keys()[-1]
    assert tab.focus_program(other_key, scroll=False) is True
    window._select_nav_for_bot(target_bot.id)
    print("    切到另一程序后 左栏高亮 =", window._nav_selected_program_key())
    assert window._nav_selected_program_key() == other_key

    # 12.4.2 高亮的是程序行，不是机器人行（机器人行没有 program key）
    bot_item = window._nav_items[target_bot.id]
    assert window._nav_item_program_key(bot_item) == ""
    assert window._nav_child_item(bot_item, primary_key) is not None
    print("    机器人行无 program key、程序行可定位 = True")

    # 12.4.3 没有程序时安全返回（不抛异常、不高亮）
    status_without_program = window.bot_status_of("不存在的机器人")
    assert status_without_program is None
    assert window._nav_program_for_bot("不存在的机器人") == ""
    print("    未知机器人 -> _nav_program_for_bot 返回空串")

    # 12.4.3.1 （N5）"窗口还没打开时点击机器人行"——真机 bug 的回归断言
    #   症状：点 ATRI［0/3］后高亮留在机器人行，而 ▸ 已经在主程序行上。
    #   根因：_nav_program_for_bot() 依赖窗口/状态取值，窗口未创建时返回空串。
    #   （注意：这里刻意不用 12.4.4 —— 那个编号已被"三态视觉"占用）
    print("\n[12.4.3.1] 点机器人行 → 高亮必须落到主程序行（窗口未打开也要对）")
    fresh_bot = config.bots[-1] if len(config.bots) > 1 else config.bots[0]
    fresh_primary = window._primary_key_for_bot(fresh_bot.id)
    assert fresh_primary, "配置里应能算出主程序 key"
    assert fresh_primary == build_manager_key(
        fresh_bot.id,
        next((p.id for p in fresh_bot.programs if p.role == ROLE_PRIMARY),
             fresh_bot.programs[0].id),
    ), fresh_primary
    # 模拟"该机器人窗口还没打开"：先关掉它
    window.close_bot_window(fresh_bot.id, confirm=False)
    assert fresh_bot.id not in window._tabs, "前置条件：窗口应已关闭"
    assert window._nav_program_for_bot(fresh_bot.id) == fresh_primary, (
        "窗口未打开时也必须能算出主程序 key（这是 bug 的根因）"
    )
    # 真正走一遍用户路径：点机器人行
    assert window.open_nav_item(window._nav_items[fresh_bot.id]) is True
    selected_now = window._nav_selected_program_key()
    marker_now = window._nav_focus_marker_key()
    print("    点机器人行 -> 高亮 = {} | ▸ = {} | 两者一致 = {}".format(
        selected_now or "（机器人行）", marker_now, selected_now == marker_now))
    assert selected_now == fresh_primary, (
        "点机器人行后高亮必须落在主程序行，实际 {}".format(selected_now or "（机器人行）")
    )
    assert selected_now == marker_now, "高亮必须与 ▸ 绑定（同一行）"

    # 再点一次（窗口已打开）也要一致
    window.open_nav_item(window._nav_items[fresh_bot.id])
    assert window._nav_selected_program_key() == fresh_primary
    assert window._nav_selected_program_key() == window._nav_focus_marker_key()
    print("    再点一次（窗口已打开）-> 高亮仍与 ▸ 一致 = True")

    # 12.4.8 修 1：全量重建不再抹掉高亮，脏标记不再粘住（真机 bug 的回归断言）
    print("\n[12.4.8] 修 1：重建保留高亮 / 脏标记复位")
    window.show_program(primary_key, scroll=False)
    assert window._nav_selected_program_key() == primary_key

    # ① 重建后高亮必须仍在（原实现在这里会变成空串 —— 真机就是这个表现）
    window._refresh_nav()
    assert window._nav_selected_program_key() == primary_key, (
        "重建后高亮丢了：{!r}".format(window._nav_selected_program_key())
    )
    assert window._nav_selected_bot_id() == target_bot.id
    print("    重建后高亮 =", repr(window._nav_selected_program_key()), "（保持在程序行）")

    # ② 重建也不允许出现"没有任何选中项"
    diagnostics = window.nav_diagnostics()
    assert diagnostics["rebuild_miss_count"] == 0, diagnostics
    print("    重建计数 =", diagnostics["rebuild_count"],
          "| 丢选中项次数 =", diagnostics["rebuild_miss_count"])

    # ③ 反复重建 5 次，高亮每次都还在
    for _ in range(5):
        window._refresh_nav()
        assert window._nav_selected_program_key() == primary_key
    print("    连续 5 次重建后高亮 =", repr(window._nav_selected_program_key()))

    # ④ 脏标记必须被消费掉（原实现 dirty_all 会一直是 True）
    window._nav_dirty_all = False
    window._nav_dirty_bots.clear()
    window._schedule_nav_refresh()
    assert window._nav_dirty_all is True, "无 bot_id 的调度应置 dirty_all"
    window._flush_nav_refresh()
    assert window._nav_dirty_all is False, "全量刷新后 dirty_all 必须复位"
    assert not window._nav_dirty_bots, "全量刷新后 dirty_bots 必须清空"
    print("    全量刷新后 dirty_all =", window._nav_dirty_all,
          "| dirty_bots =", set(window._nav_dirty_bots))

    # ⑤ 定时器真的到点之后（等满 400ms > 250ms 去抖），高亮仍在
    window.show_program(primary_key, scroll=False)
    before_timer = window._nav_selected_program_key()
    window._schedule_nav_refresh()          # 模拟"状态变化"排队一次全量刷新
    timer_loop = QEventLoop()
    QTimer.singleShot(400, timer_loop.quit)
    timer_loop.exec()
    after_timer = window._nav_selected_program_key()
    print("    去抖定时器到点前/后 =", repr(before_timer), "/", repr(after_timer))
    assert after_timer == primary_key, (
        "250ms 去抖刷新把高亮抹掉了：{!r}".format(after_timer)
    )

    # ⑥ 脏项局部刷新（带 bot_id）同样不能抹掉高亮
    window._schedule_nav_refresh(target_bot.id)
    window._flush_nav_refresh()
    assert window._nav_selected_program_key() == primary_key
    print("    局部刷新后高亮 =", repr(window._nav_selected_program_key()))

    # 12.4.4 三态视觉（R1-2）：▸ 焦点标记 / ▌ 已打开标记 / 选中是独立信息
    window.show_program(primary_key, scroll=False)
    focused_item = window._nav_child_item(bot_item, primary_key)
    other_item = window._nav_child_item(bot_item, other_key)
    assert focused_item is not None and other_item is not None
    focused_text = focused_item.text(0)
    other_text = other_item.text(0)
    print("    焦点行文字 =", repr(focused_text))
    print("    普通行文字 =", repr(other_text))
    assert focused_text.startswith(NAV_FOCUS_MARK + NAV_PREFIX_GAP), focused_text
    assert not other_text.startswith(NAV_FOCUS_MARK), other_text
    assert focused_item.data(0, ROLE_NAV_FOCUSED) is True
    assert other_item.data(0, ROLE_NAV_FOCUSED) is False
    assert focused_item.font(0).bold() is True
    assert other_item.font(0).bold() is False
    # 同一机器人的所有程序行前缀等宽（文字左对齐）
    widths = {
        len(window._nav_child_item(bot_item, key).text(0)) for key in tab.all_keys()
    }
    assert len(widths) == 1, widths

    # 12.4.5 机器人行的 ▌ 只在窗口打开时出现
    assert bot_item.text(0).startswith(NAV_OPEN_MARK), bot_item.text(0)
    assert bot_item.data(0, ROLE_NAV_OPENED) is True
    other_bot_id = config.bots[1].id
    other_bot_item = window._nav_items[other_bot_id]
    print("    已打开机器人行 =", repr(bot_item.text(0)))
    print("    未打开机器人行 =", repr(other_bot_item.text(0)))
    assert not other_bot_item.text(0).startswith(NAV_OPEN_MARK)

    # 12.4.6 焦点标记 ▸：全局唯一，绑定"当前窗口正在显示的程序"（确认 1+2）
    selected_before = window._nav_selected_program_key()
    window._mark_nav_focus(target_bot.id, other_key)
    assert window._nav_selected_program_key() == selected_before, "标记不应改选中项"

    def _marked_keys() -> List[str]:
        """全树里带 ▸ 的程序 key（应当只有 0 或 1 个）。"""
        found: List[str] = []
        for _bot_id, _item in window._nav_items.items():
            for _index in range(_item.childCount()):
                _child = _item.child(_index)
                if bool(_child.data(0, ROLE_NAV_FOCUSED)):
                    found.append(str(_child.data(0, ROLE_NAV_PROGRAM_KEY) or ""))
        return found

    markers = _marked_keys()
    print("    全树 ▸ 标记 =", markers, "| 当前显示程序 =", window._nav_focus_marker_key())
    assert len(markers) <= 1, markers
    # 有窗口打开时，标记 = 当前窗口正在显示的程序（而不是传进来的参数）
    assert window._nav_focus_marker_key() == window.current_tab().focused_program()
    assert markers == [window._nav_focus_marker_key()], markers
    assert focused_item.data(0, ROLE_NAV_FOCUSED) is True
    assert other_item.data(0, ROLE_NAV_FOCUSED) is False
    assert focused_item.font(0).bold() is True

    # 12.4.6.1 标记不再"每个机器人各一个"：其它已打开的机器人不允许有 ▸
    for other_id, other_item_root in window._nav_items.items():
        if other_id == target_bot.id:
            continue
        for index in range(other_item_root.childCount()):
            assert other_item_root.child(index).data(0, ROLE_NAV_FOCUSED) is False, (
                "机器人 {} 的第 {} 行不该有 ▸".format(other_id, index)
            )
    print("    其它机器人均无 ▸ = True（共 {} 个机器人）".format(len(window._nav_items)))

    # 12.4.6.2 关窗后标记清空（不留残影）
    window.close_bot_window(target_bot.id, confirm=False)
    markers_after_close = _marked_keys()
    assert not markers_after_close, markers_after_close
    print("    关窗后 ▸ 标记 =", markers_after_close)
    window.open_bot_tab(target_bot.id, focus=True)
    window.show_program(primary_key, scroll=False)
    assert _marked_keys() == [primary_key], _marked_keys()

    # 12.4.7 底部提示含三态图例
    window._update_nav_hint()
    hint = window.nav_hint_label.text()
    print("    左栏提示 =", hint)
    assert NAV_FOCUS_MARK in hint and NAV_OPEN_MARK in hint

    # 12.5 外观（R2）：模式持久化 / 立即生效 / 勾选同步 / 菜单与快捷键
    #      N2：断言改用**调色板 Window 亮度**（唯一可信判据），
    #      而不是 is_dark()（那个跟着"意图"走，真机上会撒谎）。
    print("\n[12.5] 外观（浅色 / 深色 / 跟随系统）")

    def palette_lum() -> int:
        return theme_tokens.palette_window_lightness(QApplication.instance())

    original_mode = window.current_theme_mode()
    window.set_theme_mode(theme_tokens.MODE_DARK)
    assert window.current_theme_mode() == theme_tokens.MODE_DARK
    assert theme_tokens.save_mode(window._settings, theme_tokens.MODE_DARK)
    saved_theme = window._settings.value(theme_tokens.SETTINGS_THEME_KEY)
    lum_dark = palette_lum()
    print("    切到深色 -> ui/theme =", saved_theme,
          "| Window亮度 =", lum_dark, "| theme_debug =",
          theme_tokens.theme_debug_info())
    assert str(saved_theme) == theme_tokens.MODE_DARK
    assert theme_tokens.is_dark(window) is True
    # N2 硬断言：深色必须让窗口底色真的变深（这次量的是颜色，不是意图）
    assert lum_dark < 128, "深色模式下 Window 亮度应 < 128，实际 {}".format(lum_dark)
    assert theme_tokens.palette_is_dark() is True

    window.set_theme_mode(theme_tokens.MODE_LIGHT)
    lum_light = palette_lum()
    print("    切到浅色 -> Window亮度 =", lum_light)
    assert theme_tokens.is_dark(window) is False
    assert lum_light >= 128, "浅色模式下 Window 亮度应 >= 128，实际 {}".format(lum_light)
    assert theme_tokens.palette_is_dark() is False

    # N2.1：日志区也要跟随主题（之前"永远深色"，浅色模式下右侧一片黑）
    def qss_bg_lightness(qss: str) -> int:
        """从样式表里取出 background-color 的亮度。"""
        marker = "background-color: "
        head = qss.split(marker, 1)[1]
        color = head.split(";", 1)[0].strip()
        return theme_tokens.QColor(color).lightness()

    light_qss = theme_tokens.log_editor_qss(window)
    assert "QPlainTextEdit#programLog" in light_qss
    assert qss_bg_lightness(light_qss) >= 128, (
        "浅色模式下日志区底色应偏亮，实际样式 {}".format(light_qss)
    )
    window.set_theme_mode(theme_tokens.MODE_DARK)
    dark_qss = theme_tokens.log_editor_qss(window)
    assert qss_bg_lightness(dark_qss) < 128, (
        "深色模式下日志区底色应偏暗，实际样式 {}".format(dark_qss)
    )
    print("    日志区底色亮度：浅色 {} / 深色 {}".format(
        qss_bg_lightness(light_qss), qss_bg_lightness(dark_qss)))

    # N2.4：菜单栏必须由 QSS 显式着色（Windows 原生菜单栏不读调色板，
    # 真机症状就是"浅色界面 + 白色菜单文字"）。
    def qss_text_lightness(qss: str) -> int:
        """从样式表里取出第一个 color: 的亮度。"""
        head = qss.split("color: ", 1)[1]
        return theme_tokens.QColor(head.split(";", 1)[0].strip()).lightness()

    light_menu = theme_tokens.menubar_qss(window)
    assert "QMenuBar::item" in light_menu, light_menu
    assert qss_text_lightness(light_menu) < 128, (
        "浅色模式菜单文字应偏暗，实际 {}".format(light_menu)
    )
    window.set_theme_mode(theme_tokens.MODE_DARK)
    dark_menu = theme_tokens.menubar_qss(window)
    assert qss_text_lightness(dark_menu) >= 128, (
        "深色模式菜单文字应偏亮，实际 {}".format(dark_menu)
    )
    assert window.menuBar().styleSheet(), "菜单栏样式必须真的设上去"
    # N2.6：必须脱离系统原生菜单栏，否则它的颜色由系统明暗决定，
    # 会出现"深色模式黑字/浅色模式白字"以及"颜色残留上一次切换"。
    assert window.menuBar().isNativeMenuBar() is False, "菜单栏必须使用 Qt 自绘"

    # N2.8：菜单栏文字色必须**真的写进了菜单栏自己的调色板**，
    # 而不是只写在 QSS 里（只写 QSS 会被 Qt 缓存，切换主题后不重取 ——
    # 真机症状："一旦某刻是浅色，之后深色模式下菜单栏永远是黑字"）。
    def menubar_text_lightness() -> int:
        pal = window.menuBar().palette()
        return pal.color(QPalette.ColorRole.WindowText).lightness()

    window.set_theme_mode(theme_tokens.MODE_LIGHT)
    light_menu_text = menubar_text_lightness()
    assert light_menu_text < 128, (
        "浅色模式菜单栏调色板文字应偏暗，实际亮度 {}".format(light_menu_text)
    )
    window.set_theme_mode(theme_tokens.MODE_DARK)
    dark_menu_text = menubar_text_lightness()
    assert dark_menu_text >= 128, (
        "深色模式菜单栏调色板文字应偏亮，实际亮度 {}".format(dark_menu_text)
    )
    # 反向再切一次：确认不会"卡在第一次的颜色上"
    window.set_theme_mode(theme_tokens.MODE_LIGHT)
    assert menubar_text_lightness() < 128, "从深色切回浅色后菜单栏文字必须重新变暗"

    # N2.9："深色 → 跟随系统（浅色）"必须让菜单栏回到浅色。
    # 真机 bug：跟随系统模式沿用了我们上一轮设的深色调色板，
    # 表现为"菜单栏独立为黑色、文字为白色"。
    window.set_theme_mode(theme_tokens.MODE_DARK)
    assert menubar_text_lightness() >= 128
    window.set_theme_mode(theme_tokens.MODE_SYSTEM)
    system_menu_text = menubar_text_lightness()
    system_window_lum = palette_lum()
    assert (system_menu_text < 128) is (system_window_lum >= 128), (
        "跟随系统时菜单栏文字明暗必须与窗口一致：文字亮度 {} / 窗口亮度 {}".format(
            system_menu_text, system_window_lum
        )
    )
    assert window.menuBar().styleSheet(), "跟随系统也要重设菜单栏样式"
    print("    深色 → 跟随系统：菜单栏文字亮度 {} / 窗口亮度 {}（一致）".format(
        system_menu_text, system_window_lum))
    print("    菜单文字亮度：浅色 {} / 深色 {}　| 菜单栏样式已设 = {}　| Qt 自绘 = {}".format(
        light_menu_text, dark_menu_text,
        bool(window.menuBar().styleSheet()), not window.menuBar().isNativeMenuBar()))
    print("    菜单栏调色板往返复验：浅色 {} → 深色 {} → 浅色 {}（不得残留）".format(
        light_menu_text, dark_menu_text, menubar_text_lightness()))

    # N2.5：外壳控件（工具栏/按钮/标签/输入）也必须显式着色 ——
    # 它们不读调色板，真机症状是"浅色界面里第二三行按钮文字发白"。
    def chrome_button_text_lightness(qss: str) -> int:
        """从外壳样式里取出 QPushButton 的文字色亮度。"""
        marker = "QPushButton {"
        assert marker in qss, "chrome_qss 缺少 QPushButton 规则"
        body = qss.split(marker, 1)[1].split("}", 1)[0]
        assert "color: " in body, body
        color = body.split("color: ", 1)[1].split(";", 1)[0].strip()
        return theme_tokens.QColor(color).lightness()

    window.set_theme_mode(theme_tokens.MODE_LIGHT)
    light_chrome = theme_tokens.chrome_qss(window)
    for needle in ("QToolBar", "QToolButton", "QPushButton", "QLabel",
                   "QMenuBar::item", "QLineEdit", "QComboBox", "QTreeWidget"):
        assert needle in light_chrome, "chrome_qss 缺少 {}".format(needle)
    light_chrome_text = chrome_button_text_lightness(light_chrome)
    assert light_chrome_text < 128, "浅色模式按钮文字应偏暗，实际 {}".format(light_chrome_text)

    window.set_theme_mode(theme_tokens.MODE_DARK)
    dark_chrome = theme_tokens.chrome_qss(window)
    dark_chrome_text = chrome_button_text_lightness(dark_chrome)
    assert dark_chrome_text >= 128, "深色模式按钮文字应偏亮，实际 {}".format(dark_chrome_text)
    assert window.styleSheet(), "外壳样式必须真的设到主窗口上"
    assert "QToolBar" in window.styleSheet()
    print("    外壳按钮文字亮度：浅色 {} / 深色 {}　| 主窗口样式长度 = {}".format(
        light_chrome_text, dark_chrome_text, len(window.styleSheet())))
    window.set_theme_mode(theme_tokens.MODE_LIGHT)

    # 深浅来回切 3 轮：亮度必须每次都跟着翻（防止"只有文字变色"的老毛病）
    for _ in range(3):
        window.set_theme_mode(theme_tokens.MODE_DARK)
        assert palette_lum() < 128
        window.set_theme_mode(theme_tokens.MODE_LIGHT)
        assert palette_lum() >= 128
    print("    深浅往返 3 轮：Window亮度始终跟随 = True")

    # 跟随系统：撤掉自建调色板，交还系统（亮度应回到系统值）
    window.set_theme_mode(theme_tokens.MODE_SYSTEM)
    print("    跟随系统 -> Window亮度 =", palette_lum(),
          "| 是否自建调色板 =", theme_tokens.palette_overridden())
    assert theme_tokens.palette_overridden() is False
    assert window.current_theme_mode() == theme_tokens.MODE_SYSTEM

    # 菜单勾选状态跟着走（不写死具体模式，避免依赖前面的调用顺序）
    expected_mode = window.current_theme_mode()
    window._refresh_theme_actions()
    checked = [
        mode for mode, action in window._theme_actions.items() if action.isChecked()
    ]
    print("    外观菜单勾选 =", checked, "| 当前模式 =", expected_mode)
    assert checked == [expected_mode], checked
    assert len(window._theme_actions) == len(theme_tokens.THEME_MODES)
    assert window.action_toggle_theme.shortcut().toString() == "Ctrl+Shift+D"

    # Ctrl+Shift+D 在浅/深之间切换（从浅色出发，避免依赖系统当前明暗）
    window.set_theme_mode(theme_tokens.MODE_LIGHT)
    window.toggle_theme()
    assert window.current_theme_mode() == theme_tokens.MODE_DARK, window.current_theme_mode()
    assert palette_lum() < 128
    window.toggle_theme()
    assert window.current_theme_mode() == theme_tokens.MODE_LIGHT, window.current_theme_mode()
    assert palette_lum() >= 128
    print("    Ctrl+Shift+D 切换：浅 -> 深 -> 浅 均正确")

    # 运行参数对话框里的外观下拉：改了就发信号、值也能读回
    from app.ui.main_window import RuntimeSettingsDialog

    settings_dialog = RuntimeSettingsDialog(window, theme_mode=theme_tokens.MODE_DARK)
    assert settings_dialog.theme_mode() == theme_tokens.MODE_DARK
    received_themes = []
    settings_dialog.theme_changed.connect(received_themes.append)
    settings_dialog.set_theme_mode(theme_tokens.MODE_SYSTEM)
    settings_dialog._on_theme_combo_changed(0)
    assert received_themes == [theme_tokens.MODE_SYSTEM], received_themes
    assert settings_dialog.values()["theme"] == theme_tokens.MODE_SYSTEM
    print("    运行参数对话框 外观下拉 =", received_themes)
    settings_dialog.close()

    # 主题令牌三种模式都能生成样式表，且深/浅不同
    window.set_theme_mode(theme_tokens.MODE_SYSTEM)
    system_qss = theme_tokens.nav_tree_qss(window)
    window.set_theme_mode(theme_tokens.MODE_DARK)
    dark_nav = theme_tokens.nav_tree_qss(window)
    dark_log = theme_tokens.log_editor_qss(window)
    window.set_theme_mode(theme_tokens.MODE_LIGHT)
    light_nav = theme_tokens.nav_tree_qss(window)
    print("    深色左栏长度 =", len(dark_nav), "| 浅色左栏长度 =", len(light_nav),
          "| 跟随系统长度 =", len(system_qss))
    assert dark_nav != light_nav, "深浅两套左栏样式应当不同"
    assert qss_bg_lightness(dark_log) < 128, dark_log
    # 恢复原来的模式，避免影响后续断言
    window.set_theme_mode(original_mode)
    print("    已恢复外观 =", theme_tokens.mode_label(original_mode))

    # 12.6 热切换收尾（R2-3）：合并刷新 / 对话框刷新 / 对话框登记清理
    print("\n[12.6] 热切换收尾")
    # 12.6.1 set_theme_mode 只刷一次（内部排队的那次被丢弃）
    window.set_theme_mode(theme_tokens.MODE_SYSTEM)
    before_count = window.theme_refresh_count()
    window.set_theme_mode(theme_tokens.MODE_DARK)
    after_dark = window.theme_refresh_count()
    window.set_theme_mode(theme_tokens.MODE_LIGHT)
    after_light = window.theme_refresh_count()
    print("    _apply_theme 次数：起始 {} -> 深色 {} -> 浅色 {}".format(
        before_count, after_dark, after_light))
    assert after_dark - before_count == 1, "切换一次应只整体刷新一次"
    assert after_light - after_dark == 1

    # 12.6.2 跟随系统时，密集的 paletteChanged 只排队一次
    window.set_theme_mode(theme_tokens.MODE_SYSTEM)
    before_count = window.theme_refresh_count()
    window._theme_refresh_pending = False
    for _ in range(5):
        window._on_palette_changed()
    assert window._theme_refresh_pending is True, "应只排队一次"
    app.processEvents()  # 让 singleShot 的 _flush 跑到
    after_count = window.theme_refresh_count()
    print("    5 次 paletteChanged -> 实际刷新 {} 次".format(after_count - before_count))
    assert after_count - before_count == 1, "合并刷新后应只刷一次"

    # 12.6.3 显式模式下忽略系统的 paletteChanged
    window.set_theme_mode(theme_tokens.MODE_DARK)
    count_before = window.theme_refresh_count()
    window._theme_refresh_pending = False
    window._on_palette_changed()
    assert window._theme_refresh_pending is False, "显式模式不应排队系统主题刷新"
    print("    显式深色下 paletteChanged 被忽略 = True")

    # 12.6.4 已打开的对话框会被一起刷新，关闭后自动清理
    from app.ui.bot_list_dialog import BotListDialog

    theme_dialog = BotListDialog(window, window, auto_refresh=False)
    window.register_dialog(theme_dialog)
    assert theme_dialog in window._live_dialogs()
    theme_dialog.hint_label.setText("占位")
    window.set_theme_mode(theme_tokens.MODE_LIGHT)
    hint_qss = theme_dialog.hint_label.styleSheet()
    print("    对话框提示样式 =", hint_qss)
    assert theme_tokens.muted_text_color(theme_dialog) in hint_qss
    # EditBotDialog 走的是 objectName 批量刷新
    from app.ui.edit_bot_dialog import EditBotDialog, HINT_LABEL_NAME

    edit_dialog = EditBotDialog(window, window.config, config.bots[0])
    window.register_dialog(edit_dialog)
    window.set_theme_mode(theme_tokens.MODE_DARK)
    edited_hints = edit_dialog.findChildren(QLabel, HINT_LABEL_NAME)
    print("    EditBotDialog 说明标签数 =", len(edited_hints))
    assert len(edited_hints) >= 1
    for label in edited_hints:
        assert theme_tokens.muted_text_color(edit_dialog) in label.styleSheet()
    edit_dialog.close()
    edit_dialog.deleteLater()
    # 12.x N4：编辑对话框里的「布局模板」下拉 —— 实时套用 + 只写本机偏好
    print("\n[12.x] 布局模板下拉（N4）")
    from app.ui.edit_bot_dialog import (  # noqa: E501
        LAYOUT_AUTO_LABEL,
        LAYOUT_CHOICES,
        EditBotDialog as _EditBotDialog,
    )

    layout_bot = config.bots[0]
    key_layout = layout_model.settings_layout_key(layout_bot.id)
    key_tree = layout_model.settings_tree_key(layout_bot.id)
    keep_layout = window._settings.value(key_layout, None)
    keep_tree = window._settings.value(key_tree, None)
    try:
        window.open_bot_tab(layout_bot.id, True)
        layout_dialog = _EditBotDialog(window, window.config, layout_bot)
        window.register_dialog(layout_dialog)

        # ① 下拉条目：「自动」+ 模板
        combo_labels = [
            layout_dialog.layout_combo.itemText(i)
            for i in range(layout_dialog.layout_combo.count())
        ]
        print("    下拉条目 =", combo_labels)
        assert combo_labels[0] == LAYOUT_AUTO_LABEL
        assert len(combo_labels) == 1 + len(LAYOUT_CHOICES)
        assert layout_dialog.selected_layout_kind() == "", "默认应为「自动」"

        # ② 选模板 → 保存 → 注册表写入 + 已打开的窗口当场换布局
        tab_before = window._tabs[layout_bot.id].layout_kind()
        layout_dialog.layout_combo.setCurrentIndex(
            layout_dialog.layout_combo.findData(layout_model.KIND_H_RIGHT_V)
        )
        assert layout_dialog.layout_template_applied() is True
        layout_dialog.save_layout_preference()
        applied = window._apply_layout_from_dialog(layout_dialog)
        tab_after = window._tabs[layout_bot.id].layout_kind()
        print("    套用模板：{} → {}　| 注册表 = {!r}　| 实时生效 = {}".format(
            tab_before, tab_after,
            window._settings.value(key_layout), applied))
        assert str(window._settings.value(key_layout)) == layout_model.KIND_H_RIGHT_V
        assert tab_after == layout_model.KIND_H_RIGHT_V, "已打开的窗口应立刻换布局"
        assert applied is True

        # ③ 选回「自动」→ 记忆被清掉、窗口恢复默认布局
        window._settings.setValue(key_tree, "{\"type\":\"pane\"}")  # 假装有自定义树
        layout_dialog.layout_combo.setCurrentIndex(0)
        assert layout_dialog.selected_layout_kind() == ""
        window._apply_layout_from_dialog(layout_dialog)
        print("    选自动后：注册表 = {!r} | 自定义树 = {!r} | 窗口布局 = {}".format(
            window._settings.value(key_layout), window._settings.value(key_tree),
            window._tabs[layout_bot.id].layout_kind()))
        assert window._settings.value(key_layout) is None
        assert window._settings.value(key_tree) is None

        layout_dialog.close()
        layout_dialog.deleteLater()
        app.processEvents()
    finally:
        if keep_layout is None:
            window._settings.remove(key_layout)
        else:
            window._settings.setValue(key_layout, keep_layout)
        if keep_tree is None:
            window._settings.remove(key_tree)
        else:
            window._settings.setValue(key_tree, keep_tree)
        window._settings.sync()

    print("\n[12.y] 切换布局必须保留日志内容（真机修复）")
    # 真机反馈："切换日志区的布局时，日志内容会消失"。
    # 原因：重建窗格会 self._views = {} 并 deleteLater() 旧控件，
    # 而日志文本存在 QPlainTextEdit 里 —— 控件一销毁文本就没了。
    # 修复：重建前 _capture_log_texts()，建完新窗格 _restore_log_texts()。
    log_bot = config.bots[0]
    window.open_bot_tab(log_bot.id, True)
    app.processEvents()
    log_tab = window._tabs.get(log_bot.id)
    assert log_tab is not None, "应当能打开机器人窗口"
    assert log_tab._views, "窗格应当已建立"

    marker = "布局切换自检标记：这一行不能丢"
    for view in log_tab._views.values():
        view.append_log(marker)
    app.processEvents()
    before_views = len(log_tab._views)
    before_hits = sum(
        1 for view in log_tab._views.values() if marker in view.editor.toPlainText()
    )
    print("    切换前：窗格 {} 个，含标记的 {} 个".format(before_views, before_hits))
    assert before_hits > 0, "写进去的标记应当能读到"

    tried = []
    for kind in ("v", "h", "single", "tabs"):
        if kind == log_tab.layout_kind():
            continue
        log_tab.set_layout(kind)
        app.processEvents()
        hits = sum(
            1 for view in log_tab._views.values() if marker in view.editor.toPlainText()
        )
        tried.append((kind, hits, len(log_tab._views), sum(
            int(getattr(v, "_line_count", 0)) for v in log_tab._views.values()
        )))
        assert hits > 0, "切到 {} 布局后日志内容不应消失".format(kind)
    for kind, hits, views, lines in tried:
        print("    切到 {:<7} → 窗格 {} 个，含标记 {} 个，行数合计 {}".format(
            kind, views, hits, lines))
    assert tried, "至少应能切到一种不同布局"
    # 恢复原布局，避免影响后面的自检项
    log_tab.set_layout(log_tab.layout_kind())
    app.processEvents()

    print("\n[12.w] 窗格按钮只作用于当前显示的程序（真机事故）")
    # 真机反馈："这一栏的按钮会导致整个 bot 实例下的所有程序共同启动停止"
    # 原因：PaneWidget._emit_action 遍历了 self.keys()（=窗格内全部程序）。
    # 这里用真实窗格验证：记录 programAction 收到哪些 key，必须**只有一个**。
    scope_bot = config.bots[0]
    window.open_bot_tab(scope_bot.id, True)
    app.processEvents()
    scope_tab = window._tabs.get(scope_bot.id)
    assert scope_tab is not None
    panes = [
        obj for obj in scope_tab.findChildren(object)
        if obj.__class__.__name__ == "PaneWidget"
    ]
    print("    找到窗格 {} 个".format(len(panes)))
    assert panes, "应当至少有一个窗格"

    multi = None
    for pane in panes:
        if len(pane.keys()) > 1:
            multi = pane
            break
    if multi is None:
        print("    没有多程序窗格（单程序窗格无法体现该 bug），跳过")
    else:
        received = []
        multi.programAction.connect(lambda key, action: received.append((key, action)))
        multi.select_program(multi.node.programs[1])
        app.processEvents()
        expected = multi.current_key()
        multi._emit_action("start")
        app.processEvents()
        print("    多程序窗格 keys = {}".format(multi.keys()))
        print("    当前显示 = {}　| 发出请求 = {}".format(expected, received))
        assert len(received) == 1, (
            "窗格按钮必须只作用于当前程序，实际发了 {} 个请求：{}".format(
                len(received), received)
        )
        assert received[0][0] == expected, (
            "请求的 key 必须是当前显示的那个程序：{} != {}".format(
                received[0][0], expected)
        )
        # 切到另一个程序再点一次，作用对象要跟着变
        received.clear()
        multi.select_program(multi.node.programs[0])
        app.processEvents()
        first_key = multi.current_key()
        multi._emit_action("stop")
        app.processEvents()
        assert len(received) == 1 and received[0][0] == first_key, (
            "切换显示后作用对象应跟着变：{}".format(received)
        )
        print("    切到 {} 后再点 → 请求 = {}（正确）".format(first_key, received))
        multi.programAction.disconnect()

    print("\n[12.z] 折叠左栏后要有可点的标签栏（真机需求）")
    # 需求原话："关闭左侧栏时，切换 bot 实例似乎只能使用 ctrl+tab，
    #           建议在取消左侧栏时还原添加左侧栏前上方的类似于浏览器的标签页"
    keep_collapsed = window._settings.value(SETTINGS_NAV_COLLAPSED, None)
    keep_forced = window._settings.value(SETTINGS_BOT_TAB_BAR, None)
    keep_current = window._settings.value(SETTINGS_CURRENT_BOT, None)
    try:
        bar = window.bot_tab_bar
        assert bar is not None, "标签栏应当存在"

        # 打开至少两个机器人窗口，标签栏才有意义
        opened = []
        for bot in config.bots[:2]:
            window.open_bot_tab(bot.id, focus=False)
            opened.append(bot.id)
        window.toggle_nav(True)
        app.processEvents()
        print("    已打开窗口 {} 个，标签数 {}，标签栏可见 = {}".format(
            len(window._tabs), bar.count(), bar.isVisible()))
        assert bar.count() == len(window._tabs), "标签数应等于已打开窗口数"
        assert not bar.isVisible(), "左栏展开且未手动勾选时，标签栏应当隐藏"
        texts = [bar.tabText(i) for i in range(bar.count())]
        data = [bar.tabData(i) for i in range(bar.count())]
        assert all(data), "每个标签都要带上 bot_id（用于切换）"
        print("    标签文字 = {}　| 每个标签都带 bot_id = True".format(texts))

        # ① 折叠左栏 → 标签栏 + Bot 工具条自动出现
        window.toggle_nav(False)
        app.processEvents()
        bot_bar = window.bot_bar
        print("    折叠左栏后：标签栏可见 = {}，Bot 工具条可见 = {}".format(
            bar.isVisible(), bot_bar.isVisible()))
        assert bar.isVisible(), "折叠左栏后标签栏必须自动出现"
        assert bot_bar.isVisible(), "Bot 工具条应当始终可用（它就是那一排操作按钮）"
        # 工具条上要能看到关键动作（打开全部窗口 / 自定义打开 = 查看已有 bot）
        tool_actions = [a.text() for a in bot_bar.actions() if a.text()]
        print("    Bot 工具条动作 = {}".format(tool_actions))
        for wanted in ("新建 Bot", "编辑当前 Bot", "启动当前 Bot", "停止当前 Bot",
                       "重启当前 Bot", "打开全部窗口", "查看已有 bot…", "打开配置文件",
                       "启动全部", "停止全部"):
            assert any(wanted in text for text in tool_actions), (
                "工具条应当包含「{}」，实际 {}".format(wanted, tool_actions)
            )
        # 只保留**一行**工具栏（真机确认的布局）：顶部不应再有第二条
        bars = window.findChildren(QToolBar)
        print("    窗口里的工具栏数量 = {}（应当只有 1 条）".format(len(bars)))
        assert len(bars) == 1, "应当只有一条工具栏，实际 {}".format(
            [b.windowTitle() for b in bars]
        )
        assert window.toolbar is bot_bar, "self.toolbar 应指向这条唯一的工具条"

        # ② 点标签 → 切到对应机器人（等价于左栏点它）
        target_bot = opened[-1]
        target_tab = window._tabs[target_bot]
        index = [i for i in range(bar.count()) if bar.tabData(i) == target_bot][0]
        window.show_bot_view(opened[0], focus=True)
        app.processEvents()
        assert window.stack.currentWidget() is window._tabs[opened[0]]
        bar.setCurrentIndex(index)
        app.processEvents()
        current = window.stack.currentWidget()
        print("    点标签 #{} → 当前页面 = {}（期望 {}）".format(
            index, getattr(current, "bot_id", "?"), target_bot))
        assert current is target_tab, "点标签应当切到那个机器人的窗口"
        # 高亮也要跟着落到那个机器人的焦点程序行（与左栏单击一致）
        expected_key = (
            target_tab.focused_program() or window._primary_key_for_bot(target_bot)
        )
        marker_now = window._nav_focus_marker_key()
        selected_now = window._nav_selected_program_key()
        print("    高亮 = {!r} | ▸ = {!r}（期望 {!r}）".format(
            selected_now, marker_now, expected_key))
        assert selected_now == expected_key, "点标签后高亮应落在该机器人的程序行"
        assert selected_now == marker_now, "高亮必须与 ▸ 绑定（同一行）"

        # ③ 展开左栏 → 标签栏按"是否手动勾选"决定
        window.toggle_nav(True)
        app.processEvents()
        assert not window.bot_tab_bar.isVisible(), "展开后未勾选时应隐藏标签栏"
        assert bot_bar.isVisible(), "Bot 工具条应当始终可用（与左栏折叠无关）"
        forced = window.toggle_bot_tab_bar(True)   # 手动显示
        app.processEvents()
        print("    手动勾选「显示标签栏」→ 返回 {}，可见 = {}".format(
            forced, bar.isVisible()))
        assert forced is True and bar.isVisible(), "手动勾选后应显示"
        assert settings_bool(
            window._settings.value(SETTINGS_BOT_TAB_BAR, False), False
        ), "偏好应当写进注册表"
        window.toggle_bot_tab_bar(False)
        app.processEvents()
        assert not bar.isVisible(), "取消勾选后应隐藏"

        # ④ 关闭一个窗口 → 标签同步减少
        before_count = bar.count()
        window.close_bot_window(opened[-1], confirm=False)
        app.processEvents()
        print("    关闭一个窗口：标签 {} → {}".format(before_count, bar.count()))
        assert bar.count() == before_count - 1, "关窗后标签数量要跟着减少"
    finally:
        for key, value in (
            (SETTINGS_NAV_COLLAPSED, keep_collapsed),
            (SETTINGS_BOT_TAB_BAR, keep_forced),
            (SETTINGS_CURRENT_BOT, keep_current),
        ):
            if value is None:
                window._settings.remove(key)
            else:
                window._settings.setValue(key, value)
        window._settings.sync()
        window.toggle_bot_tab_bar(False)
        window.toggle_nav(True)
        app.processEvents()

    theme_dialog.close()
    theme_dialog.deleteLater()
    app.processEvents()
    alive_after = window._live_dialogs()
    print("    关闭后仍在册的对话框 =", len(alive_after))
    assert theme_dialog not in alive_after and edit_dialog not in alive_after
    # 已销毁的对话框再刷一次也不能炸
    window._apply_dialog_theme(theme_dialog)
    window.unregister_dialog(theme_dialog)
    print("    已销毁对话框刷新安全 = True")
    window.set_theme_mode(original_mode)

    # 12.5 Ctrl/Shift 旁路：只选中，不改变焦点
    before_focus = tab.focused_program()
    window._nav_modifier_held = lambda: True  # 模拟按住 Ctrl
    window._on_nav_current_changed(window._nav_items[target_bot.id], None)
    assert tab.focused_program() == before_focus
    print("    Ctrl 单击 -> 焦点保持不变 =", tab.focused_program())

    # 12.6 窗格按钮 -> 单程序启停（走真实的启动请求路径）
    started_keys = []
    tab.startRequested.connect(lambda keys: started_keys.extend(keys))
    pane0 = tab.pane_of_key(primary_key)
    pane0._emit_action("start")
    assert primary_key in started_keys, started_keys
    print("    窗格按钮 start ->", started_keys)

    # 12.7 自定义布局：写入 QSettings 的是 JSON 树，且模板名记为 custom
    custom_tree = layout_model.PaneNode.split(layout_model.ORIENT_H, [
        layout_model.PaneNode.split(layout_model.ORIENT_V, [
            layout_model.PaneNode.pane(tab.program_id_of_key(tab.all_keys()[0])),
            layout_model.PaneNode.pane(tab.program_id_of_key(tab.all_keys()[1])),
        ]),
        layout_model.PaneNode.pane(tab.program_id_of_key(tab.all_keys()[2])),
    ])
    assert tab.apply_layout_tree(custom_tree) is True
    assert tab.layout_kind() == layout_model.KIND_CUSTOM
    saved_tree = window._settings.value(layout_model.settings_tree_key(target_bot.id))
    assert saved_tree, "自定义布局树应写入 QSettings"
    assert layout_model.tree_from_json(saved_tree) is not None
    print("    自定义布局已保存，JSON 长度 =", len(str(saved_tree)))

    # 12.8 比例与焦点被记住
    window._save_pane_layout(target_bot.id, tab)
    saved_sizes = window._settings.value(layout_model.settings_sizes_key(target_bot.id))
    saved_focus = window._settings.value(layout_model.settings_focus_key(target_bot.id))
    print("    pane/sizes =", saved_sizes, "| pane/focus =", saved_focus)
    assert isinstance(saved_sizes, dict) and saved_sizes
    assert saved_focus

    # 12.9 关窗再开：布局 / 比例 / 焦点都应还原
    saved_kind = tab.layout_kind()
    saved_description = tab.layout_description()
    assert window.close_bot_window(target_bot.id, confirm=False) is True
    reopened = window.open_bot_tab(target_bot.id, focus=True)
    assert reopened is not None
    print("    重开后 布局 =", reopened.layout_kind(),
          "| 窗格 =", reopened.pane_count(),
          "| 焦点 =", reopened.focused_program())
    assert reopened.layout_kind() == saved_kind, (reopened.layout_kind(), saved_kind)
    assert reopened.layout_description() == saved_description
    assert reopened.focused_program() == saved_focus

    # 12.10 恢复默认布局会清掉记忆
    assert window.reset_layout() is True
    assert not window._settings.value(layout_model.settings_tree_key(target_bot.id))
    print("    恢复默认后 布局 =", reopened.layout_kind())

    # 12.11 布局菜单可用且有勾选
    window._rebuild_layout_menu()
    menu_actions = [action for action in window.layout_menu.actions()
                    if action.isCheckable()]
    print("    布局菜单可勾选项 =", len(menu_actions))
    assert len(menu_actions) >= len(window._layout_kinds())
    assert any(action.isChecked() for action in menu_actions)

    print("\n自检通过：工具栏、窗口打开/关闭、状态刷新、分割比例、QSettings、"
          "Bot 状态公共层（计数/归类/文案/提示）、"
          "查看已有 bot 对话框（列表/过滤/动作分发/删除与排序）、"
          "左侧竖栏导航（树结构/单击打开/折叠/循环切换/设置持久化）、"
          "Phase D（Ctrl+1..9、折叠文案与宽度记忆、关闭全部窗口、状态图例）、"
          "分屏布局（六模板/单击跳转/窗格按钮/自定义布局/比例与焦点持久化）均正常。")
    window._closing = True
    window.close()
    try:
        os.remove(str(config.path))
    except OSError:
        pass
    del app
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """便捷入口：单独运行本文件时直接起一个窗口。"""
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    settings = QSettings(ORG_NAME, APP_NAME)
    config_path = settings.value("config/path", str(DEFAULT_CONFIG_PATH))
    config = BotConfig.load(Path(str(config_path)), create_if_missing=True)
    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
