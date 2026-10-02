# -*- coding: utf-8 -*-
"""主题（浅色 / 深色 / 跟随系统）与配色令牌。

为什么单独一个模块
------------------
R2 之前，"颜色"散落在三个文件里：``program_widget`` 的日志区配色、
``main_window`` 的左栏 QSS 与状态色、``bot_tab`` 的窗格状态色。热切换主题时
必须把所有地方都刷一遍，散着写容易漏、也没法统一"深色下该用哪个灰"。

于是把令牌集中到这里：

    · 模式：MODE_SYSTEM / MODE_LIGHT / MODE_DARK + apply_theme(app, mode)
    · 判定：current_mode() / current_scheme() / is_dark()
    · 颜色：muted_text_color() / status_color() / focus_border_color()
             log_editor_qss() / nav_tree_qss() / pane_qss()
    · 行为：window背景、按钮、列表三者都从**调色板**取色，
            只有"深色状态下系统没给深色调色板"时才用固定的深色常量兜底。

其它模块只调用本模块的函数，不再自己写颜色字面量；
``program_widget`` 与 ``main_window`` 里原有的同名函数保留为转发（依赖不破）。
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

# ---------------------------------------------------------------------------
# 模式
# ---------------------------------------------------------------------------

#: 跟随系统（默认）
MODE_SYSTEM = "system"
#: 强制浅色
MODE_LIGHT = "light"
#: 强制深色
MODE_DARK = "dark"

#: 全部模式（顺序即菜单顺序）
THEME_MODES = (MODE_SYSTEM, MODE_LIGHT, MODE_DARK)

#: 模式 -> 菜单文字
MODE_LABELS: Dict[str, str] = {
    MODE_SYSTEM: "跟随系统",
    MODE_LIGHT: "浅色模式",
    MODE_DARK: "深色模式",
}

#: 模式 -> 菜单文字后面的说明
MODE_HINTS: Dict[str, str] = {
    MODE_SYSTEM: "跟随 Windows 的浅色/深色设置，切换系统主题时界面自动跟随",
    MODE_LIGHT: "始终使用浅色界面",
    MODE_DARK: "始终使用深色界面（日志区本来就是深色）",
}

#: QSettings 里保存模式的键（本机界面偏好）
SETTINGS_THEME_KEY = "ui/theme"

#: QSettings 组织名 / 应用名（与 main_window 保持一致；QSettings 会找同一处注册表项）
ORG_NAME = "QQBotLauncher"
APP_NAME = "QQBot启动管理器"

#: 用户选择的模式（system/light/dark）
_ACTIVE_MODE: str = MODE_SYSTEM
#: 实际生效的方案（永远是 light/dark）——「跟随系统」也会解析成其中之一
_RESOLVED_MODE: str = MODE_LIGHT
#: 是否已经由本模块写入过调色板（写入过就不再信任 styleHints().colorScheme()）
_PALETTE_OVERRIDDEN: bool = False
#: 我们写入的调色板**是不是深色**（与上面分开存：
#: "我们控制着调色板" 与 "当前是深是浅" 是两件事，混用一个标志会造成判据自相矛盾）
_PALETTE_PRESENTED_DARK: bool = False
#: 最近一次 apply_theme 的结果说明（诊断用）
_LAST_APPLY_MSG: str = ""

# ---------------------------------------------------------------------------
# 深色令牌（仅在系统没有提供深色调色板时使用）
# ---------------------------------------------------------------------------

#: 深色界面用的基础色（窗口 / 控件底色 / 文字）
DARK_WINDOW = "#2b2b2b"
DARK_BASE = "#1e1f22"
DARK_ALTERNATE = "#33363a"
DARK_TEXT = "#d6d6d6"
DARK_BUTTON = "#3a3d41"
DARK_BORDER = "#3a3d41"
DARK_HIGHLIGHT = "#2f6fb5"
DARK_HIGHLIGHT_TEXT = "#ffffff"
DARK_DISABLED_TEXT = "#7a7a7a"

#: 浅色界面用的基础色（N2.4：柔和黄灰，不用纯白 —— 纯白刺眼；
#: 也不用 Windows 标准板的 #ced0d4（发灰发脏））
LIGHT_WINDOW = "#f0efe9"
LIGHT_BASE = "#f7f6f1"
LIGHT_ALTERNATE = "#e8e6dd"
LIGHT_TEXT = "#1f1f1f"
LIGHT_BUTTON = "#eae8e0"
LIGHT_BORDER = "#c6c3b8"
LIGHT_HIGHLIGHT = "#2f6fb5"
LIGHT_HIGHLIGHT_TEXT = "#ffffff"
LIGHT_DISABLED_TEXT = "#9a9a9a"

#: 日志区（深色）：深底浅字
LOG_DARK_BG = "#1e1f22"
LOG_DARK_TEXT = "#d6d6d6"
LOG_DARK_BORDER = "#3a3d41"
LOG_DARK_SELECTION = "#2f6fb5"

#: 日志区（浅色）：**故意与窗口/左栏同色**（`LIGHT_WINDOW` == `#f0efe9`）。
#:
#: 真机确认（用户实测后明确要求保留）：浅色下日志区是"浅黄灰"而不是纯白，
#: 这样它和窗体连成一片，不会在浅色界面里"挖"出一块刺眼的白方块。
#: 深色下才让它比窗口（#2b2b2b）更深，用 #1e1f22 形成层次。
#: ⚠️ 别把它"修"成白色 —— 那不是 bug，是有意为之。
LOG_LIGHT_BG = "#f0efe9"
LOG_LIGHT_TEXT = "#1f1f1f"
LOG_LIGHT_BORDER = "#c6c3b8"
LOG_LIGHT_SELECTION = "#2f6fb5"

#: 日志区还原终端颜色用的 ANSI 16 色（顺序就是 ANSI 的 0-15：
#: 前 8 个基础色 黑红绿黄蓝品红青白，后 8 个是对应的亮色）。
#:
#: 两套的目标不一样，别把它们改成同一份：
#:   · **深色**照搬 Windows Terminal 的 Campbell 配色 —— 用户在终端里看到什么色，
#:     日志区就是什么色（真机日志里 `ESC[32m` 的 INFO 行两边都是绿色）；
#:   · **浅色**必须在 `#f0efe9` 上读得清，所以整体压暗：终端里的亮白 #f2f2f2、
#:     亮黄 #f9f1a5 放到浅底上等于隐形，这里换成深灰 / 深橄榄黄。
ANSI_DARK_COLORS: Tuple[str, ...] = (
    "#0c0c0c", "#c50f1f", "#13a10e", "#c19c00",
    "#0037da", "#881798", "#3a96dd", "#cccccc",
    "#767676", "#e74856", "#16c60c", "#f9f1a5",
    "#3b78ff", "#b4009e", "#61d6d6", "#f2f2f2",
)

ANSI_LIGHT_COLORS: Tuple[str, ...] = (
    "#1f1f1f", "#b91c1c", "#0b6b0b", "#8a5a00",
    "#1d4ed8", "#8b1a8b", "#0e7490", "#6b6b6b",
    "#5a5a5a", "#d13438", "#107c10", "#9a6700",
    "#2563eb", "#a21caf", "#0891b2", "#3f3f3f",
)

#: 浅色底上"太亮就压暗"的亮度阈值；深色底上"太暗就提亮"的阈值。
ANSI_LIGHT_MAX_LUMA = 0.62
ANSI_DARK_MIN_LUMA = 0.10

#: xterm 256 色里 6×6×6 色立方用的六档分量
ANSI_CUBE_LEVELS: Tuple[int, ...] = (0, 95, 135, 175, 215, 255)

#: 选中项文字色 / 日志区禁用文字色
HIGHLIGHTED_TEXT = "#ffffff"
DISABLED_LOG_TEXT = "#9a9a9a"

#: 浅色左栏兜底（与 main_window 原来的硬编码一致，保证观感不变）
NAV_LIGHT_BORDER = "#dcdcdc"
NAV_LIGHT_BG = "#ffffff"
NAV_LIGHT_SELECTED_BG = "#cfe3f7"
NAV_LIGHT_SELECTED_TEXT = "#1a1a1a"
NAV_LIGHT_HOVER_BG = "#eaf3fc"

#: 次要文字色
MUTED_DARK = "#9a9a9a"
MUTED_LIGHT = "#6b6b6b"

#: 状态色（浅深主题通用；深色下同样清晰）
COLOR_IDLE = "#808080"
COLOR_STARTING = "#d9a441"
COLOR_RUNNING = "#3fa34d"
COLOR_STOPPING = "#d9a441"
COLOR_STOPPED = "#808080"
COLOR_FAILED = "#d9534f"
COLOR_DISABLED = "#808080"

#: 窗格焦点边框色
FOCUS_BORDER_DARK = "#4a7fb5"
FOCUS_BORDER_LIGHT = "#7ab0e0"

#: 进程状态 + Bot 汇总状态 -> 颜色（键是字符串，避免 import ProcessManager 造成环）
STATUS_COLORS: Dict[str, str] = {
    "idle": COLOR_IDLE,
    "starting": COLOR_STARTING,
    "running": COLOR_RUNNING,
    "stopping": COLOR_STOPPING,
    "stopped": COLOR_STOPPED,
    "failed": COLOR_FAILED,
    "disabled": COLOR_DISABLED,
    "partial": COLOR_STARTING,
    "unknown": COLOR_IDLE,
}


# ---------------------------------------------------------------------------
# 模式读写
# ---------------------------------------------------------------------------

def normalize_mode(mode: object) -> str:
    """把任意输入规范成三种模式之一（不认识的值 -> 跟随系统）。"""
    text = str(mode or "").strip().lower()
    if text in ("dark", "深色", "深色模式", "night"):
        return MODE_DARK
    if text in ("light", "浅色", "浅色模式", "day"):
        return MODE_LIGHT
    return MODE_SYSTEM


def mode_label(mode: object) -> str:
    """模式的中文名（用于菜单与状态提示）。"""
    return MODE_LABELS.get(normalize_mode(mode), MODE_LABELS[MODE_SYSTEM])


def current_mode() -> str:
    """当前生效的模式（本模块记忆的值）。"""
    return _ACTIVE_MODE


def palette_overridden() -> bool:
    """是否由本模块强制写过调色板（此时不能相信系统的 colorScheme）。"""
    return _PALETTE_OVERRIDDEN


# ---------------------------------------------------------------------------
# 主题判定
# ---------------------------------------------------------------------------

def os_scheme() -> str:
    """读取**操作系统**当前是深色还是浅色（N3.0，跟随系统的唯一依据）。

    为什么不直接用 `QStyleHints.colorScheme()`：那个值会被我们自己调用
    `setColorScheme(Dark/Light)` 改掉，于是"深色 → 跟随系统"时会读到
    残留的 dark，进而给菜单栏套上深色配色 —— 这正是"菜单栏独立变黑"的根源。

    这里直接读 Windows 的真实偏好（注册表 AppsUseLightTheme：0=深色，1=浅色），
    拿不到时再退回 styleHints，最后按调色板亮度兜底。返回 "dark"/"light"。
    """
    # ① Windows 真实偏好
    try:
        settings = QSettings(
            r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            QSettings.Format.NativeFormat,
        )
        value = settings.value("AppsUseLightTheme", None)
        if value is not None:
            text = str(value).strip().lower()
            if text in ("0", "false", "no", "off"):
                return "dark"
            if text in ("1", "true", "yes", "on"):
                return "light"
    except (AttributeError, TypeError, RuntimeError, ValueError):
        pass

    # ② styleHints（可能被我们自己改过，仅作参考）
    scheme = _style_hints_scheme()
    if scheme:
        return scheme

    # ③ 调色板亮度兜底
    return "dark" if _palette_is_dark() else "light"


def _style_hints_scheme() -> str:
    """系统给出的配色方案："dark" / "light" / ""（拿不到）。"""
    try:
        app = QApplication.instance()
        if app is None:
            return ""
        hints = app.styleHints()
        if hints is None:
            return ""
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except (AttributeError, TypeError, RuntimeError):
        pass
    return ""


def _palette_is_dark(palette: Optional[QPalette] = None) -> bool:
    """按"窗口底色 vs 文字色亮度"判断是否深色。"""
    try:
        if palette is None:
            app = QApplication.instance()
            palette = app.palette() if app is not None else None
        if palette is None:
            return False
        window_light = palette.color(QPalette.ColorRole.Window).lightness()
        text_light = palette.color(QPalette.ColorRole.WindowText).lightness()
        return window_light < 128 and text_light > window_light
    except (AttributeError, TypeError, RuntimeError):
        return False


def current_scheme(widget: Optional[QWidget] = None) -> str:
    """当前实际呈现的配色："dark" / "light"。**永远以实际调色板为准。**

    N2.1 定稿（这是修掉"白字/花屏"的关键）：
      1. 我们自己装过调色板 → 用它记录的明暗（`_PALETTE_PRESENTED_DARK`）
      2. QStyleHints.colorScheme() **只在它与调色板明暗一致时**才采用 ——
         Windows 上经常出现 hint=dark 而调色板是浅色的情况（真机实测：
         hint=dark、Window 亮度=206）。此时若听 hint，界面就会用深色的
         文字色去配浅色的底色 —— 也就是"跟随系统（浅色界面）却有白色文字"。
      3. 显式模式（没有 QApplication 时也能给出合理答案）
      4. 调色板亮度
    """
    if _PALETTE_OVERRIDDEN:
        return "dark" if _PALETTE_PRESENTED_DARK else "light"
    # N3.0：跟随系统已解析成实色方案，直接用解析结果（避免再猜系统）
    if _ACTIVE_MODE == MODE_SYSTEM and _RESOLVED_MODE in (MODE_DARK, MODE_LIGHT):
        return _RESOLVED_MODE
    palette_dark = _palette_is_dark(widget.palette() if widget is not None else None)
    scheme = _style_hints_scheme()
    if scheme:
        if (scheme == "dark") == palette_dark:
            return scheme
        # hint 与实际调色板矛盾：相信颜色（它才是肉眼所见）
    if _ACTIVE_MODE == MODE_DARK:
        return "dark"
    if _ACTIVE_MODE == MODE_LIGHT:
        return "light"
    return "dark" if palette_dark else "light"


def is_dark(widget: Optional[QWidget] = None) -> bool:
    """当前是否深色（等价于 is_dark_theme）。"""
    return current_scheme(widget) == "dark"


# ---------------------------------------------------------------------------
# 偏好：从 QSettings 读 / 写模式
# ---------------------------------------------------------------------------

def load_mode(settings: object = None, default: str = MODE_SYSTEM) -> str:
    """从 QSettings 读取主题偏好（读不到时返回 default）。"""
    if settings is None:
        try:
            from PyQt6.QtCore import QSettings

            settings = QSettings(ORG_NAME, APP_NAME)
        except (ImportError, TypeError, RuntimeError):
            return normalize_mode(default)
    try:
        raw = settings.value(SETTINGS_THEME_KEY, "")
    except (AttributeError, TypeError, RuntimeError):
        return normalize_mode(default)
    text = str(raw or "").strip()
    return normalize_mode(text) if text else normalize_mode(default)


def save_mode(settings: object, mode: object) -> bool:
    """把主题偏好写进 QSettings；写成功返回 True。"""
    if settings is None:
        return False
    try:
        settings.setValue(SETTINGS_THEME_KEY, normalize_mode(mode))
        settings.sync()
        return True
    except (AttributeError, TypeError, RuntimeError):
        return False


def startup_mode(settings: object = None) -> str:
    """启动时要用的模式：QSettings 偏好（默认跟随系统）。"""
    return load_mode(settings, MODE_SYSTEM)


# ---------------------------------------------------------------------------
# 应用主题（热切换入口）
# ---------------------------------------------------------------------------

def _build_dark_palette(base: Optional[QPalette] = None) -> QPalette:
    """构造一套深色调色板（把 Windows 浅色调色板换成深灰阶）。"""
    palette = QPalette(base) if base is not None else QPalette()
    window = QColor(DARK_WINDOW)
    base_color = QColor(DARK_BASE)
    alternate = QColor(DARK_ALTERNATE)
    text = QColor(DARK_TEXT)
    button = QColor(DARK_BUTTON)
    border = QColor(DARK_BORDER)
    highlight = QColor(DARK_HIGHLIGHT)
    highlighted_text = QColor(DARK_HIGHLIGHT_TEXT)
    disabled = QColor(DARK_DISABLED_TEXT)

    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.Window, window)
        palette.setColor(group, QPalette.ColorRole.WindowText, text)
        palette.setColor(group, QPalette.ColorRole.Base, base_color)
        palette.setColor(group, QPalette.ColorRole.AlternateBase, alternate)
        palette.setColor(group, QPalette.ColorRole.ToolTipBase, base_color)
        palette.setColor(group, QPalette.ColorRole.ToolTipText, text)
        palette.setColor(group, QPalette.ColorRole.Text, text)
        palette.setColor(group, QPalette.ColorRole.Button, button)
        palette.setColor(group, QPalette.ColorRole.ButtonText, text)
        palette.setColor(group, QPalette.ColorRole.BrightText, QColor("#ff5555"))
        palette.setColor(group, QPalette.ColorRole.Link, highlight)
        palette.setColor(group, QPalette.ColorRole.Highlight, highlight)
        palette.setColor(group, QPalette.ColorRole.HighlightedText, highlighted_text)
        palette.setColor(group, QPalette.ColorRole.PlaceholderText, disabled)
        palette.setColor(group, QPalette.ColorRole.Mid, border)
        palette.setColor(group, QPalette.ColorRole.Dark, QColor("#1a1a1a"))
        palette.setColor(group, QPalette.ColorRole.Shadow, QColor("#101010"))
        palette.setColor(group, QPalette.ColorRole.Light, QColor("#454545"))
        palette.setColor(group, QPalette.ColorRole.Midlight, QColor("#3a3a3a"))

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    return palette


def palette_window_lightness(app: Optional[QApplication] = None) -> int:
    """当前应用调色板里 Window 角色的亮度（0~255）。

    N2：这是**唯一可信**的"界面到底深还是浅"的判据 ——
    ``styleHints().colorScheme()`` 只表示"我们的意图/系统开关"，
    真机上出现过"hint 说深色、窗口底却仍是系统浅色"的情况。
    """
    try:
        target = app if app is not None else QApplication.instance()
        if target is None:
            return 255
        return int(target.palette().color(QPalette.ColorRole.Window).lightness())
    except (AttributeError, TypeError, RuntimeError):
        return 255


def palette_is_dark(app: Optional[QApplication] = None, threshold: int = 128) -> bool:
    """当前应用调色板是否偏深（按 Window 亮度）。"""
    return palette_window_lightness(app) < threshold


def resolved_mode() -> str:
    """实际生效的方案（永远是 "light" / "dark"）。"""
    if _RESOLVED_MODE in (MODE_DARK, MODE_LIGHT):
        return _RESOLVED_MODE
    return MODE_DARK if os_scheme() == "dark" else MODE_LIGHT


def theme_debug_info(mode: object = None) -> Dict[str, object]:
    """主题诊断信息（`--theme-debug` / 自检用）。"""
    app = QApplication.instance()
    lightness = palette_window_lightness(app)
    hint = _style_hints_scheme()
    return {
        "mode": normalize_mode(mode if mode is not None else _ACTIVE_MODE),
        "resolved": resolved_mode(),
        "os": os_scheme(),
        "hint": hint or "?",
        "window_lightness": lightness,
        "palette_dark": lightness < 128,
        "overridden": _PALETTE_OVERRIDDEN,
        "scheme": current_scheme(),
        "last_apply": _LAST_APPLY_MSG,
    }


def _build_light_palette(base: Optional[QPalette] = None) -> QPalette:
    """构造一套**明确的**浅色调色板（N2.2）。

    为什么不用 ``app.style().standardPalette()``：
    Windows 上它的 ``Window`` 是 ``#ced0d4``（浅灰，亮度 206），
    于是"浅色模式"整体发灰；而且它由原生样式生成，
    各组角色（Disabled/Inactive）与我们的 QSS 期望并不总是一致。
    这里和白/黑两套色都写死，行为可预测。
    """
    palette = QPalette(base) if base is not None else QPalette()
    window = QColor(LIGHT_WINDOW)
    base_color = QColor(LIGHT_BASE)
    alternate = QColor(LIGHT_ALTERNATE)
    text = QColor(LIGHT_TEXT)
    button = QColor(LIGHT_BUTTON)
    border = QColor(LIGHT_BORDER)
    highlight = QColor(LIGHT_HIGHLIGHT)
    highlighted_text = QColor(LIGHT_HIGHLIGHT_TEXT)
    disabled = QColor(LIGHT_DISABLED_TEXT)

    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.Window, window)
        palette.setColor(group, QPalette.ColorRole.WindowText, text)
        palette.setColor(group, QPalette.ColorRole.Base, base_color)
        palette.setColor(group, QPalette.ColorRole.AlternateBase, alternate)
        palette.setColor(group, QPalette.ColorRole.ToolTipBase, QColor("#ffffdc"))
        palette.setColor(group, QPalette.ColorRole.ToolTipText, text)
        palette.setColor(group, QPalette.ColorRole.Text, text)
        palette.setColor(group, QPalette.ColorRole.Button, button)
        palette.setColor(group, QPalette.ColorRole.ButtonText, text)
        palette.setColor(group, QPalette.ColorRole.BrightText, QColor("#d9534f"))
        palette.setColor(group, QPalette.ColorRole.Link, highlight)
        palette.setColor(group, QPalette.ColorRole.Highlight, highlight)
        palette.setColor(group, QPalette.ColorRole.HighlightedText, highlighted_text)
        palette.setColor(group, QPalette.ColorRole.PlaceholderText, disabled)
        palette.setColor(group, QPalette.ColorRole.Mid, border)
        palette.setColor(group, QPalette.ColorRole.Dark, QColor("#9aa0a6"))
        palette.setColor(group, QPalette.ColorRole.Shadow, QColor("#b0b4ba"))
        palette.setColor(group, QPalette.ColorRole.Light, QColor("#fbfaf7"))
        palette.setColor(group, QPalette.ColorRole.Midlight, QColor("#e6e9ee"))

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    return palette


def _apply_explicit_palette(app: QApplication, dark: bool) -> bool:
    """把"我们自己的"调色板装上去（深浅两套都是自建），返回是否成功。

    同时更新两个**各自独立**的标志：
      · ``_PALETTE_OVERRIDDEN``        —— 我们现在控制着调色板吗
      · ``_PALETTE_PRESENTED_DARK``    —— 装上去的这套是深色吗
    分开存是因为：浅色虽然也是我们装的，但它与 Qt 原生浅色外观一致，
    记成"未覆盖"可以让原生样式继续绘制按钮等控件；把两者混在一个标志里
    会迫使 ``current_scheme()`` 退回 "hint" 判据，而 Windows 上 hint 与真实
    调色板经常不一致（真机实测：hint=dark、Window 亮度=206 → 白字配浅底）。
    """
    global _PALETTE_OVERRIDDEN, _PALETTE_PRESENTED_DARK
    if dark:
        try:
            # 以**系统标准板**为底（而不是当前调色板），
            # 避免"深→浅→深"反复切换时把上一轮的颜色带进来
            app.setPalette(_build_dark_palette(app.style().standardPalette()))
        except (AttributeError, TypeError, RuntimeError):
            _PALETTE_OVERRIDDEN = False
            _PALETTE_PRESENTED_DARK = False
            return False
        _PALETTE_OVERRIDDEN = True
        _PALETTE_PRESENTED_DARK = True
        return True
    try:
        app.setPalette(_build_light_palette(app.style().standardPalette()))
    except (AttributeError, TypeError, RuntimeError):
        _PALETTE_OVERRIDDEN = False
        _PALETTE_PRESENTED_DARK = False
        return False
    # 浅色：调色板也是我们装的，但它与 Qt 原生浅色外观一致，
    # 所以记录为"未覆盖"（让原生样式继续绘制），同时明暗记录为浅色。
    _PALETTE_OVERRIDDEN = False
    _PALETTE_PRESENTED_DARK = False
    return True


def apply_theme(app: Optional[QApplication], mode: object, force_palette: bool = False) -> str:
    """把主题模式应用到整个应用，返回规范化之后的模式。

    **N3.0（按用户方案重写）：「跟随系统」不再"把调色板交还系统"，而是
    先读操作系统当前是深是浅，然后套用我们自己那套对应的配色。**

    为什么必须这样：交还系统会让**系统调色板 / 我们的调色板 / 菜单栏自己那份
    调色板**三套并存、互相打架，真机上表现为"深色 → 跟随系统后菜单栏独立变黑、
    文字白色"这类无法收敛的怪象。现在全局任何时刻只有**一套**调色板，
    所有控件（菜单栏 / 工具栏 / 日志区 / 左栏）都走同一条已验证的逻辑。

    · 跟随系统：`os_scheme()` 读系统偏好 → 解析成深/浅 → 与手动选择完全相同
    · 浅色/深色：`setColorScheme()` + 无条件装自建调色板
    · `force_palette=True` 保留为兼容参数（现在默认就装调色板）
    """
    global _ACTIVE_MODE, _RESOLVED_MODE
    global _PALETTE_OVERRIDDEN, _PALETTE_PRESENTED_DARK, _LAST_APPLY_MSG
    normalized = normalize_mode(mode)
    _ACTIVE_MODE = normalized
    if app is None:
        _RESOLVED_MODE = normalized
        return normalized

    # 「跟随系统」解析成实色方案（只读系统偏好，不受我们自己的 setColorScheme 影响）
    if normalized == MODE_SYSTEM:
        resolved = MODE_DARK if os_scheme() == "dark" else MODE_LIGHT
    else:
        resolved = normalized
    _RESOLVED_MODE = resolved

    dark = resolved == MODE_DARK
    want = Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light
    try:
        app.styleHints().setColorScheme(want)
    except (AttributeError, TypeError, RuntimeError):
        pass

    # 无条件装自建调色板：这是"窗口底色也跟着变"的唯一可靠保证，
    # 也是"跟随系统"能稳定工作的前提（不再有第二套系统调色板掺和）。
    _apply_explicit_palette(app, dark)

    # N2.1：装完回读实际亮度，**仅用于诊断**。
    # 不再用它去改写"谁拥有调色板"——浅色用的是自建浅色调色板，
    # 一旦把标志清掉，current_scheme() 会退回 hint 判据，
    # 而 Windows 上 hint 常与真实调色板不一致 → "文字色"与"底色"来自两套配色。
    measured_dark = palette_is_dark(app)
    prefix = "跟随系统→{}".format("深色" if dark else "浅色") \
        if normalized == MODE_SYSTEM else ("深色" if dark else "浅色")
    if measured_dark == dark:
        _LAST_APPLY_MSG = "{}：调色板已生效，Window 亮度 {}".format(
            prefix, palette_window_lightness(app)
        )
    else:
        _LAST_APPLY_MSG = "{}：警告，期望{}但 Window 亮度 {}".format(
            prefix, "深色" if dark else "浅色", palette_window_lightness(app)
        )

    _refresh_widget_styles(app)
    return normalized


def _refresh_widget_styles(app: QApplication) -> None:
    """强刷所有已有控件，避免"部分控件还是旧色"。"""
    try:
        for widget in app.allWidgets():
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
    except (AttributeError, TypeError, RuntimeError):
        pass


# ---------------------------------------------------------------------------
# 颜色令牌
# ---------------------------------------------------------------------------

def muted_text_color(widget: Optional[QWidget] = None) -> str:
    """次要文字颜色（跟随主题）。"""
    return MUTED_DARK if is_dark(widget) else MUTED_LIGHT


def status_color(key: object, default: str = COLOR_IDLE) -> str:
    """状态 -> 颜色（未知状态返回 default）。"""
    text = str(key or "").strip().lower()
    return STATUS_COLORS.get(text, default)


def status_colors() -> Dict[str, str]:
    """状态色表的副本（供各模块建立自己的映射）。"""
    return dict(STATUS_COLORS)


# ---------------------------------------------------------------------------
# 日志区还原终端颜色（ANSI）
# ---------------------------------------------------------------------------

def log_colors(widget: Optional[QWidget] = None) -> Tuple[str, str]:
    """日志区当前的 (底色, 文字色)。"""
    if is_dark(widget):
        return LOG_DARK_BG, LOG_DARK_TEXT
    return LOG_LIGHT_BG, LOG_LIGHT_TEXT


def ansi_palette(widget: Optional[QWidget] = None) -> Tuple[str, ...]:
    """当前主题的 ANSI 16 色。"""
    return ANSI_DARK_COLORS if is_dark(widget) else ANSI_LIGHT_COLORS


def ansi_index_rgb(index: int) -> Tuple[int, int, int]:
    """xterm 256 色索引 → RGB（纯函数，方便单测）。

    16-231 是 6×6×6 色立方，232-255 是 24 级灰阶；
    0-15 是"基础 16 色"，这里给一份标准 xterm 值兜底
    （实际会先查主题色板，走不到这里）。
    """
    try:
        value = max(0, min(255, int(index)))
    except (TypeError, ValueError):
        return (0, 0, 0)
    if value < 16:
        base = (
            (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
            (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
            (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
            (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
        )
        return base[value]
    if value < 232:
        value -= 16
        return (
            ANSI_CUBE_LEVELS[value // 36],
            ANSI_CUBE_LEVELS[(value // 6) % 6],
            ANSI_CUBE_LEVELS[value % 6],
        )
    gray = 8 + (value - 232) * 10
    return (gray, gray, gray)


def color_luminance(rgb: Tuple[int, int, int]) -> float:
    """近似相对亮度（0=黑，1=白）。不做 sRGB 线性化 —— 这里只用来判断"太亮/太暗"。"""
    red, green, blue = (max(0, min(255, int(v))) / 255.0 for v in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def readable_rgb(rgb: Tuple[int, int, int], dark: bool) -> Tuple[int, int, int]:
    """把一个颜色调整到"当前日志底色上读得清"。

    终端色板默认是给黑底设计的：`ESC[97m`（亮白）在浅色日志区上完全看不见，
    而 `ESC[30m`（黑）在深色日志区上同样看不见。这里按底色做单向修正：

      · 浅色底：亮度超过阈值 → 逐步压暗（往黑色靠）；
      · 深色底：亮度低于阈值 → 逐步提亮（往白色靠）。

    只动"读不清"的颜色，正常颜色原样保留 —— 所以终端里是什么观感，日志区基本一致。
    """
    values = [max(0, min(255, int(v))) for v in rgb]
    if dark:
        steps = 0
        while color_luminance(tuple(values)) < ANSI_DARK_MIN_LUMA and steps < 8:
            values = [int(round(v + (255 - v) * 0.35)) for v in values]
            steps += 1
    else:
        steps = 0
        while color_luminance(tuple(values)) > ANSI_LIGHT_MAX_LUMA and steps < 8:
            values = [int(round(v * 0.72)) for v in values]
            steps += 1
    return (values[0], values[1], values[2])


def _hex_color(rgb: Tuple[int, int, int]) -> str:
    """(r, g, b) → `#rrggbb`。"""
    return "#{:02x}{:02x}{:02x}".format(*[max(0, min(255, int(v))) for v in rgb])


def ansi_color(value: object, widget: Optional[QWidget] = None) -> str:
    """把 `app.ansi.AnsiColor` 换成当前主题下的十六进制颜色。

    认不出来 / 传 None 时返回空串，调用方按"用日志区默认文字色"处理。
    这里不 import app.ansi：只需要对象有 `kind` / `index` / `rgb` 三个属性，
    这样 theme 依旧只依赖 PyQt6。
    """
    if value is None:
        return ""
    kind = str(getattr(value, "kind", "") or "")
    dark = is_dark(widget)
    if kind == "index":
        try:
            index = int(getattr(value, "index", -1))
        except (TypeError, ValueError):
            return ""
        if 0 <= index < 16:
            return ansi_palette(widget)[index]
        if 0 <= index < 256:
            return _hex_color(readable_rgb(ansi_index_rgb(index), dark))
        return ""
    if kind == "rgb":
        raw = getattr(value, "rgb", None) or (0, 0, 0)
        try:
            rgb = (int(raw[0]), int(raw[1]), int(raw[2]))
        except (TypeError, ValueError, IndexError):
            return ""
        return _hex_color(readable_rgb(rgb, dark))
    return ""


def mix_colors(first: str, second: str, ratio: float) -> str:
    """把两个 `#rrggbb` 按比例混合（ratio=0 取 first，=1 取 second）。

    用途：ANSI 的"暗淡"（`ESC[2m`）在日志区表现为"文字色往底色靠一点"。
    """
    try:
        ratio = max(0.0, min(1.0, float(ratio)))
        one = QColor(first)
        two = QColor(second)
        if not one.isValid() or not two.isValid():
            return first
        return _hex_color((
            one.red() + (two.red() - one.red()) * ratio,
            one.green() + (two.green() - one.green()) * ratio,
            one.blue() + (two.blue() - one.blue()) * ratio,
        ))
    except (TypeError, ValueError, RuntimeError):
        return first


def focus_border_color(widget: Optional[QWidget] = None) -> str:
    """窗格焦点边框色。"""
    return FOCUS_BORDER_DARK if is_dark(widget) else FOCUS_BORDER_LIGHT


def palette_color(widget: Optional[QWidget], role: QPalette.ColorRole, fallback: str) -> str:
    """从调色板取色（取不到时用 fallback）。"""
    try:
        palette = widget.palette() if widget is not None else QApplication.palette()
        return palette.color(role).name()
    except (AttributeError, TypeError, RuntimeError):
        return fallback


def nav_palette(widget: Optional[QWidget] = None) -> Dict[str, str]:
    """左栏 QSS 需要的全部颜色（深色/浅色各一套，浅色尽量贴近原生）。"""
    if is_dark(widget):
        return {
            "base": palette_color(widget, QPalette.ColorRole.Base, DARK_BASE),
            "text": palette_color(widget, QPalette.ColorRole.WindowText, DARK_TEXT),
            "border": palette_color(widget, QPalette.ColorRole.Mid, DARK_BORDER),
            "sel": palette_color(widget, QPalette.ColorRole.Highlight, DARK_HIGHLIGHT),
            "sel_text": palette_color(
                widget, QPalette.ColorRole.HighlightedText, DARK_HIGHLIGHT_TEXT
            ),
            "hover": palette_color(
                widget, QPalette.ColorRole.AlternateBase, DARK_ALTERNATE
            ),
            "button": palette_color(widget, QPalette.ColorRole.Button, DARK_BUTTON),
            "disabled": _disabled_text_color(widget),
        }
    return {
        "base": NAV_LIGHT_BG,
        "text": NAV_LIGHT_SELECTED_TEXT,
        "border": NAV_LIGHT_BORDER,
        "sel": NAV_LIGHT_SELECTED_BG,
        "sel_text": NAV_LIGHT_SELECTED_TEXT,
        "hover": NAV_LIGHT_HOVER_BG,
        "button": palette_color(widget, QPalette.ColorRole.Button, "#f0f0f0"),
        "disabled": _disabled_text_color(widget),
    }


def _disabled_text_color(widget: Optional[QWidget] = None) -> str:
    """禁用态文字色。"""
    try:
        palette = widget.palette() if widget is not None else QApplication.palette()
        return palette.color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText
        ).name()
    except (AttributeError, TypeError, RuntimeError):
        return "#7a7a7a"


def log_colors(widget: Optional[QWidget] = None) -> Tuple[str, str, str, str]:
    """日志区四色：(背景, 文字, 边框, 选中背景)。

    N2.3 定稿：**只用我们自己的常量**，不再从 `app.palette()` 推导。
    原因（真机实测）：调色板到底有没有被 Qt 采用、什么时候采用，不受我们控制；
    日志区作为"主要内容区"，配色必须可预测 —— 深色深底浅字、浅色柔和黄灰底近黑字。
    跟随系统时用系统给的明暗（`is_dark()`）来二选一。
    """
    if is_dark(widget):
        return LOG_DARK_BG, LOG_DARK_TEXT, LOG_DARK_BORDER, LOG_DARK_SELECTION
    return LOG_LIGHT_BG, LOG_LIGHT_TEXT, LOG_LIGHT_BORDER, LOG_LIGHT_SELECTION


def _contrast_ok(color_a: str, color_b: str, min_delta: int = 40) -> bool:
    """两个颜色的亮度差是否够看（防止"白底白字"这类不可读的组合）。"""
    try:
        light_a = QColor(color_a).lightness()
        light_b = QColor(color_b).lightness()
        return abs(light_a - light_b) >= min_delta
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# 样式表
# ---------------------------------------------------------------------------

def apply_log_palette(editor: "QPlainTextEdit", dark: bool) -> Tuple[str, str, str, str]:
    """把日志区配色**直接设到控件自己的调色板**上，并强制重绘（N2.3 定稿）。

    为什么不能只靠样式表：真机上出现过"样式串设对了、Qt 也收到了，
    但界面不重绘 / 被父控件重新 polish 覆盖"的情况（日志区永远停在启动时的颜色）。
    控件调色板是 Qt 一定会采用的那条路，配合 viewport().update() 保证立刻重绘。

    返回实际使用的 (背景, 文字, 边框, 选中背景)。
    """
    bg, fg, border, sel = (
        (LOG_DARK_BG, LOG_DARK_TEXT, LOG_DARK_BORDER, LOG_DARK_SELECTION)
        if dark
        else (LOG_LIGHT_BG, LOG_LIGHT_TEXT, LOG_LIGHT_BORDER, LOG_LIGHT_SELECTION)
    )
    try:
        palette = QPalette(editor.palette())
        for role, color in (
            (QPalette.ColorRole.Base, bg),
            (QPalette.ColorRole.Window, bg),
            (QPalette.ColorRole.Text, fg),
            (QPalette.ColorRole.WindowText, fg),
            (QPalette.ColorRole.Highlight, sel),
            (QPalette.ColorRole.HighlightedText, HIGHLIGHTED_TEXT),
        ):
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                palette.setColor(group, role, QColor(color))
        palette.setColor(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(DISABLED_LOG_TEXT)
        )
        editor.setPalette(palette)
        # 关键加固（真机反馈"深色下日志文字是黑的"）：
        # 只设调色板时，Qt 可能因为该控件**已经被 setStyleSheet 过**而优先用样式表
        # 里的默认色（我们此前只写了 border），于是文字回退成系统默认的黑色。
        # 把颜色**同时**写进样式表和调色板，两条路指向同一个明确颜色，
        # 无论 Qt 走哪条都不会再出现黑字。
        editor.setStyleSheet(
            "QPlainTextEdit#programLog {{"
            " background-color: {bg};"
            " color: {fg};"
            " selection-background-color: {sel};"
            " selection-color: {sel_text};"
            " border: 1px solid {border};"
            " border-radius: 3px;"
            "}}".format(bg=bg, fg=fg, sel=sel, sel_text=HIGHLIGHTED_TEXT, border=border)
        )
        viewport = editor.viewport()
        if viewport is not None:
            # 样式表作用于 viewport，只 update() 主控件在部分平台上不重绘
            viewport.setPalette(palette)
            viewport.update()
        editor.update()
    except (AttributeError, TypeError, RuntimeError):
        pass
    return bg, fg, border, sel


def chrome_palette(widget: Optional[QWidget] = None) -> Dict[str, str]:
    """界面"外壳"（菜单栏/工具栏/按钮/标签）的配色（N2.5）。

    这些控件**不是**我们 QSS 的目标，它们直接读调色板；而 Windows 上
    Qt 的原生样式在"跟随系统"时常常给出与背景不匹配的文字色
    （真机症状：浅色界面里工具栏/按钮文字发白；深色界面里菜单栏看不清）。
    所以这里把它们的颜色**全部显式指定**，不依赖调色板。
    """
    if is_dark(widget):
        return {
            "text": DARK_TEXT,
            "muted": MUTED_DARK,
            "button": DARK_BUTTON,
            "button_text": DARK_TEXT,
            "border": DARK_BORDER,
            "hover": DARK_ALTERNATE,
            "sel": DARK_HIGHLIGHT,
            "sel_text": DARK_HIGHLIGHT_TEXT,
            "disabled": DARK_DISABLED_TEXT,
            "tooltip_bg": DARK_BASE,
            "chrome_bg": DARK_WINDOW,
        }
    # N3.0：跟随系统已解析成深浅之一，is_dark() 已经给出正确结果，
    # 因此这里不再需要单独的"系统分支"（少一条分支就少一处 bug）。
    return {
        "text": LIGHT_TEXT,
        "muted": MUTED_LIGHT,
        "button": LIGHT_BUTTON,
        "button_text": LIGHT_TEXT,
        "border": LIGHT_BORDER,
        "hover": LIGHT_ALTERNATE,
        "sel": LIGHT_HIGHLIGHT,
        "sel_text": LIGHT_HIGHLIGHT_TEXT,
        "disabled": LIGHT_DISABLED_TEXT,
        "tooltip_bg": "#fbfaf7",
        "chrome_bg": LIGHT_WINDOW,
    }


def _system_chrome_palette(widget: Optional[QWidget] = None) -> Dict[str, str]:
    """跟随系统模式下的外壳配色：从当前调色板取色，并做对比度兜底（N2.9）。"""
    try:
        target = widget if widget is not None else QApplication.instance()
        palette = target.palette() if target is not None else None
        if palette is not None:
            bg = palette.color(QPalette.ColorRole.Window)
            text = palette.color(QPalette.ColorRole.WindowText)
            # 对比度不足（例如系统给了深底深字）时，按底色明暗改用黑白
            if abs(bg.lightness() - text.lightness()) < 60:
                text = QColor("#1f1f1f") if bg.lightness() >= 128 else QColor("#e8e8e8")
            base = palette.color(QPalette.ColorRole.Base)
            mid = palette.color(QPalette.ColorRole.Mid)
            button = palette.color(QPalette.ColorRole.Button)
            highlight = palette.color(QPalette.ColorRole.Highlight)
            highlighted_text = palette.color(QPalette.ColorRole.HighlightedText)
            if abs(highlight.lightness() - highlighted_text.lightness()) < 40:
                highlighted_text = QColor("#ffffff")
            return {
                "text": text.name(),
                "muted": text.name(),
                "button": button.name(),
                "button_text": text.name(),
                "border": mid.name(),
                "hover": base.name(),
                "sel": highlight.name(),
                "sel_text": highlighted_text.name(),
                "disabled": mid.name(),
                "tooltip_bg": base.name(),
                "chrome_bg": bg.name(),
            }
    except (AttributeError, TypeError, RuntimeError):
        pass
    # 取不到任何调色板信息时的最后兜底（浅色一套）
    return {
        "text": LIGHT_TEXT, "muted": MUTED_LIGHT, "button": LIGHT_BUTTON,
        "button_text": LIGHT_TEXT, "border": LIGHT_BORDER,
        "hover": LIGHT_ALTERNATE, "sel": LIGHT_HIGHLIGHT,
        "sel_text": LIGHT_HIGHLIGHT_TEXT, "disabled": LIGHT_DISABLED_TEXT,
        "tooltip_bg": "#fbfaf7", "chrome_bg": LIGHT_WINDOW,
    }


def bot_tab_bar_qss(widget: Optional[QWidget] = None) -> str:
    """实例区顶部浏览器式标签栏（QTabBar#botTabBar）的样式。

    为什么要单独一条：QTabBar 是"原生绘制"的部件，不读我们的调色板
    （和菜单栏同一类问题）—— 深色模式下会停在系统默认的浅色外观、文字发黑。
    这里显式指定文字/底色/选中态，颜色全部取自 :func:`chrome_palette`，
    与外层保持同一套配色。
    """
    c = chrome_palette(widget)
    return (
        "QTabBar#botTabBar {{"
        " background-color: {chrome_bg};"
        "}}"
        "QTabBar#botTabBar::tab {{"
        " color: {text};"
        " background-color: {button};"
        " border: 1px solid {border};"
        " border-bottom: none;"
        " border-top-left-radius: 4px;"
        " border-top-right-radius: 4px;"
        " padding: 3px 10px;"
        " margin-right: 2px;"
        " margin-top: 2px;"
        " min-width: 60px;"
        "}}"
        "QTabBar#botTabBar::tab:hover {{"
        " background-color: {hover};"
        "}}"
        "QTabBar#botTabBar::tab:selected {{"
        " color: {sel_text};"
        " background-color: {sel};"
        " border-color: {sel};"
        "}}"
        "QTabBar#botTabBar::tab:disabled {{"
        " color: {disabled};"
        "}}"
        "QTabBar#botTabBar QToolButton {{"
        " color: {muted};"
        " background: transparent;"
        " border: none;"
        "}}"
        "QTabBar#botTabBar QToolButton:hover {{"
        " color: {text};"
        "}}"
    ).format(**c)


def chrome_qss(widget: Optional[QWidget] = None) -> str:
    """菜单栏 + 工具栏 + 通用按钮/标签/输入的配色（N2.5，设在主窗口上）。

    一次性覆盖所有"外壳"控件，避免再出现"某一行文字颜色不跟主题"：
      · QMenuBar / QMenu        —— Windows 原生绘制，不读调色板
      · QToolBar / QToolButton  —— 工具栏
      · QPushButton / QCheckBox —— 面板按钮、窗格按钮
      · QLabel                  —— 各类标签
      · QLineEdit / QComboBox / QSpinBox / QDoubleSpinBox —— 对话框输入
      · QTreeWidget / QTableWidget —— 列表与表格
      · QStatusBar / QToolTip   —— 状态栏与提示
    """
    c = chrome_palette(widget)
    return (
        "QMenuBar {{ color: {text}; background-color: transparent; }}"
        "QMenuBar::item {{ color: {text}; background: transparent; padding: 4px 10px; }}"
        "QMenuBar::item:selected {{ background-color: {hover}; color: {text}; }}"
        "QMenuBar::item:pressed {{ background-color: {sel}; color: {sel_text}; }}"
        "QMenu {{ color: {text}; background-color: {tooltip_bg}; }}"
        "QMenu::item {{ color: {text}; padding: 4px 18px; }}"
        "QMenu::item:selected {{ background-color: {sel}; color: {sel_text}; }}"
        "QMenu::item:disabled {{ color: {disabled}; }}"
        "QMenu::separator {{ height: 1px; background-color: {border}; margin: 4px 8px; }}"
        "QToolBar, QToolBar#botBar {{ color: {text}; background-color: transparent; border: 0; }}"
        "QToolBar QToolButton {{ color: {text}; }}"
        "QToolBar QToolButton:disabled {{ color: {disabled}; }}"
        "QToolBar QToolButton:hover {{ background-color: {hover}; }}"
        "QPushButton {{ color: {button_text}; background-color: {button}; "
        "  border: 1px solid {border}; border-radius: 3px; padding: 3px 10px; }}"
        "QPushButton:hover {{ background-color: {hover}; }}"
        "QPushButton:pressed {{ background-color: {sel}; color: {sel_text}; }}"
        "QPushButton:disabled {{ color: {disabled}; border-color: {border}; }}"
        "QToolButton {{ color: {text}; }}"
        "QCheckBox {{ color: {text}; }}"
        "QRadioButton {{ color: {text}; }}"
        "QLabel {{ color: {text}; background: transparent; }}"
        "QStatusBar {{ color: {text}; }}"
        "QStatusBar QLabel {{ color: {text}; }}"
        "QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{"
        "  color: {text}; background-color: {tooltip_bg}; "
        "  border: 1px solid {border}; border-radius: 3px; padding: 2px 4px; }}"
        "QLineEdit:disabled, QComboBox:disabled {{ color: {disabled}; }}"
        "QTreeWidget, QTableWidget {{ color: {text}; }}"
        "QTreeWidget::item, QTableWidget::item {{ color: {text}; }}"
        "QToolTip {{ color: {text}; background-color: {tooltip_bg}; "
        "  border: 1px solid {border}; }}"
    ).format(**c)


def apply_menubar_palette(menu_bar, widget: Optional[QWidget] = None) -> None:
    """把配色**直接设到菜单栏与各下拉菜单的调色板**上（N2.8）。

    为什么必须这样（真机实测的坑）：菜单栏的文字色如果只在 QSS 里写
    （`QMenuBar::item { color: ... }`），Qt 会把它**合进菜单栏调色板**，
    而那个调色板是在"第一次被 setStyleSheet/setPalette"时定下来的；
    之后即使我们再 setStyleSheet 新颜色，菜单栏也**不重新取色** ——
    表现为"一旦某刻是浅色，之后深色模式下菜单栏永远是黑字"。
    直接设 palette 可以绕开这个缓存。
    """
    c = chrome_palette(widget)
    targets = [menu_bar]
    try:
        for action in menu_bar.actions():
            menu = action.menu()
            if menu is not None:
                targets.append(menu)
    except (AttributeError, RuntimeError):
        pass
    for target in targets:
        if target is None:
            continue
        try:
            palette = QPalette(target.palette())
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                palette.setColor(group, QPalette.ColorRole.Window, QColor(c["chrome_bg"]))
                palette.setColor(group, QPalette.ColorRole.WindowText, QColor(c["text"]))
                palette.setColor(group, QPalette.ColorRole.Button, QColor(c["chrome_bg"]))
                palette.setColor(group, QPalette.ColorRole.ButtonText, QColor(c["text"]))
                palette.setColor(group, QPalette.ColorRole.Text, QColor(c["text"]))
                palette.setColor(group, QPalette.ColorRole.Base, QColor(c["tooltip_bg"]))
                palette.setColor(group, QPalette.ColorRole.Highlight, QColor(c["sel"]))
                palette.setColor(
                    group, QPalette.ColorRole.HighlightedText, QColor(c["sel_text"])
                )
            palette.setColor(
                QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.WindowText,
                QColor(c["disabled"]),
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["disabled"])
            )
            target.setPalette(palette)
            style = target.style()
            style.unpolish(target)
            style.polish(target)
            target.update()
        except (AttributeError, TypeError, RuntimeError):
            continue


def menubar_qss(widget: Optional[QWidget] = None) -> str:
    """菜单栏 / 菜单样式（N2.8）。

    **颜色只取自 chrome_palette（我们自己的常量）**，不再读控件调色板 ——
    真机教训：读调色板时，菜单栏的颜色会在"第一次取值"后被 Qt 缓存住，
    导致切换主题后菜单栏永远是同一个颜色（黑字/白字残留）。
    这里只负责内边距与悬停底色，文字色由 apply_menubar_palette 直接设进调色板。
    """
    c = chrome_palette(widget)
    return (
        "QMenuBar {{ color: {text}; background-color: {chrome_bg}; }}"
        "QMenuBar::item {{ color: {text}; background: transparent; padding: 4px 10px; }}"
        "QMenuBar::item:selected {{ background-color: {hover}; color: {text}; }}"
        "QMenuBar::item:pressed {{ background-color: {sel}; color: {sel_text}; }}"
        "QMenu {{ color: {text}; background-color: {tooltip_bg}; }}"
        "QMenu::item {{ color: {text}; background: transparent; padding: 4px 18px; }}"
        "QMenu::item:selected {{ background-color: {sel}; color: {sel_text}; }}"
        "QMenu::item:disabled {{ color: {disabled}; }}"
    ).format(**c)


def log_editor_qss(widget: Optional[QWidget] = None) -> str:
    """日志区（QPlainTextEdit#programLog）样式。"""
    background, text, border, selection = log_colors(widget)
    return (
        "QPlainTextEdit#programLog {{"
        " background-color: {bg};"
        " color: {fg};"
        " border: 1px solid {border};"
        " border-radius: 3px;"
        " selection-background-color: {sel};"
        "}}"
    ).format(bg=background, fg=text, border=border, sel=selection)


#: 分支箭头图片缓存（键 = (颜色, 方向)），避免每次刷样式都重绘
_BRANCH_ARROW_CACHE: Dict[Tuple[str, str], str] = {}


def _branch_arrow_url(color: str, direction: str) -> str:
    """生成分支箭头图片的 url(...)（N6 定稿）。

    为什么不用内联 SVG：`url(data:image/svg+xml;utf8,<svg …>)` 里含
    `:` `'` `,` `<` `>`，Qt 的样式表解析器会**整段拒绝**
    （真机现象：控制台刷 `Could not parse stylesheet of object navPanel`，
    该控件所有样式全部失效 → 深色下箭头/文字发黑）。
    改用 QPainter 画一张 12×12 的 PNG 存到临时目录，再用普通文件路径引用 ——
    这条路 Qt 一定认，颜色也完全由我们控制。

    取不到 QApplication / 无法写临时文件时返回空串（调用方会跳过该规则）。
    """
    key = (str(color), str(direction))
    cached = _BRANCH_ARROW_CACHE.get(key)
    if cached is not None:
        return cached

    path = ""
    try:
        import tempfile

        from PyQt6.QtCore import QPointF, Qt as _Qt
        from PyQt6.QtGui import QColor, QImage, QPainter, QPolygonF

        size = 12
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(_Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(QColor(color))
            painter.setPen(_Qt.PenStyle.NoPen)
            if direction == "down":
                triangle = QPolygonF([
                    QPointF(2.5, 4.0), QPointF(9.5, 4.0), QPointF(6.0, 8.5),
                ])
            else:
                triangle = QPolygonF([
                    QPointF(4.0, 2.5), QPointF(4.0, 9.5), QPointF(8.5, 6.0),
                ])
            painter.drawPolygon(triangle)
        finally:
            painter.end()

        folder = os.path.join(tempfile.gettempdir(), "qqbot_launcher_theme")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "branch_{}_{}.png".format(
            direction, str(color).lstrip("#")))
        if not os.path.exists(path):
            image.save(path, "PNG")
    except (ImportError, OSError, AttributeError, TypeError, RuntimeError, ValueError):
        path = ""

    url = ""
    if path and os.path.exists(path):
        # QSS 里路径必须用正斜杠
        url = "url({})".format(path.replace("\\", "/"))
    _BRANCH_ARROW_CACHE[key] = url
    return url


def nav_branch_qss(widget: Optional[QWidget] = None) -> str:
    """左栏树的分支箭头样式（颜色由主题决定，不依赖原生绘制）。

    只写 ``::branch { background: transparent; }`` 时，箭头由**原生样式**绘制，
    它会去读列表自己的调色板角色 —— 真机表现就是"深色模式下箭头是黑的"。
    这里用 `_branch_arrow_url()` 生成的 PNG 把箭头颜色固定成主题色。
    """
    if is_dark(widget):
        color = DARK_TEXT
    else:
        color = "#5a5a5a"
    right = _branch_arrow_url(color, "right")
    down = _branch_arrow_url(color, "down")
    rules = "QTreeWidget#navTree::branch {{ background: transparent; }}"
    # 取不到箭头图片时**不下发 image 规则** —— 宁可用原生箭头，
    # 也好过让 Qt 解析失败导致整段样式表失效。
    if right:
        rules += (
            "QTreeWidget#navTree::branch:has-children:!has-siblings:closed,"
            "QTreeWidget#navTree::branch:closed:has-children:has-siblings {{"
            "  image: {right};"
            "}}"
        ).format(right=right)
    if down:
        rules += (
            "QTreeWidget#navTree::branch:open:has-children:!has-siblings,"
            "QTreeWidget#navTree::branch:open:has-children:has-siblings {{"
            "  image: {down};"
            "}}"
        ).format(down=down)
    return rules


def nav_tree_qss(widget: Optional[QWidget] = None) -> str:
    """左侧竖栏面板样式（含列表、按钮、标签）。

    深色下把列表底色、按钮底色都对齐调色板，避免出现"白底黑字"的突兀区块；
    浅色下只给列表加一层轻边框，其余交给系统原生外观。
    """
    colors = nav_palette(widget)
    # 箭头样式由 nav_branch_qss 生成（内含单花括号），必须**先转义**再参与外层
    # format —— 否则 "{{ background: transparent; }}" 会被当成占位符，
    # 直接抛 KeyError: ' background'（静态检查 tools\check_branch_qss.py 会拦住）。
    branch = nav_branch_qss(widget).replace("{", "{{").replace("}", "}}")
    colors = dict(colors)
    colors["branch"] = branch
    if not is_dark(widget):
        return (
            "QTreeWidget#navTree {{"
            "  border: 1px solid {border};"
            "  border-radius: 3px;"
            "  background-color: {base};"
            "  color: {text};"
            "  outline: 0;"
            "}}"
            "QTreeWidget#navTree::item {{ padding: 3px 2px; }}"
            "QTreeWidget#navTree::item:selected {{"
            "  background-color: {sel};"
            "  color: {sel_text};"
            "}}"
            "QTreeWidget#navTree::item:hover {{ background-color: {hover}; }}"
            "{branch}"
        ).format(**colors)

    return (
        "QTreeWidget#navTree {{"
        "  border: 1px solid {border};"
        "  border-radius: 3px;"
        "  background-color: {base};"
        "  color: {text};"
        "  outline: 0;"
        "}}"
        "QTreeWidget#navTree::item {{ padding: 3px 2px; }}"
        "QTreeWidget#navTree::item:selected {{"
        "  background-color: {sel};"
        "  color: {sel_text};"
        "}}"
        "QTreeWidget#navTree::item:hover {{ background-color: {hover}; }}"
        "{branch}"
        "QWidget#navPanel QPushButton {{"
        "  color: {text};"
        "  background-color: {button};"
        "  border: 1px solid {border};"
        "  border-radius: 3px;"
        "  padding: 3px 8px;"
        "}}"
        "QWidget#navPanel QPushButton:hover {{"
        "  background-color: {hover};"
        "  border-color: {sel};"
        "}}"
        "QWidget#navPanel QPushButton:pressed {{ background-color: {base}; }}"
        "QWidget#navPanel QPushButton:disabled {{"
        "  color: {disabled};"
        "  border-color: {border};"
        "}}"
        "QWidget#navPanel QLabel {{ color: {text}; }}"
    ).format(**colors)


def pane_qss(widget: Optional[QWidget] = None) -> str:
    """窗格（PaneWidget）样式：焦点边框 + 标题栏底色。"""
    border = focus_border_color(widget)
    if is_dark(widget):
        panel = palette_color(widget, QPalette.ColorRole.AlternateBase, DARK_ALTERNATE)
        text = palette_color(widget, QPalette.ColorRole.WindowText, DARK_TEXT)
        separator = palette_color(widget, QPalette.ColorRole.Mid, DARK_BORDER)
    else:
        panel = NAV_LIGHT_HOVER_BG
        text = NAV_LIGHT_SELECTED_TEXT
        separator = NAV_LIGHT_BORDER
    return (
        "QWidget#paneTitleBar {{"
        "  background-color: {panel};"
        "  border-bottom: 1px solid {separator};"
        "}}"
        "QWidget#paneTitleBar QLabel {{ color: {text}; }}"
        "QWidget#paneTitleBar QPushButton {{ padding: 1px 6px; }}"
        "QWidget#paneTabs::pane {{ border: 1px solid {separator}; }}"
        "QWidget[focused=\"true\"] {{ border: 1px solid {border}; }}"
    ).format(panel=panel, text=text, separator=separator, border=border)


def dialog_hint_qss(widget: Optional[QWidget] = None) -> str:
    """对话框里次要说明文字的样式。"""
    return "color: {};".format(muted_text_color(widget))


def placeholder_qss(widget: Optional[QWidget] = None) -> str:
    """空状态占位页的提示文字样式。"""
    return "color: {};".format(muted_text_color(widget))


def style_sheet_for(name: str, widget: Optional[QWidget] = None) -> str:
    """按名字取样式表（方便统一刷新时遍历）。"""
    table = {
        "log": log_editor_qss,
        "nav": nav_tree_qss,
        "pane": pane_qss,
        "muted": dialog_hint_qss,
    }
    builder = table.get(str(name or "").strip().lower())
    return builder(widget) if builder is not None else ""


# ---------------------------------------------------------------------------
# 自检（不需要 QApplication）
# ---------------------------------------------------------------------------

def _selftest() -> int:
    # 1) 模式规范化
    assert normalize_mode("dark") == MODE_DARK
    assert normalize_mode("DARK") == MODE_DARK
    assert normalize_mode("深色") == MODE_DARK
    assert normalize_mode("light") == MODE_LIGHT
    assert normalize_mode("浅色模式") == MODE_LIGHT
    assert normalize_mode("") == MODE_SYSTEM
    assert normalize_mode(None) == MODE_SYSTEM
    assert normalize_mode("乱七八糟") == MODE_SYSTEM
    print("[1] normalize_mode 正常：", [(m, normalize_mode(m)) for m in
                                        ("dark", "light", "", "xyz")])

    # 2) 菜单文字
    assert mode_label("dark") == "深色模式"
    assert mode_label("light") == "浅色模式"
    assert mode_label("system") == "跟随系统"
    assert len(THEME_MODES) == 3
    print("[2] 三种模式文字：", [mode_label(m) for m in THEME_MODES])

    # 3) 状态色
    assert status_color("running") == COLOR_RUNNING
    assert status_color("FAILED") == COLOR_FAILED
    assert status_color("partial") == COLOR_STARTING
    assert status_color("不认识") == COLOR_IDLE
    assert status_color("x", "#123456") == "#123456"
    print("[3] 状态色：running =", status_color("running"),
          "| failed =", status_color("failed"), "| 未知 ->", status_color("x"))

    # 4) 样式表字符串（不依赖调色板也能生成）
    log_qss = log_editor_qss(None)
    nav_qss = nav_tree_qss(None)
    pane_qss_text = pane_qss(None)
    assert "QPlainTextEdit#programLog" in log_qss
    assert "QTreeWidget#navTree" in nav_qss
    assert 'QWidget[focused="true"]' in pane_qss_text
    assert "{" not in log_qss.split("}")[0] or "background-color" in log_qss
    print("[4] 样式表生成正常，日志区长度 =", len(log_qss),
          "| 左栏长度 =", len(nav_qss), "| 窗格长度 =", len(pane_qss_text))

    # 5) 模式记忆
    global _ACTIVE_MODE
    keep = _ACTIVE_MODE
    _ACTIVE_MODE = MODE_DARK
    assert current_mode() == MODE_DARK
    assert is_dark(None) is True, "显式深色时 is_dark 必须为真"
    _ACTIVE_MODE = MODE_LIGHT
    assert is_dark(None) is False, "显式浅色时 is_dark 必须为假"
    _ACTIVE_MODE = keep
    print("[5] 模式记忆与 is_dark 一致")

    # 6) 深色调色板构造（不需要 QApplication）
    palette = _build_dark_palette()
    window = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.WindowText)
    assert window.lightness() < 128, window.lightness()
    assert text.lightness() > window.lightness(), (text.lightness(), window.lightness())
    assert palette.color(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
    ).name() == DARK_DISABLED_TEXT
    print("[6] 深色调色板：Window =", window.name(),
          "| WindowText =", text.name(),
          "| Highlight =", palette.color(QPalette.ColorRole.Highlight).name())

    # 7) apply_theme(None) 不炸
    assert apply_theme(None, "dark") == MODE_DARK
    assert current_mode() == MODE_DARK
    _ACTIVE_MODE = keep
    print("[7] apply_theme(None, ...) 安全返回")

    print("\ntheme.py 自检通过：模式规范化、菜单文字、状态色、三种样式表、"
          "模式记忆、深色调色板、无应用实例时的安全性均正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
