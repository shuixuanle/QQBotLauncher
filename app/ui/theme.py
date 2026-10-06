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

import datetime
import json
import os
import re
from typing import Dict, List, Optional, Tuple

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
#: 深色主题的边框 / 分隔线色。
#: 原来是 #3a3d41（与背景 #2b2b2b 的对比度只有 1.30）—— 真机反馈"深色模式下
#: 工具栏那条浅色分割线看不见"。提到 #45484c 后对比度 1.54，和浅色主题的
#: #c6c3b8 / #f0efe9（1.53）基本一致，两套看起来一样清楚。
DARK_BORDER = "#45484c"
DARK_HIGHLIGHT = "#2f6fb5"
DARK_HIGHLIGHT_TEXT = "#ffffff"
DARK_DISABLED_TEXT = "#7a7a7a"

#: 浅色界面用的基础色（N2.4：柔和黄灰，不用纯白 —— 纯白刺眼；
#: 也不用 Windows 标准板的 #ced0d4（发灰发脏））
LIGHT_WINDOW = "#f0efe9"
#: 列表 / 输入区底色。真机需求原话："把左侧 bot 实例部分的颜色改成淡灰 f4f4f4" ——
#: 比窗体（#f0efe9）略亮一点点，于是左侧列表能"看出来是一块列表"；
#: 以前浅色下这里读的是系统调色板的 Base（纯白），根本不受我们控制。
LIGHT_BASE = "#f4f4f4"
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

#: 日志区（浅色）：**故意与窗口/左栏同色**（`LIGHT_WINDOW` == `#f0efe9`）。
#:
#: 真机确认（用户实测后明确要求保留）：浅色下日志区是"浅黄灰"而不是纯白，
#: 这样它和窗体连成一片，不会在浅色界面里"挖"出一块刺眼的白方块。
#: 深色下才让它比窗口（#2b2b2b）更深，用 #1e1f22 形成层次。
#: ⚠️ 别把它"修"成白色 —— 那不是 bug，是有意为之。
LOG_LIGHT_BG = "#f0efe9"
LOG_LIGHT_TEXT = "#1f1f1f"

#: 日志区还原终端颜色用的 ANSI 16 色（顺序就是 ANSI 的 0-15：
#: 前 8 个基础色 黑红绿黄蓝品红青白，后 8 个是对应的亮色）。
#:
#: 两套的目标不一样，别把它们改成同一份：
#:   · **深色**照搬 Windows Terminal 的 Campbell 配色 —— 用户在终端里看到什么色，
#:     日志区就是什么色（真机日志里 `ESC[32m` 的 INFO 行两边都是绿色）；
#:   · **浅色**必须在 `#f0efe9` 上读得清，所以整体压暗。
#:
#: 浅色板是按 **WCAG 对比度**定的（真机反馈："浅色模式下带颜色的字体颜色深些观感更好"）：
#:   · 基础色 1-6：对比度 ≥ 8:1 —— 与正文同级，长时间看日志不累眼；
#:   · 亮色 9-14：对比度 ≈ 6:1 —— 仍然"比基础色亮一档"，保住终端的明暗语义；
#:   · 灰阶（0/7/8/15）**故意反着来**：终端里的"亮白 #f2f2f2"放浅底上等于隐形，
#:     这里换成深灰 #333333；"黑 #0c0c0c"仍是 #1f1f1f（与正文同色）。
#: 改这几个值之前，先跑 `python tools\check_ansi_log.py`（它会逐个算对比度）。
ANSI_DARK_COLORS: Tuple[str, ...] = (
    "#0c0c0c", "#c50f1f", "#13a10e", "#c19c00",
    "#0037da", "#881798", "#3a96dd", "#cccccc",
    "#767676", "#e74856", "#16c60c", "#f9f1a5",
    "#3b78ff", "#b4009e", "#61d6d6", "#f2f2f2",
)

ANSI_LIGHT_COLORS: Tuple[str, ...] = (
    "#1f1f1f", "#8d1515", "#095309", "#624000",
    "#173da8", "#7c177c", "#094e60", "#4a4a4a",
    "#606060", "#a72a2d", "#0d680d", "#7a5100",
    "#1e51c1", "#981aa4", "#056177", "#333333",
)

#: 浅色底上"太亮就压暗"的亮度阈值；深色底上"太暗就提亮"的阈值。
#:
#: 0.38 是跟着浅色板定的：浅色板里最亮的那个（`#606060`，亮黑）近似亮度 0.376，
#: 于是 256 色 / 真彩色算出来的颜色会被压到同一个"深度"，观感一致。
ANSI_LIGHT_MAX_LUMA = 0.38
ANSI_DARK_MIN_LUMA = 0.10

# ---------------------------------------------------------------------------
# 自定义色板（「日志配色工作台」调出来的颜色；存在 QSettings 里，启动时载入）
#
# 支持覆盖的只有**日志区**这一块：
#   · `light_bg` / `light_fg` / `dark_bg` / `dark_fg` —— 日志区底色与文字色；
#   · `light_ansi` / `dark_ansi` —— 16 个 ANSI 颜色。
# 界面其它部分（窗口底色、按钮、左栏）不在这里管，原因很简单：
# 那些颜色是成套推导出来的（见 nav_palette / chrome_qss），单改一个只会更花。
# ---------------------------------------------------------------------------

SETTINGS_CUSTOM_PREFIX = "colors/"

#: 运行期的覆盖值（键同上，值是颜色字符串 / 16 色元组）
_CUSTOM: Dict[str, object] = {}


#: xterm 256 色里 6×6×6 色立方用的六档分量
ANSI_CUBE_LEVELS: Tuple[int, ...] = (0, 95, 135, 175, 215, 255)

#: 选中项文字色 / 日志区禁用文字色
HIGHLIGHTED_TEXT = "#ffffff"
DISABLED_LOG_TEXT = "#9a9a9a"

#: 左侧列表**选中行**（浅色）：浅蓝底 + 深字。
#: 它和 `highlight`（按钮/链接那种强调色）是两回事 —— 分开成两个角色，
#: 改一个不会连带把另一个也改掉。
NAV_LIGHT_SELECTED_BG = "#cfe3f7"
NAV_LIGHT_SELECTED_TEXT = "#1a1a1a"

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
# 界面颜色角色：整个界面可自定义的基础
#
# 为什么要"角色"：以前颜色散在十几个常量与函数里（LIGHT_* / DARK_* / NAV_* / LOG_*…），
# 想改一个"左侧列表底色"得先查它到底由谁决定 —— 真机需求就是"界面各部分都要能改"。
# 现在收敛成一张表：每个角色深浅各一个内置值，自定义色板按角色覆盖，
# 而所有 QSS / 调色板都从这张表取色。
# ---------------------------------------------------------------------------

#: 角色名（也是 QSettings 键的后缀：`colors/light_base` …）
UI_ROLES: Tuple[str, ...] = (
    "window",          # 窗体 / 工具栏 / 菜单栏底色
    "base",            # 列表、输入区底色（左侧机器人列表就是它）
    "alternate",       # 悬停 / 交替行
    "text",            # 主文字
    "muted",           # 次要文字（提示行、行数、状态栏次要信息）
    "button",          # 按钮底色
    "border",          # 边框 / 分隔线
    "highlight",       # 强调色（进度、链接、日志选中）
    "highlight_text",  # 强调色上的文字
    "selection_bg",    # 列表选中行底色
    "selection_text",  # 列表选中行文字
    "disabled",        # 禁用态文字
    "focus_border",    # 窗格焦点边框
    "bg",              # 日志区底色
    "fg",              # 日志区文字色
)

#: 角色中文名（工作台、README、报错信息共用一份）
ROLE_LABELS: Dict[str, str] = {
    "window": "窗体 / 工具栏",
    "base": "列表 / 输入区",
    "alternate": "悬停 / 交替行",
    "text": "主文字",
    "muted": "次要文字",
    "button": "按钮底色",
    "border": "边框 / 分隔线",
    "highlight": "强调色",
    "highlight_text": "强调色上的文字",
    "selection_bg": "列表选中行",
    "selection_text": "选中行文字",
    "disabled": "禁用文字",
    "focus_border": "窗格焦点边框",
    "bg": "日志区底色",
    "fg": "日志区文字",
}

#: 浅色内置值。说明几处容易踩的点：
#:   · `window` 与 `bg` **故意同色**（#f0efe9）：日志区与窗体连成一片，
#:     不在浅色界面里"挖"出一块刺眼的白方块（真机确认过要保留）；
#:   · `base` 比窗体略亮一点点（#f4f4f4）：左侧列表要能"看出来是一块列表"，
#:     真机需求原话是"把左侧 bot 实例部分的颜色改成淡灰 f4f4f4"；
#:   · `selection_bg` / `selection_text` 是**列表内**的选中行（浅蓝底深字），
#:     与 `highlight`（按钮/链接那种强调色）分开，改一个不会连带改另一个。
#:
#: 表里的值**全部引用上面的常量** —— 颜色依然只有一个出处，
#: 这张表只负责"哪个部位用哪个颜色"。
LIGHT_ROLES: Dict[str, str] = {
    "window": LIGHT_WINDOW,
    "base": LIGHT_BASE,
    "alternate": LIGHT_ALTERNATE,
    "text": LIGHT_TEXT,
    "muted": MUTED_LIGHT,
    "button": LIGHT_BUTTON,
    "border": LIGHT_BORDER,
    "highlight": LIGHT_HIGHLIGHT,
    "highlight_text": LIGHT_HIGHLIGHT_TEXT,
    "selection_bg": NAV_LIGHT_SELECTED_BG,
    "selection_text": NAV_LIGHT_SELECTED_TEXT,
    "disabled": LIGHT_DISABLED_TEXT,
    "focus_border": FOCUS_BORDER_LIGHT,
    "bg": LOG_LIGHT_BG,
    "fg": LOG_LIGHT_TEXT,
}

DARK_ROLES: Dict[str, str] = {
    "window": DARK_WINDOW,
    "base": DARK_BASE,
    "alternate": DARK_ALTERNATE,
    "text": DARK_TEXT,
    "muted": MUTED_DARK,
    "button": DARK_BUTTON,
    "border": DARK_BORDER,
    "highlight": DARK_HIGHLIGHT,
    "highlight_text": DARK_HIGHLIGHT_TEXT,
    "selection_bg": DARK_HIGHLIGHT,
    "selection_text": DARK_HIGHLIGHT_TEXT,
    "disabled": DARK_DISABLED_TEXT,
    "focus_border": FOCUS_BORDER_DARK,
    "bg": LOG_DARK_BG,
    "fg": LOG_DARK_TEXT,
}

#: 深浅 → 角色表
ROLE_TABLES: Dict[bool, Dict[str, str]] = {False: LIGHT_ROLES, True: DARK_ROLES}


def builtin_role_color(role: str, dark: bool) -> str:
    """内置的角色颜色（**不受**自定义影响）。"""
    table = ROLE_TABLES.get(bool(dark)) or LIGHT_ROLES
    return table.get(str(role)) or table.get("text", "#000000")


def role_color(role: str, dark: Optional[bool] = None,
               widget: Optional[QWidget] = None) -> str:
    """取某个角色的颜色：**自定义优先**，其次内置。

    不传 `dark` 就按 `is_dark(widget)` 判断（跟随系统 / 显式模式都由它决定）。
    角色名不认识时退回主文字色 —— 宁可颜色不对，也不要让 QSS 里出现空值。
    """
    if dark is None:
        dark = is_dark(widget)
    key = "{}_{}".format("dark" if dark else "light", role)
    value = _CUSTOM.get(key)
    if isinstance(value, str) and value:
        return value
    return builtin_role_color(role, bool(dark))


def resolve_roles(dark: Optional[bool] = None,
                  widget: Optional[QWidget] = None) -> Dict[str, str]:
    """一次性取出当前生效的全部角色颜色（工作台、自检、检查器都用它）。"""
    if dark is None:
        dark = is_dark(widget)
    return {role: role_color(role, dark=dark) for role in UI_ROLES}


def ansi_palette_for(dark: bool) -> Tuple[str, ...]:
    """按明暗直接取 ANSI 色板（不看控件，自定义优先）。"""
    custom = _CUSTOM.get("dark_ansi" if dark else "light_ansi")
    if isinstance(custom, (tuple, list)) and len(custom) == 16:
        return tuple(str(item) for item in custom)
    return ANSI_DARK_COLORS if dark else ANSI_LIGHT_COLORS


# ---------------------------------------------------------------------------
# 配色历史：留下"最近调过什么"，随时能退回去
#
# 只存**与内置不同的项**（diff）：既省地方，读起来也一眼能看出改了什么。
# 记录写在 QSettings 的 colors/history（一个 JSON 数组，最多 COLOR_HISTORY_LIMIT 条）。
# ---------------------------------------------------------------------------

SETTINGS_COLOR_HISTORY = "colors/history"
COLOR_HISTORY_LIMIT = 12


def compose_role_colors(mode: str, overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """内置角色 + 覆盖 → 完整角色表（纯函数）。"""
    dark = str(mode).lower() == "dark"
    result = {role: builtin_role_color(role, dark) for role in UI_ROLES}
    for role, value in (overrides or {}).items():
        if role in result and is_hex_color(value):
            result[role] = normalize_hex(value)
    return result


def diff_role_colors(mode: str, roles: Dict[str, str]) -> Dict[str, str]:
    """只保留与内置不同的角色（纯函数）；非法值直接丢掉。"""
    dark = str(mode).lower() == "dark"
    result: Dict[str, str] = {}
    for role, value in (roles or {}).items():
        if role not in UI_ROLES or not is_hex_color(value):
            continue
        normalized = normalize_hex(value)
        if normalized != builtin_role_color(role, dark):
            result[role] = normalized
    return result


def color_snapshot(mode: str, note: str = "") -> Dict[str, object]:
    """当前生效配色的快照：`{time, mode, note, roles(仅差异), ansi(仅差异)}`。"""
    normalized_mode = "dark" if str(mode).lower() == "dark" else "light"
    dark = normalized_mode == "dark"
    roles = {role: role_color(role, dark=dark) for role in UI_ROLES}
    ansi = list(ansi_palette_for(dark))
    builtin_ansi = list(ANSI_DARK_COLORS if dark else ANSI_LIGHT_COLORS)
    return {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": normalized_mode,
        "note": str(note or "")[:60],
        "roles": diff_role_colors(normalized_mode, roles),
        "ansi": [] if ansi == builtin_ansi else ansi,
    }


def encode_history_entry(entry: Dict[str, object]) -> str:
    """一条历史 → 一行 JSON（纯函数，便于单测与人工查看）。"""
    payload = {
        "time": str(entry.get("time") or ""),
        "mode": "dark" if str(entry.get("mode")) == "dark" else "light",
        "note": str(entry.get("note") or "")[:60],
        "roles": {str(key): normalize_hex(value)
                  for key, value in dict(entry.get("roles") or {}).items()
                  if str(key) in UI_ROLES and is_hex_color(value)},
        "ansi": [normalize_hex(color) for color in (entry.get("ansi") or [])
                 if is_hex_color(color)],
    }
    if len(payload["ansi"]) != 16:
        payload["ansi"] = []
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def decode_history_entry(text: object) -> Optional[Dict[str, object]]:
    """一行 JSON → 一条历史；不合法返回 None（纯函数，坏数据不许把界面搞乱）。"""
    if isinstance(text, dict):
        raw = text
    else:
        try:
            raw = json.loads(str(text))
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, dict):
        return None
    mode = "dark" if str(raw.get("mode")) == "dark" else "light"
    roles = {
        str(key): normalize_hex(value)
        for key, value in dict(raw.get("roles") or {}).items()
        if str(key) in UI_ROLES and is_hex_color(value)
    }
    ansi = [normalize_hex(item) for item in (raw.get("ansi") or []) if is_hex_color(item)]
    if len(ansi) != 16:
        ansi = []
    return {
        "time": str(raw.get("time") or ""),
        "mode": mode,
        "note": str(raw.get("note") or "")[:60],
        "roles": roles,
        "ansi": ansi,
    }


def load_color_history(settings: object = None) -> List[Dict[str, object]]:
    """读历史（最近的在前）；读不出来就返回空表。"""
    store = settings_for_colors(settings)
    if store is None:
        return []
    try:
        raw = store.value(SETTINGS_COLOR_HISTORY, "")
    except (TypeError, RuntimeError):
        return []
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        try:
            items = json.loads(str(raw))
        except (TypeError, ValueError):
            return []
    if not isinstance(items, (list, tuple)):
        return []
    entries = []
    for item in items:
        entry = decode_history_entry(item)
        if entry is not None:
            entries.append(entry)
    return entries


def save_color_history(entries, settings: object = None) -> bool:
    """写历史（自动截到 COLOR_HISTORY_LIMIT 条）。"""
    store = settings_for_colors(settings)
    if store is None:
        return False
    encoded = [encode_history_entry(entry) for entry in list(entries or [])]
    encoded = encoded[:COLOR_HISTORY_LIMIT]
    try:
        store.setValue(SETTINGS_COLOR_HISTORY, json.dumps(encoded, ensure_ascii=False))
        store.sync()
    except (TypeError, RuntimeError):
        return False
    return True


def push_color_history(settings: object = None, mode: str = "", note: str = "") -> bool:
    """把**当前生效配色**记一条到历史（最近的排最前，最多 12 条）。"""
    entry = color_snapshot(mode or ("dark" if is_dark(None) else "light"), note=note)
    entries = [entry] + load_color_history(settings)
    if len(entries) > 1 and encode_history_entry(entries[0]) == encode_history_entry(entries[1]):
        entries = entries[1:]          # 和上一条完全一样就不重复占位
    return save_color_history(entries, settings)


def clear_color_history(settings: object = None) -> None:
    """清空历史。"""
    store = settings_for_colors(settings)
    if store is None:
        return
    try:
        store.remove(SETTINGS_COLOR_HISTORY)
        store.sync()
    except (TypeError, RuntimeError):
        pass


def apply_history_entry(entry: Dict[str, object], settings: object = None) -> bool:
    """套用一条历史：写进设置 + 运行期覆盖跟着更新（界面由调用方刷新）。"""
    decoded = decode_history_entry(entry)
    if decoded is None:
        return False
    mode = str(decoded["mode"])
    roles = dict(decoded["roles"] or {})
    ansi = list(decoded["ansi"] or [])
    payload = {"{}_{}".format(mode, role): roles.get(role, "") for role in UI_ROLES}
    payload["{}_ansi".format(mode)] = ansi if ansi else ()
    return save_custom_colors(settings, **payload)


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


# ---------------------------------------------------------------------------
# 应用主题（热切换入口）
# ---------------------------------------------------------------------------

def _build_dark_palette(base: Optional[QPalette] = None) -> QPalette:
    """构造一套深色调色板（把 Windows 浅色调色板换成深灰阶）。"""
    palette = QPalette(base) if base is not None else QPalette()
    window = QColor(role_color("window", dark=True))
    base_color = QColor(role_color("base", dark=True))
    alternate = QColor(role_color("alternate", dark=True))
    text = QColor(role_color("text", dark=True))
    button = QColor(role_color("button", dark=True))
    border = QColor(role_color("border", dark=True))
    highlight = QColor(role_color("highlight", dark=True))
    highlighted_text = QColor(role_color("highlight_text", dark=True))
    disabled = QColor(role_color("disabled", dark=True))

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
        # Dark / Shadow 是 Qt 给"3D 边框 / 自绘细线"用的角色。原来深色主题里给的是
        # #1a1a1a / #101010 —— 比背景还暗，画出来的线与背景糊在一起（真机反馈：
        # 深色模式下工具栏分割线看不见）。改用可见的边框色。
        palette.setColor(group, QPalette.ColorRole.Dark, border)
        palette.setColor(group, QPalette.ColorRole.Shadow, border)
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
    window = QColor(role_color("window", dark=False))
    base_color = QColor(role_color("base", dark=False))
    alternate = QColor(role_color("alternate", dark=False))
    text = QColor(role_color("text", dark=False))
    button = QColor(role_color("button", dark=False))
    border = QColor(role_color("border", dark=False))
    highlight = QColor(role_color("highlight", dark=False))
    highlighted_text = QColor(role_color("highlight_text", dark=False))
    disabled = QColor(role_color("disabled", dark=False))

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
    """次要文字颜色（跟随主题，可自定义）。"""
    return role_color("muted", widget=widget)


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

def ansi_palette(widget: Optional[QWidget] = None) -> Tuple[str, ...]:
    """当前主题的 ANSI 16 色（**自定义色板优先**，没设过就用内置那套）。"""
    dark = is_dark(widget)
    custom = _CUSTOM.get("dark_ansi" if dark else "light_ansi")
    if isinstance(custom, (tuple, list)) and len(custom) == 16:
        return tuple(str(item) for item in custom)
    return ANSI_DARK_COLORS if dark else ANSI_LIGHT_COLORS


# ---------------------------------------------------------------------------
# 自定义色板：读写 / 解析（解析函数是纯的，检查器会抠出来单独测）
# ---------------------------------------------------------------------------

def parse_palette_text(text: object, expected: int = 16) -> Optional[Tuple[str, ...]]:
    """把配置里的文本解析成颜色元组；格式不对返回 None。

    接受的写法（怎么存都好认）：
        ``"#1f1f1f,#8d1515,…"``         逗号 / 分号 / 空格分隔
        ``'["#1f1f1f", "#8d1515", …]'``  JSON 数组（QSettings 存 list 时是这个样子）

    统一输出小写 `#rrggbb`。**纯函数**，不碰 Qt（检查器直接抠出来跑）。
    """
    if text is None:
        return None
    if isinstance(text, (list, tuple)):
        items = [str(item) for item in text]
    else:
        raw = str(text).strip()
        if not raw:
            return None
        if raw.startswith("["):
            try:
                loaded = json.loads(raw)
            except (ValueError, TypeError):
                return None
            if not isinstance(loaded, (list, tuple)):
                return None
            items = [str(item) for item in loaded]
        else:
            items = [piece for piece in re.split(r"[,;\s]+", raw) if piece]

    colors: list = []
    for item in items:
        value = str(item).strip()
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
            return None
        colors.append("#" + value.lstrip("#").lower())
    if len(colors) != expected:
        return None
    return tuple(colors)


def custom_colors() -> Dict[str, object]:
    """当前生效的自定义覆盖值（副本，改它不会影响运行期状态）。"""
    return dict(_CUSTOM)


def color_setting_key(name: str) -> str:
    """自定义颜色的设置键：`light_base` → `colors/light_base`。"""
    return SETTINGS_CUSTOM_PREFIX + str(name)


def custom_color_keys() -> Tuple[str, ...]:
    """所有可以被自定义的颜色键：`<模式>_<角色>` + `<模式>_ansi`。"""
    keys: List[str] = []
    for mode in ("light", "dark"):
        for role in UI_ROLES:
            keys.append("{}_{}".format(mode, role))
        keys.append("{}_ansi".format(mode))
    return tuple(keys)


def set_custom_colors(**values) -> None:
    """设置运行期的自定义颜色（工作台用它做即时预览）。

    键的写法：`<light|dark>_<角色>`（角色见 :data:`UI_ROLES`），外加 `..._ansi`。
    · 传 None → 这一项不动；
    · 传空字符串 / 空列表 → **清掉**这一项，回到内置；
    · 颜色不合法 → 忽略（宁可保持原样，也不要把界面弄坏）。
    """
    for key, value in (values or {}).items():
        name = str(key)
        mode = name.split("_", 1)[0]
        if mode not in ("light", "dark") or value is None:
            continue
        if name.endswith("_ansi"):
            if isinstance(value, (str, list, tuple)) and len(value) == 0:
                _CUSTOM.pop(name, None)          # 显式清空 → 回到内置色板
                continue
            parsed = parse_palette_text(value)
            if parsed is None:
                continue
            _CUSTOM[name] = parsed
            continue
        role = name[len(mode) + 1:]
        if role not in UI_ROLES:
            continue
        text = str(value).strip()
        if not text:
            _CUSTOM.pop(name, None)              # 显式清空 → 回到内置颜色
            continue
        if is_hex_color(text):
            _CUSTOM[name] = normalize_hex(text)


def settings_for_colors(settings: object = None):
    """取 QSettings（没传就按本程序的组织名 / 应用名建一个）。"""
    if settings is not None:
        return settings
    try:
        return QSettings(ORG_NAME, APP_NAME)
    except (TypeError, RuntimeError):
        return None


def load_custom_colors(settings: object = None) -> bool:
    """从 QSettings 载入自定义颜色（启动时调用）。有载入到东西返回 True。"""
    store = settings_for_colors(settings)
    if store is None:
        return False
    loaded = False
    for key in custom_color_keys():
        try:
            value = store.value(color_setting_key(key), "")
        except (TypeError, RuntimeError):
            continue
        if key.endswith("_ansi"):
            parsed = parse_palette_text(value)
            if parsed is not None:
                _CUSTOM[key] = parsed
                loaded = True
            continue
        text = str(value or "").strip()
        if text and is_hex_color(text):
            _CUSTOM[key] = normalize_hex(text)
            loaded = True
    return loaded


def save_custom_colors(settings: object = None, **values) -> bool:
    """把自定义颜色写进 QSettings（工作台"保存"按钮用）。空值 = 删除该项。"""
    store = settings_for_colors(settings)
    if store is None:
        return False
    allowed = set(custom_color_keys())
    for key, value in (values or {}).items():
        name = str(key)
        if name not in allowed:
            continue
        setting_key = color_setting_key(name)
        empty = value is None or (isinstance(value, (str, list, tuple)) and len(value) == 0)
        if empty:
            try:
                store.remove(setting_key)
            except (TypeError, RuntimeError):
                continue
            _CUSTOM.pop(name, None)
            continue
        if name.endswith("_ansi"):
            parsed = parse_palette_text(value)
            if parsed is None:
                continue
            text = ",".join(parsed)
            _CUSTOM[name] = parsed
        else:
            if not is_hex_color(value):
                continue
            text = normalize_hex(str(value))
            _CUSTOM[name] = text
        try:
            store.setValue(setting_key, text)
        except (TypeError, RuntimeError):
            continue
    try:
        store.sync()
    except (AttributeError, RuntimeError):
        pass
    return True


def clear_custom_colors(settings: object = None) -> None:
    """清掉全部自定义颜色（回到内置那套）。"""
    store = settings_for_colors(settings)
    for key in custom_color_keys():
        if store is not None:
            try:
                store.remove(color_setting_key(key))
            except (TypeError, RuntimeError):
                pass
    _CUSTOM.clear()
    if store is not None:
        try:
            store.sync()
        except (AttributeError, RuntimeError):
            pass


def is_hex_color(value: object) -> bool:
    """是不是 `#rrggbb`（允许省略 `#`）。"""
    return bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", str(value or "").strip()))


def normalize_hex(value: object) -> str:
    """统一成小写 `#rrggbb`。"""
    text = str(value or "").strip().lstrip("#").lower()
    return "#" + text


def relative_luminance(color: str) -> float:
    """WCAG 相对亮度（0=黑，1=白）。纯字符串运算，不需要 Qt。

    注意与 :func:`color_luminance` 的区别：那个是"大概够不够亮"的近似值
    （给压暗/提亮用，不做 sRGB 线性化）；这个是**标准公式**，
    专门用来算对比度、判断"这行字在日志区底色上读不读得清"。
    """
    text = str(color or "").strip().lstrip("#")
    if len(text) != 6:
        return 0.0
    try:
        channels = [int(text[index:index + 2], 16) / 255.0 for index in (0, 2, 4)]
    except ValueError:
        return 0.0
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(color_a: str, color_b: str) -> float:
    """两个颜色的 WCAG 对比度（1.0-21.0）。

    经验值：正文 ≥ 4.5 算合格（AA），≥ 7 算舒服（AAA），
    ≥ 10 基本"一眼就看见"。色板里每个颜色都应该 ≥ 4.5。
    """
    first, second = relative_luminance(color_a), relative_luminance(color_b)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


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
    """窗格焦点边框色（可自定义）。"""
    return role_color("focus_border", widget=widget)


def palette_color(widget: Optional[QWidget], role: QPalette.ColorRole, fallback: str) -> str:
    """从调色板取色（取不到时用 fallback）。"""
    try:
        palette = widget.palette() if widget is not None else QApplication.palette()
        return palette.color(role).name()
    except (AttributeError, TypeError, RuntimeError):
        return fallback


def nav_palette(widget: Optional[QWidget] = None) -> Dict[str, str]:
    """左栏 QSS 需要的全部颜色。

    N3.x：**全部走颜色角色**。以前浅色这一支直接读系统调色板
    （`palette_color(Base, ...)` 拿到的是纯白），于是"左侧列表底色"根本不受我们控制 ——
    想在浅色下用淡灰都做不到。真机需求就是"左侧 bot 实例部分改成淡灰"，所以必须收敛到角色。
    """
    return {
        "base": role_color("base", widget=widget),
        "text": role_color("text", widget=widget),
        "border": role_color("border", widget=widget),
        "sel": role_color("selection_bg", widget=widget),
        "sel_text": role_color("selection_text", widget=widget),
        "hover": role_color("alternate", widget=widget),
        "button": role_color("button", widget=widget),
        "disabled": role_color("disabled", widget=widget),
    }


def log_colors_for(dark: bool) -> Tuple[str, str, str, str]:
    """日志区四色：(背景, 文字, 边框, 选中背景)。

    四色**全部可被自定义色板覆盖**（工作台里调出来的值）；
    角色分别是 `bg` / `fg` / `border` / `highlight` —— 与界面其它部分共用同一张表，
    所以改"边框/强调色"时日志区会跟着一起变，不会各说各话。
    """
    return (
        role_color("bg", dark=dark),
        role_color("fg", dark=dark),
        role_color("border", dark=dark),
        role_color("highlight", dark=dark),
    )


def log_colors(widget: Optional[QWidget] = None) -> Tuple[str, str, str, str]:
    """日志区四色：(背景, 文字, 边框, 选中背景)。

    N2.3 定稿：**只用我们自己的常量**，不再从 `app.palette()` 推导。
    原因（真机实测）：调色板到底有没有被 Qt 采用、什么时候采用，不受我们控制；
    日志区作为"主要内容区"，配色必须可预测 —— 深色深底浅字、浅色柔和黄灰底近黑字。
    跟随系统时用系统给的明暗（`is_dark()`）来二选一。
    自定义色板（工作台里调的）优先于常量。
    """
    return log_colors_for(is_dark(widget))


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
    bg, fg, border, sel = log_colors_for(bool(dark))
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
            "text": role_color("text", dark=True),
            "muted": role_color("muted", dark=True),
            "button": role_color("button", dark=True),
            "button_text": role_color("text", dark=True),
            "border": role_color("border", dark=True),
            "hover": role_color("alternate", dark=True),
            "sel": role_color("highlight", dark=True),
            "sel_text": role_color("highlight_text", dark=True),
            "disabled": role_color("disabled", dark=True),
            "tooltip_bg": role_color("base", dark=True),
            "chrome_bg": role_color("window", dark=True),
        }
    # N3.0：跟随系统已解析成深浅之一，is_dark() 已经给出正确结果，
    # 因此这里不再需要单独的"系统分支"（少一条分支就少一处 bug）。
    return {
        "text": role_color("text", dark=False),
        "muted": role_color("muted", dark=False),
        "button": role_color("button", dark=False),
        "button_text": role_color("text", dark=False),
        "border": role_color("border", dark=False),
        "hover": role_color("alternate", dark=False),
        "sel": role_color("highlight", dark=False),
        "sel_text": role_color("highlight_text", dark=False),
        "disabled": role_color("disabled", dark=False),
        "tooltip_bg": role_color("base", dark=False),
        "chrome_bg": role_color("window", dark=False),
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
        # 工具栏/主窗口的分割线：Qt 默认用调色板的 Dark 角色画，深色主题下那个是
        # 近黑色（#1a1a1a），落在深色工具栏上几乎看不见（真机反馈 2026-10-05：
        # "深色模式下浅色的分割线理应看得见，现在看不清楚"）。显式用 border 角色画，
        # 顺便让它跟着自定义配色走。
        "QToolBar::separator {{ background-color: {border}; width: 1px; margin: 3px 6px; }}"
        "QMainWindow::separator {{ background-color: {border}; width: 1px; height: 1px; }}"
        # 窗格之间的分割条：**不要整条涂成边框色**。
        # 真机 2026-10-06："各程序日志区域的边框还是会与分隔线融合，我希望不要融合" ——
        # 整条涂色时，分割条和两侧日志框的边框同色相邻，看上去糊成一条粗带子。
        # 现在只画**中间那条 1px 细线**（背景透明，露出面板底色），
        # 两侧自然留出空隙，分隔线与日志框边框一眼能分清。
        # 拖动区域宽度仍由 setHandleWidth 决定（3px）。
        "QSplitter::handle {{ background-color: transparent; }}"
        "QSplitter::handle:horizontal {{ border-left: 1px solid {border}; }}"
        "QSplitter::handle:vertical {{ border-top: 1px solid {border}; }}"
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
    panel = role_color("alternate", widget=widget)
    text = role_color("text", widget=widget)
    separator = role_color("border", widget=widget)
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
