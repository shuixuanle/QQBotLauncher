# -*- coding: utf-8 -*-
"""配色工作台（应用内对话框）：整界面 + 日志颜色，改了**立即生效**。

怎么打开
--------
主界面：**视图 → 外观 → 配色工作台…**（也可以 `python tools\\palette_studio.py` 单独开，
两边是**同一个对话框**，不存两份界面 —— 免得"工具里好看、应用里不一样"）。

能改什么（深浅两套各自独立）
----------------------------
· **15 个界面角色** —— 窗体、列表/输入区、悬停、主文字、次要文字、按钮、边框、
  强调色、列表选中行、禁用文字、窗格焦点边框、日志区底色 / 文字色…；
· **16 个 ANSI 颜色** —— 机器人日志里的终端颜色。

改一下会发生什么
----------------
1. 值先写进 `theme` 的运行期覆盖（`set_custom_colors`）；
2. 发 `colorsChanged` 信号 → 主窗口立刻 `_apply_theme()`（含所有已打开的实例窗口、
   对话框、菜单栏、工具栏），**不用重启**；
3. 点「保存」才写进设置（`colors/light_base` …，下次启动带着）；
4. 每次保存都会往**配色历史**里记一条（只存与内置不同的项 + 时间），
   随时能从下拉里退回去。

界面上还有什么
--------------
左侧上半是真日志控件（16 色各一行，行尾是它和日志底色的 WCAG 对比度），
下半是界面样例（列表含选中行、按钮、禁用按钮、输入框、次要文字）；
右侧是所有色块，点开取色器即改即画；该看对比度的地方直接标数字，不达标红框。
"""

from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.program_widget import ProgramWidget  # noqa: E402

#: 16 个 ANSI 颜色的中文名（编号与终端一致）
ANSI_NAMES = (
    "30 黑", "31 红", "32 绿", "33 黄", "34 蓝", "35 品红", "36 青", "37 白",
    "90 亮黑", "91 亮红", "92 亮绿", "93 亮黄", "94 亮蓝", "95 亮品红", "96 亮青", "97 亮白",
)

#: 写进预览文本用的 ANSI 编号
ANSI_CODES = tuple(list(range(30, 38)) + list(range(90, 98)))

#: 需要显示对比度的角色：角色 → (跟哪个角色比, 合格线)
#: 只给"文字压在某块底色上"的组合算 —— 底色跟底色比毫无意义。
CONTRAST_PAIRS = {
    "text": ("window", 7.0),
    "muted": ("window", 3.0),
    "fg": ("bg", 7.0),
    "highlight_text": ("highlight", 4.5),
    "selection_text": ("selection_bg", 4.5),
}

#: 撤销栈最多留多少步
UNDO_LIMIT = 30

#: 真机日志片段（ATRI 主程序那条），用来在真实语境里看颜色
REAL_LOGS = (
    "\x1b[32;20m10-02 13:22:52 [INFO] atri-bot.PluginLoader | 插件已加载: group_manager v1.0.0\x1b[0m",
    "\x1b[38;20m10-02 13:22:53 [DEBUG] atri-bot.whitelist | 群相关事件:{'self_id': 3835346614}\x1b[0m",
    "\x1b[33m10-02 13:22:54 [WARNING] atri-bot.ChatManager | 上下文接近上限，准备归档\x1b[0m",
    "\x1b[31m10-02 13:22:55 [ERROR] atri-bot.OneBotAdapter | 连接被拒绝: 127.0.0.1:8888\x1b[0m",
    "10-02 13:22:56 [INFO] atri-bot.Bot | 管理面板已就绪: http://127.0.0.1:5125/admin/",
    "\x1b[1m加粗\x1b[0m　\x1b[4m下划线\x1b[0m　\x1b[7m反显\x1b[0m　"
    "\x1b[38;5;208m256 色 208\x1b[0m　\x1b[38;2;255;105;180m真彩色\x1b[0m",
)


def build_sample(palette, background: str, foreground: str) -> str:
    """生成日志预览文本：16 色各一行 + 真实日志 + 样式演示。"""
    lines = ['═══ ① ANSI 16 色（行尾 = 与日志底色的 WCAG 对比度，< 4.5 标"偏淡"）═══']
    for index, name in enumerate(ANSI_NAMES):
        ratio = theme_tokens.contrast_ratio(palette[index], background)
        flag = "" if ratio >= 4.5 else "   ← 偏淡"
        lines.append(
            "\x1b[{code}m{name}　示例：插件已加载 / INFO / 正在连接 127.0.0.1:8888…"
            "\x1b[0m　对比度 {ratio:.2f}{flag}".format(
                code=ANSI_CODES[index], name=name, ratio=ratio, flag=flag)
        )
    lines.append("")
    lines.append("═══ ② 真实日志片段（机器人实际输出的样子）═══")
    lines.extend(REAL_LOGS)
    lines.append("")
    lines.append("═══ ③ 长行与中文排版 ═══")
    lines.append(
        "10-02 14:10:42 [INFO] atri-bot.ChatManager | 正在对上下文进行批量备份！"
        "这条故意写得很长，用来确认长中文行在浅底上读起来会不会发糊。"
    )
    lines.append("")
    lines.append("═══ ④ 普通文字（没有颜色序列）应当是「日志区文字」色：{} ═══".format(foreground))
    lines.append("这一行没写颜色，正常情况下就是默认文字色，不该继承上一行的绿。")
    return "\n".join(lines) + "\n"


class Swatch(QPushButton):
    """一个色块按钮：显示颜色、名称、hex、（可选）对比度；点它开取色器。"""

    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Consolas", 9))

    def update_color(self, label: str, color: str, detail: str = "",
                     warn: bool = False) -> None:
        """刷新外观（颜色 / 文字 / 是否告警）。"""
        text_color = "#ffffff" if theme_tokens.relative_luminance(color) < 0.5 else "#000000"
        border = theme_tokens.COLOR_FAILED if warn else theme_tokens.COLOR_IDLE
        self.setStyleSheet(
            "QPushButton {{ background-color: {bg}; color: {fg};"
            " border: {width}px solid {border}; border-radius: 3px; padding: 2px 6px; }}".format(
                bg=color, fg=text_color, border=border, width=2 if warn else 1)
        )
        text = "{}　{}".format(label, color)
        if detail:
            text += "　{}".format(detail)
        self.setText(text)
        self.setToolTip("点一下改这个颜色")


class PaletteDialog(QDialog):
    """配色工作台。改了立即生效（通过 `colorsChanged` 让主窗口重刷）。"""

    #: 任何颜色变化都会发一次：主窗口接上它做"立即生效"
    colorsChanged = pyqtSignal()
    #: 保存 / 清除 / 套用历史之后发一次（需要落盘或提示时用）
    colorsSaved = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None, dark: Optional[bool] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("配色工作台")
        self.setModal(False)
        self.resize(1180, 780)
        self.setSizeGripEnabled(True)

        self.dark = theme_tokens.is_dark(parent) if dark is None else bool(dark)
        self.roles: Dict[str, str] = {}
        self.colors: List[str] = []
        self.edited: set = set()
        self.ansi_edited = False
        self.undo_stack: List[dict] = []
        self.history: List[dict] = []

        self._build_ui()
        self._load_from_theme()
        self._reload_history()
        self._refresh_all(emit=False)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _mode(self) -> str:
        return "dark" if self.dark else "light"

    def _default_colors(self) -> List[str]:
        return list(theme_tokens.ANSI_DARK_COLORS if self.dark
                    else theme_tokens.ANSI_LIGHT_COLORS)

    def _load_from_theme(self) -> None:
        """把当前生效的配色读进界面（含已保存的自定义）。"""
        custom = theme_tokens.custom_colors()
        mode = self._mode()
        self.roles = {}
        self.edited = set()
        for role in theme_tokens.UI_ROLES:
            builtin = theme_tokens.builtin_role_color(role, self.dark)
            value = str(custom.get("{}_{}".format(mode, role)) or builtin)
            self.roles[role] = value
            if value != builtin:
                self.edited.add(role)
        ansi = custom.get("{}_ansi".format(mode))
        self.colors = list(ansi) if ansi else self._default_colors()
        self.ansi_edited = self.colors != self._default_colors()

    def _push_undo(self) -> None:
        """记一步撤销点（最多 30 步）。"""
        self.undo_stack.append({
            "roles": dict(self.roles),
            "colors": list(self.colors),
            "dark": self.dark,
        })
        if len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("模式：", self))
        self.mode_box = QComboBox(self)
        self.mode_box.addItems(["浅色模式", "深色模式"])
        self.mode_box.setCurrentIndex(1 if self.dark else 0)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        top.addWidget(self.mode_box)
        top.addSpacing(10)
        self.hint = QLabel(
            "改一下就生效（主界面、已打开的窗口、日志都会跟着变）；"
            "点「保存」才写进设置，下次启动自动带上。", self)
        self.hint.setWordWrap(True)
        top.addWidget(self.hint, 1)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)

        self.preview = ProgramWidget("日志预览", "", "", max_lines=2000, show_toolbar=True)
        left_layout.addWidget(self.preview, 3)

        sample_box = QGroupBox("界面样例（列表 / 按钮 / 输入框 —— 和左栏同一类控件）", left)
        sample_layout = QVBoxLayout(sample_box)
        self.sample_list = QListWidget(sample_box)
        for text in ("ATRI [3/3]", "雨沐 [3/3]", "消防栓 [0/1]"):
            self.sample_list.addItem(QListWidgetItem(text))
        self.sample_list.setCurrentRow(0)      # 选中行 → selection_bg / selection_text
        self.sample_list.setMaximumHeight(84)
        sample_layout.addWidget(self.sample_list)
        row = QHBoxLayout()
        self.sample_button = QPushButton("启动全部", sample_box)
        self.sample_disabled = QPushButton("已禁用", sample_box)
        self.sample_disabled.setEnabled(False)
        self.sample_line = QLineEdit("输入框（base 底色）", sample_box)
        self.sample_muted = QLabel("次要文字 · muted", sample_box)
        row.addWidget(self.sample_button)
        row.addWidget(self.sample_disabled)
        row.addWidget(self.sample_line, 1)
        sample_layout.addLayout(row)
        sample_layout.addWidget(self.sample_muted)
        left_layout.addWidget(sample_box, 1)
        splitter.addWidget(left)

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)

        roles_box = QGroupBox("界面颜色（点色块改色）", right)
        roles_grid = QGridLayout(roles_box)
        roles_grid.setSpacing(4)
        self.role_swatches: Dict[str, Swatch] = {}
        for index, role in enumerate(theme_tokens.UI_ROLES):
            swatch = Swatch(theme_tokens.ROLE_LABELS.get(role, role), roles_box)
            swatch.clicked.connect(lambda _checked=False, name=role: self._pick_role(name))
            roles_grid.addWidget(swatch, index % 8, index // 8)
            self.role_swatches[role] = swatch
        right_layout.addWidget(roles_box)

        ansi_box = QGroupBox("ANSI 16 色（日志里的终端颜色）", right)
        ansi_grid = QGridLayout(ansi_box)
        ansi_grid.setSpacing(4)
        self.swatches: List[Swatch] = []
        for index in range(16):
            swatch = Swatch(ANSI_NAMES[index], ansi_box)
            swatch.clicked.connect(
                lambda _checked=False, position=index: self._pick_ansi(position))
            ansi_grid.addWidget(swatch, index % 8, index // 8)
            self.swatches.append(swatch)
        right_layout.addWidget(ansi_box)

        self.summary = QLabel("", right)
        self.summary.setWordWrap(True)
        right_layout.addWidget(self.summary)

        buttons = QHBoxLayout()
        for text, handler in (
            ("撤销", self._on_undo),
            ("恢复内置", self._on_reset),
            ("保存", self._on_save),
            ("清除自定义", self._on_clear),
            ("复制为代码", self._on_copy),
        ):
            button = QPushButton(text, right)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        right_layout.addLayout(buttons)

        history_row = QHBoxLayout()
        history_row.addWidget(QLabel("配色记录：", right))
        self.history_box = QComboBox(right)
        self.history_box.setMinimumWidth(240)
        history_row.addWidget(self.history_box, 1)
        apply_button = QPushButton("套用这条", right)
        apply_button.clicked.connect(self._on_apply_history)
        history_row.addWidget(apply_button)
        clear_history = QPushButton("清空记录", right)
        clear_history.clicked.connect(self._on_clear_history)
        history_row.addWidget(clear_history)
        right_layout.addLayout(history_row)
        right_layout.addStretch(1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        outer.addLayout(close_row)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def _on_mode_changed(self, index: int) -> None:
        self.dark = index == 1
        self._load_from_theme()
        self._refresh_all()

    def _pick_role(self, role: str) -> None:
        chosen = QColorDialog.getColor(
            QColor(self.roles.get(role, "#ffffff")), self,
            "选择：{}".format(theme_tokens.ROLE_LABELS.get(role, role)))
        if not chosen.isValid():
            return
        self._push_undo()
        self.roles[role] = chosen.name().lower()
        builtin = theme_tokens.builtin_role_color(role, self.dark)
        if self.roles[role] == builtin:
            self.edited.discard(role)
        else:
            self.edited.add(role)
        self._refresh_all()

    def _pick_ansi(self, index: int) -> None:
        chosen = QColorDialog.getColor(QColor(self.colors[index]), self,
                                       "选择 {}".format(ANSI_NAMES[index]))
        if not chosen.isValid():
            return
        self._push_undo()
        self.colors[index] = chosen.name().lower()
        self.ansi_edited = self.colors != self._default_colors()
        self._refresh_all()

    def _on_undo(self) -> None:
        """退回上一步（含模式切换）。"""
        if not self.undo_stack:
            self.status.setText("没有可撤销的步骤了。")
            return
        step = self.undo_stack.pop()
        self.dark = bool(step.get("dark", self.dark))
        self.mode_box.blockSignals(True)
        self.mode_box.setCurrentIndex(1 if self.dark else 0)
        self.mode_box.blockSignals(False)
        self.roles = dict(step["roles"])
        self.colors = list(step["colors"])
        self._sync_edited_flags()
        self._refresh_all()
        self.status.setText("已撤销一步（还剩 {} 步）。".format(len(self.undo_stack)))

    def _sync_edited_flags(self) -> None:
        """按当前值重新判断"哪些和内置不一样"。"""
        self.edited = {role for role, value in self.roles.items()
                       if value != theme_tokens.builtin_role_color(role, self.dark)}
        self.ansi_edited = self.colors != self._default_colors()

    def _on_reset(self) -> None:
        """当前模式回到内置（运行期立即生效；设置里的自定义项也会被清掉）。"""
        self._push_undo()
        mode = self._mode()
        theme_tokens.set_custom_colors(
            **{"{}_{}".format(mode, role): "" for role in theme_tokens.UI_ROLES},
            **{"{}_ansi".format(mode): ()})
        self.roles = {role: theme_tokens.builtin_role_color(role, self.dark)
                      for role in theme_tokens.UI_ROLES}
        self.colors = self._default_colors()
        self.edited = set()
        self.ansi_edited = False
        self._refresh_all()
        self.status.setText("{}已恢复内置配色。".format(
            "深色" if self.dark else "浅色"))

    def _on_save(self) -> None:
        """写进设置（只写与内置不同的项）+ 记一条配色历史。"""
        mode = self._mode()
        payload = {"{}_{}".format(mode, role): (self.roles[role] if role in self.edited else "")
                   for role in theme_tokens.UI_ROLES}
        payload["{}_ansi".format(mode)] = self.colors if self.ansi_edited else ()
        if not theme_tokens.save_custom_colors(None, **payload):
            QMessageBox.warning(self, "保存失败", "写设置失败，请看控制台输出。")
            return
        note = "{}模式：改 {} 项".format("深色" if self.dark else "浅色",
                                        len(self.edited) + (1 if self.ansi_edited else 0))
        theme_tokens.push_color_history(None, mode=mode, note=note)
        self._reload_history()
        self.colorsSaved.emit()
        self.status.setText("已保存（{}），下次启动自动带上；配色记录 +1。".format(note))

    def _on_clear(self) -> None:
        answer = QMessageBox.question(
            self, "清除自定义", "清除后回到内置配色（深浅两套一起清）。继续？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._push_undo()
        theme_tokens.clear_custom_colors(None)
        self._load_from_theme()
        self._refresh_all()
        self.colorsSaved.emit()
        self.status.setText("已回到内置配色。")

    def _on_copy(self) -> None:
        """把当前配色复制成可直接粘进 theme.py 的代码。"""
        role_name = "DARK_ROLES" if self.dark else "LIGHT_ROLES"
        ansi_name = "ANSI_DARK_COLORS" if self.dark else "ANSI_LIGHT_COLORS"
        lines = ["{} = {{".format(role_name)]
        for role in theme_tokens.UI_ROLES:
            lines.append('    "{}": "{}",'.format(role, self.roles[role]))
        lines.append("}")
        lines.append("")
        lines.append("{}: Tuple[str, ...] = (".format(ansi_name))
        for index in range(0, 16, 4):
            lines.append("    " + ", ".join(
                '"{}"'.format(color) for color in self.colors[index:index + 4]) + ",")
        lines.append(")")
        code = "\n".join(lines) + "\n"
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(code)
        self.status.setText("配色代码已复制到剪贴板（可以直接贴给作者）。")

    # ------------------------------------------------------------------
    # 配色记录（历史）
    # ------------------------------------------------------------------

    def _reload_history(self) -> None:
        self.history = theme_tokens.load_color_history(None)
        self.history_box.clear()
        if not self.history:
            self.history_box.addItem("（还没有记录：点「保存」会记一条）")
            return
        for index, entry in enumerate(self.history):
            mode = "深色" if entry.get("mode") == "dark" else "浅色"
            self.history_box.addItem("{}　{}　{}".format(
                index + 1, entry.get("time") or "?", entry.get("note") or mode))

    def _on_apply_history(self) -> None:
        index = self.history_box.currentIndex()
        if not self.history or not (0 <= index < len(self.history)):
            return
        entry = self.history[index]
        if not theme_tokens.apply_history_entry(entry, None):
            QMessageBox.warning(self, "无法套用", "这条记录读不出来（可能被改坏了）。")
            return
        self.dark = entry.get("mode") == "dark"
        self.mode_box.blockSignals(True)
        self.mode_box.setCurrentIndex(1 if self.dark else 0)
        self.mode_box.blockSignals(False)
        self._load_from_theme()
        self._refresh_all()
        self.colorsSaved.emit()
        self.status.setText("已套用第 {} 条记录（{}）。".format(
            index + 1, entry.get("time") or ""))

    def _on_clear_history(self) -> None:
        answer = QMessageBox.question(self, "清空记录", "把配色记录全部删掉？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        theme_tokens.clear_color_history(None)
        self._reload_history()
        self.status.setText("配色记录已清空。")

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------

    def _apply_to_theme(self) -> None:
        """把界面上的值塞给 theme（运行期覆盖），用于即时预览。"""
        values = {"{}_{}".format(self._mode(), role): color
                  for role, color in self.roles.items()}
        values["{}_ansi".format(self._mode())] = self.colors
        theme_tokens.set_custom_colors(**values)

    def _refresh_all(self, emit: bool = True) -> None:
        """应用 → 重画预览与样例 → 刷新色块；`emit=True` 时通知主窗口立即重刷。"""
        self._apply_to_theme()
        if emit:
            self.colorsChanged.emit()
        # 对话框自己也跟着换色（和主界面同一套外壳样式）
        self.setStyleSheet(theme_tokens.chrome_qss(self))
        self.preview.apply_theme()
        self.preview.load_raw_text(build_sample(
            self.colors, self.roles.get("bg", "#ffffff"), self.roles.get("fg", "#000000")))
        self._refresh_swatches()
        self._refresh_summary()

    def _refresh_swatches(self) -> None:
        for role, swatch in self.role_swatches.items():
            color = self.roles.get(role, "#ffffff")
            detail, warn = "", False
            pair = CONTRAST_PAIRS.get(role)
            if pair:
                other, limit = pair
                ratio = theme_tokens.contrast_ratio(color, self.roles.get(other, "#ffffff"))
                detail = "{:.2f}".format(ratio)
                warn = ratio < limit
            if role in self.edited:
                detail = (detail + " " if detail else "") + "改"
            swatch.update_color(theme_tokens.ROLE_LABELS.get(role, role), color,
                                detail, warn=warn)
        for index, swatch in enumerate(self.swatches):
            color = self.colors[index]
            ratio = theme_tokens.contrast_ratio(color, self.roles.get("bg", "#ffffff"))
            swatch.update_color(ANSI_NAMES[index], color, "{:.2f}".format(ratio),
                                warn=ratio < 4.5)

    def _refresh_summary(self) -> None:
        background = self.roles.get("bg", "#ffffff")
        ratios = sorted(theme_tokens.contrast_ratio(color, background) for color in self.colors)
        weak = [ANSI_NAMES[index] for index, color in enumerate(self.colors)
                if theme_tokens.contrast_ratio(color, background) < 4.5]
        self.summary.setText(
            "当前编辑：{}模式　改过 {} 个界面角色{}{}\n"
            "ANSI 16 色对比度（对日志底色 {}）：最低 {:.2f}　中位 {:.2f}{}".format(
                "深色" if self.dark else "浅色", len(self.edited),
                "（含 ANSI 16 色）" if self.ansi_edited else "",
                "　|　配色记录 {} 条".format(len(self.history)),
                background, ratios[0], ratios[len(ratios) // 2],
                "" if not weak else "\n⚠ 偏淡（< 4.5）：" + "、".join(weak))
        )
        self.summary.setStyleSheet("color: {};".format(
            theme_tokens.COLOR_FAILED if weak else theme_tokens.COLOR_RUNNING))
