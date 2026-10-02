# -*- coding: utf-8 -*-
"""配色工作台：在**真控件**上试色，满意了存成自定义配色（整个界面都能改）。

为什么要这个东西
----------------
"颜色够不够清楚 / 顺不顺眼"是主观的，靠改代码猜很费劲：
改一次 `theme.py` → 重启管理器 → 盯着屏幕看 → 再改…… 而且颜色和**底色**互相影响
（`#f0efe9` 这种偏暖的浅底上，蓝/青天然显脏），所以必须能"边改边看"。

能改什么（深浅两套各自独立）：
  · **15 个界面角色** —— 窗体、列表/输入区、悬停、主文字、次要文字、按钮、边框、
    强调色、列表选中行、禁用文字、窗格焦点边框、日志区底色 / 文字色…；
  · **16 个 ANSI 颜色** —— 机器人日志里的终端颜色。

本工具做的事：
  · 左侧上半是**真的** `ProgramWidget`（和主界面同一套控件、同一条上色代码路径），
    里面是 16 个 ANSI 颜色各一行 + 真实日志；行尾标着它与日志底色的 **WCAG 对比度**；
  · 左侧下半是**界面样例**：列表（含选中行）、按钮、次要文字 —— 就是左栏那几种控件；
    而工作台窗口本身也会跟着换色（因为它就是用 `theme.apply_theme()` 上的色）；
  · 右侧是所有色块，点一下开取色器，改完立刻重画；该看对比度的地方会标出来；
  · 满意后点「保存为自定义配色」：写进管理器自己的 QSettings，
    下次启动 `python main.py`（或 exe）就生效；不想要了随时点「清除自定义」。

用法：
    python tools\\palette_studio.py            # 先看浅色
    python tools\\palette_studio.py dark       # 直接看深色
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import PyQt6  # noqa: F401
except ImportError:
    print("需要 PyQt6（pip install -r requirements.txt），本机没装 —— 无法启动工作台。")
    raise SystemExit(0)

from PyQt6.QtCore import Qt                                       # noqa: E402
from PyQt6.QtGui import QColor, QFont, QGuiApplication            # noqa: E402
from PyQt6.QtWidgets import (                                     # noqa: E402
    QApplication,
    QColorDialog,
    QComboBox,
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

from app.ui import theme as theme_tokens                          # noqa: E402
from app.ui.program_widget import ProgramWidget                   # noqa: E402

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
    lines.append("这一行没有写颜色，正常情况下就是默认文字色，不该继承上一行的绿。")
    return "\n".join(lines) + "\n"


class Swatch(QPushButton):
    """一个色块按钮：显示颜色、名称、hex、（可选）对比度；点它开取色器。"""

    def __init__(self, label: str, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Consolas", 9))
        self._label = label

    def update_color(self, color: str, detail: str = "", warn: bool = False) -> None:
        """刷新外观（颜色 / 文字 / 是否告警）。"""
        text_color = "#ffffff" if theme_tokens.relative_luminance(color) < 0.5 else "#000000"
        border = theme_tokens.COLOR_FAILED if warn else theme_tokens.COLOR_IDLE
        self.setStyleSheet(
            "QPushButton {{ background-color: {bg}; color: {fg};"
            " border: {width}px solid {border}; border-radius: 3px; padding: 2px 6px; }}".format(
                bg=color, fg=text_color, border=border, width=2 if warn else 1)
        )
        text = "{}　{}".format(self._label, color)
        if detail:
            text += "　{}".format(detail)
        self.setText(text)
        self.setToolTip("点一下改这个颜色")


class Studio(QWidget):
    """工作台主窗口。

    编辑的是**整套界面颜色**：15 个界面角色 + 16 个 ANSI 颜色，深浅两套各自独立。
    """

    def __init__(self, dark: bool = False) -> None:
        super().__init__()
        self.dark = bool(dark)
        self.app = QApplication.instance() or QApplication(sys.argv[:1])

        self.roles: dict = {}
        self.colors: list = []
        self.edited: set = set()
        self.ansi_edited = False
        #: 每个模式各自的编辑状态（切来切去不丢没保存的改动）
        self.state: dict = {"light": None, "dark": None}

        self.setWindowTitle(self._title())
        self.resize(1260, 820)
        self._build_ui()
        self._load_state()
        self._refresh_all()

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _title(self) -> str:
        return "配色工作台 —— {}".format("深色模式" if self.dark else "浅色模式")

    def _mode(self) -> str:
        return "dark" if self.dark else "light"

    def _default_colors(self) -> list:
        return list(theme_tokens.ANSI_DARK_COLORS if self.dark
                    else theme_tokens.ANSI_LIGHT_COLORS)

    def _save_state(self) -> None:
        self.state[self._mode()] = (dict(self.roles), list(self.colors),
                                    set(self.edited), bool(self.ansi_edited))

    def _load_state(self) -> None:
        """载入当前模式的编辑状态：优先本次会话改过的，其次已保存的，最后内置。"""
        saved = self.state[self._mode()]
        if saved is not None:
            self.roles, self.colors, self.edited, self.ansi_edited = (
                dict(saved[0]), list(saved[1]), set(saved[2]), bool(saved[3]))
            return
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

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("模式："))
        self.mode_box = QComboBox(self)
        self.mode_box.addItems(["浅色模式", "深色模式"])
        self.mode_box.setCurrentIndex(1 if self.dark else 0)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        top.addWidget(self.mode_box)
        top.addSpacing(12)
        hint = QLabel(
            "改完直接生效：窗口本身、左侧样例、下面的日志区都会跟着变；"
            "满意后点右下「保存为自定义配色」，重启管理器即生效。", self)
        hint.setWordWrap(True)
        top.addWidget(hint, 1)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ---- 左：日志预览 + 界面样例 ----
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
        self.sample_list.setCurrentRow(0)          # 选中行 → selection_bg / selection_text
        self.sample_list.setMaximumHeight(84)
        sample_layout.addWidget(self.sample_list)

        row = QHBoxLayout()
        self.sample_button = QPushButton("启动全部", sample_box)
        self.sample_disabled = QPushButton("已禁用", sample_box)
        self.sample_disabled.setEnabled(False)
        self.sample_line = QLineEdit("输入框（base 底色）", sample_box)
        self.sample_muted = QLabel("次要文字 · muted（状态栏那种）", sample_box)
        row.addWidget(self.sample_button)
        row.addWidget(self.sample_disabled)
        row.addWidget(self.sample_line, 1)
        sample_layout.addLayout(row)
        sample_layout.addWidget(self.sample_muted)
        left_layout.addWidget(sample_box, 1)
        splitter.addWidget(left)

        # ---- 右：色板 ----
        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)

        roles_box = QGroupBox("界面颜色（点色块改色）", right)
        roles_grid = QGridLayout(roles_box)
        roles_grid.setSpacing(4)
        self.role_swatches = {}
        for index, role in enumerate(theme_tokens.UI_ROLES):
            label = theme_tokens.ROLE_LABELS.get(role, role)
            swatch = Swatch(label, roles_box)
            swatch.clicked.connect(lambda _checked=False, name=role: self._pick_role(name))
            roles_grid.addWidget(swatch, index % 8, index // 8)
            self.role_swatches[role] = swatch
        right_layout.addWidget(roles_box)

        ansi_box = QGroupBox("ANSI 16 色（日志里的终端颜色）", right)
        ansi_grid = QGridLayout(ansi_box)
        ansi_grid.setSpacing(4)
        self.swatches = []
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
            ("恢复内置（此模式）", self._on_reset),
            ("保存为自定义配色", self._on_save),
            ("清除自定义", self._on_clear),
            ("复制为代码", self._on_copy),
        ):
            button = QPushButton(text, right)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        right_layout.addLayout(buttons)
        right_layout.addStretch(1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

        self.status = QLabel("", self)
        outer.addWidget(self.status)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def _on_mode_changed(self, index: int) -> None:
        self._save_state()
        self.dark = index == 1
        self.setWindowTitle(self._title())
        self._load_state()
        self._refresh_all()

    def _pick_role(self, role: str) -> None:
        chosen = QColorDialog.getColor(
            QColor(self.roles.get(role, "#ffffff")), self,
            "选择：{}".format(theme_tokens.ROLE_LABELS.get(role, role)))
        if not chosen.isValid():
            return
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
        self.colors[index] = chosen.name().lower()
        self.ansi_edited = self.colors != self._default_colors()
        self._refresh_all()

    def _on_reset(self) -> None:
        """当前模式回到内置（只改运行期值，不写盘）。"""
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

    def _on_save(self) -> None:
        """把"与内置不同"的那些写进 QSettings（没改的会被清掉）。"""
        mode = self._mode()
        payload = {}
        for role in theme_tokens.UI_ROLES:
            payload["{}_{}".format(mode, role)] = (
                self.roles[role] if role in self.edited else "")
        payload["{}_ansi".format(mode)] = self.colors if self.ansi_edited else ()
        if not theme_tokens.save_custom_colors(None, **payload):
            QMessageBox.warning(self, "保存失败", "写设置失败，请看控制台输出。")
            return
        QMessageBox.information(
            self, "已保存",
            "{}的自定义配色已保存（{} 处）。\n重启管理器（python main.py）后生效。".format(
                "深色模式" if self.dark else "浅色模式",
                len(self.edited) + (1 if self.ansi_edited else 0)))

    def _on_clear(self) -> None:
        answer = QMessageBox.question(
            self, "清除自定义", "清除后回到内置配色（深浅两套一起清）。继续？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        theme_tokens.clear_custom_colors(None)
        self.state = {"light": None, "dark": None}
        self._load_state()
        self._refresh_all()
        QMessageBox.information(self, "已清除", "已回到内置配色，重启管理器生效。")

    def _on_copy(self) -> None:
        """把当前配色复制成可直接粘进 theme.py 的代码。"""
        mode = self._mode()
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
        QMessageBox.information(self, "已复制",
                                "配置代码已放进剪贴板（可直接粘给作者）：\n\n" + code)

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------

    def _apply_to_theme(self) -> None:
        """把工作台里的值塞给 theme（运行期覆盖），用于即时预览。"""
        values = {"{}_{}".format(self._mode(), role): color
                  for role, color in self.roles.items()}
        values["{}_ansi".format(self._mode())] = self.colors
        theme_tokens.set_custom_colors(**values)

    def _refresh_all(self) -> None:
        """改完颜色后：塞进 theme → 重上全套样式 → 重画预览与样例 → 刷新色块。"""
        self._apply_to_theme()
        theme_tokens.apply_theme(self.app, self._mode())
        self.preview.apply_theme()
        self.preview.load_raw_text(
            build_sample(self.colors, self.roles.get("bg", "#ffffff"),
                         self.roles.get("fg", "#000000")))
        self.setStyleSheet(theme_tokens.chrome_qss(self))
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
            swatch.update_color(color, detail, warn=warn)
        for index, swatch in enumerate(self.swatches):
            color = self.colors[index]
            ratio = theme_tokens.contrast_ratio(color, self.roles.get("bg", "#ffffff"))
            swatch.update_color(color, "{:.2f}".format(ratio), warn=ratio < 4.5)

    def _refresh_summary(self) -> None:
        background = self.roles.get("bg", "#ffffff")
        ratios = sorted(theme_tokens.contrast_ratio(color, background)
                        for color in self.colors)
        weak = [ANSI_NAMES[index] for index, color in enumerate(self.colors)
                if theme_tokens.contrast_ratio(color, background) < 4.5]
        switched = "深色" if self.dark else "浅色"
        custom = theme_tokens.custom_colors()
        self.summary.setText(
            "当前编辑：{}　改过 {} 个界面角色{}{}\n"
            "ANSI 16 色对比度（对日志底色 {}）：最低 {:.2f}　中位 {:.2f}{}".format(
                switched, len(self.edited),
                "（含 ANSI 16 色）" if self.ansi_edited else "",
                "　|　设置里已有 {}".format(
                    "、".join(sorted(custom))[:60] + ("…" if len(custom) > 4 else ""))
                if custom else "　|　设置里暂无自定义",
                background, ratios[0], ratios[len(ratios) // 2],
                "" if not weak else "\n⚠ 偏淡（< 4.5）：" + "、".join(weak))
        )
        self.summary.setStyleSheet("color: {};".format(
            theme_tokens.COLOR_FAILED if weak else theme_tokens.COLOR_RUNNING))
        self.status.setText(
            "提示：对比度红框 = 这一项在它该压住的底色上不够清楚；"
            "「保存为自定义配色」只写与内置不同的项。")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(theme_tokens.APP_NAME)
    app.setOrganizationName(theme_tokens.ORG_NAME)

    theme_tokens.load_custom_colors()
    dark = any(str(arg).lower() in ("dark", "--dark", "深色") for arg in sys.argv[1:])
    theme_tokens.apply_theme(app, "dark" if dark else "light")

    studio = Studio(dark=dark)
    studio.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
