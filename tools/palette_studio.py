# -*- coding: utf-8 -*-
"""日志配色工作台：在**真控件**上试色，满意了存成自定义色板。

为什么要这个东西
----------------
"日志里的颜色够不够清楚"是主观的，靠改代码猜很费劲：
改一次 `theme.py` → 重启管理器 → 盯着屏幕看 → 再改…… 而且配色和**底色**互相影响
（`#f0efe9` 这种偏暖的浅底上，蓝色/青色天然显脏），所以必须能"边改边看"。

本工具做的事：
  · 左侧就是**真的** `ProgramWidget`（和主界面同一套控件、同一条上色代码路径），
    里面躺着一段真实风格的日志：16 个 ANSI 颜色各占一行，行尾标着
    它和当前底色的 **WCAG 对比度**，低于 4.5 会写"偏淡"；
  · 右侧是 16 个色块 + 日志底色 / 文字色，点一下就开取色器，改完立刻重画；
  · 底色给了几个预设（内置的 `#f0efe9`、纯白、米白……）—— 用来验证
    "是不是浅色底本身让颜色发闷"；
  · 满意后点「保存为自定义色板」：值写进管理器自己的 QSettings，
    下次启动 `python main.py`（或 exe）就生效；不想要了随时点「清除自定义」。

用法：
    python tools\\palette_studio.py            # 先看浅色
    python tools\\palette_studio.py dark       # 直接看深色

注意：改的是**日志区**（底色 / 文字色 / 16 个 ANSI 色）。界面其它部分
（窗口底色、按钮、左栏）不在这里管 —— 那些颜色是成套推导的，单改一个只会更花。
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
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme as theme_tokens                          # noqa: E402
from app.ui.program_widget import ProgramWidget                   # noqa: E402

#: 16 个颜色的中文名（编号与终端一致）
ANSI_NAMES = (
    "30 黑", "31 红", "32 绿", "33 黄", "34 蓝", "35 品红", "36 青", "37 白",
    "90 亮黑", "91 亮红", "92 亮绿", "93 亮黄", "94 亮蓝", "95 亮品红", "96 亮青", "97 亮白",
)

#: 写进预览文本用的 ANSI 编号
ANSI_CODES = tuple(list(range(30, 38)) + list(range(90, 98)))

#: 底色预设：显示名 → 颜色（空串 = 跟随内置）
BG_PRESETS = (
    ("跟随内置", ""),
    ("纯白 #ffffff", "#ffffff"),
    ("米白 #faf8f2", "#faf8f2"),
    ("淡灰 #f4f4f4", "#f4f4f4"),
    ("浅米黄 #fdf6e3", "#fdf6e3"),
    ("淡蓝灰 #eef2f6", "#eef2f6"),
)

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


def build_sample(palette, background: str) -> str:
    """生成预览文本：16 色各一行 + 真实日志 + 样式演示。

    行尾的对比度是**按这一版候选颜色现算的** —— 改一个颜色，整段的数字跟着变。
    """
    lines = ['═══ ① ANSI 16 色（行尾 = 与当前底色的 WCAG 对比度，< 4.5 标"偏淡"）═══']
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
    return "\n".join(lines) + "\n"


class Swatch(QPushButton):
    """一个色块按钮：显示颜色、编号、hex、对比度；点它开取色器。"""

    def __init__(self, label: str, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(34)
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
        self.setText("{}　{}　{}".format(self._label, color, detail).rstrip())
        self.setToolTip("点一下改这个颜色（当前对比度 {}）".format(detail))


class Studio(QWidget):
    """工作台主窗口。"""

    def __init__(self, dark: bool = False) -> None:
        super().__init__()
        self.dark = bool(dark)
        self.app = QApplication.instance() or QApplication(sys.argv[:1])

        self.colors: list = []
        self.background = ""
        self.foreground = ""
        self.bg_custom = False
        #: 每个模式各自的编辑状态（切来切去不丢没保存的改动）
        self.state: dict = {"light": None, "dark": None}

        self.setWindowTitle(self._title())
        self.resize(1180, 760)
        self._build_ui()
        self._load_state()
        self._refresh_all()

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _title(self) -> str:
        return "日志配色工作台 —— {}".format("深色模式" if self.dark else "浅色模式")

    def _builtin_bg(self) -> str:
        return theme_tokens.log_colors_for(self.dark)[0]

    def _default_colors(self) -> list:
        return list(theme_tokens.ANSI_DARK_COLORS if self.dark
                    else theme_tokens.ANSI_LIGHT_COLORS)

    def _save_state(self) -> None:
        self.state["dark" if self.dark else "light"] = (
            list(self.colors), self.background, self.foreground, self.bg_custom)

    def _load_state(self) -> None:
        """载入当前模式的编辑状态：优先用本次会话里改过的，其次用已保存/内置的。"""
        saved = self.state["dark" if self.dark else "light"]
        if saved is not None:
            self.colors, self.background, self.foreground, self.bg_custom = (
                list(saved[0]), saved[1], saved[2], saved[3])
            return
        custom = theme_tokens.custom_colors()
        key = "dark_ansi" if self.dark else "light_ansi"
        saved_palette = custom.get(key)
        self.colors = list(saved_palette) if saved_palette else self._default_colors()
        bg_key, fg_key = ("dark_bg", "dark_fg") if self.dark else ("light_bg", "light_fg")
        self.bg_custom = bool(custom.get(bg_key))
        self.background = str(custom.get(bg_key) or self._builtin_bg())
        self.foreground = str(custom.get(fg_key) or theme_tokens.log_colors_for(self.dark)[1])

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # --- 顶部：模式 / 底色 ---
        top = QHBoxLayout()
        top.addWidget(QLabel("模式："))
        self.mode_box = QComboBox(self)
        self.mode_box.addItems(["浅色模式", "深色模式"])
        self.mode_box.setCurrentIndex(1 if self.dark else 0)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        top.addWidget(self.mode_box)

        top.addSpacing(18)
        top.addWidget(QLabel("日志底色："))
        self.bg_box = QComboBox(self)
        for name, _value in BG_PRESETS:
            self.bg_box.addItem(name)
        self.bg_box.currentIndexChanged.connect(self._on_bg_preset)
        top.addWidget(self.bg_box)

        pick_bg = QPushButton("自选底色…", self)
        pick_bg.clicked.connect(lambda: self._pick_color("background"))
        top.addWidget(pick_bg)
        pick_fg = QPushButton("自选文字色…", self)
        pick_fg.clicked.connect(lambda: self._pick_color("foreground"))
        top.addWidget(pick_fg)
        top.addStretch(1)
        outer.addLayout(top)

        # --- 中间：左预览 / 右色板 ---
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.preview = ProgramWidget("日志预览", "", "", max_lines=2000, show_toolbar=True)
        splitter.addWidget(self.preview)

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        grid_box = QGroupBox("ANSI 16 色（点色块改色）", right)
        grid = QGridLayout(grid_box)
        grid.setSpacing(4)
        self.swatches = []
        for index in range(16):
            swatch = Swatch(ANSI_NAMES[index], grid_box)
            swatch.clicked.connect(lambda _checked=False, position=index: self._pick_color(position))
            grid.addWidget(swatch, index % 8, index // 8)
            self.swatches.append(swatch)
        right_layout.addWidget(grid_box)

        self.summary = QLabel("", right)
        self.summary.setWordWrap(True)
        right_layout.addWidget(self.summary)

        row = QHBoxLayout()
        for text, handler in (
            ("重置为内置", self._on_reset),
            ("保存为自定义色板", self._on_save),
            ("清除自定义", self._on_clear),
            ("复制为代码", self._on_copy),
        ):
            button = QPushButton(text, right)
            button.clicked.connect(handler)
            row.addWidget(button)
        right_layout.addLayout(row)

        hint = QLabel(
            "对比度 < 4.5 的颜色会用红框标出（正文级最低要求）。\n"
            "换个底色再比一遍 —— 底色一变，合适的颜色也会变。\n"
            "「保存为自定义色板」写进管理器自己的设置里，重启管理器生效。",
            right,
        )
        hint.setWordWrap(True)
        right_layout.addWidget(hint)
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
        self._save_state()                       # 先存下当前模式的改动
        self.dark = index == 1
        self.setWindowTitle(self._title())
        theme_tokens.apply_theme(self.app, "dark" if self.dark else "light")
        self._load_state()
        self.bg_box.setCurrentIndex(0)
        self._refresh_all()

    def _on_bg_preset(self, index: int) -> None:
        if not (0 <= index < len(BG_PRESETS)):
            return
        preset = BG_PRESETS[index][1]
        self.bg_custom = bool(preset)
        self.background = preset or self._builtin_bg()
        self._refresh_all()

    def _pick_color(self, target) -> None:
        """target 是 0-15 的下标，或 "background" / "foreground"。"""
        if isinstance(target, int):
            current, title = self.colors[target], "选择 {}".format(ANSI_NAMES[target])
        elif target == "background":
            current, title = self.background, "选择日志底色"
        else:
            current, title = self.foreground, "选择日志文字色"
        chosen = QColorDialog.getColor(QColor(current), self, title)
        if not chosen.isValid():
            return
        value = chosen.name().lower()
        if isinstance(target, int):
            self.colors[target] = value
        elif target == "background":
            self.background, self.bg_custom = value, True
            self.bg_box.setCurrentIndex(0)
        else:
            self.foreground = value
        self._refresh_all()

    def _on_reset(self) -> None:
        """回到内置（只动当前模式，且只改运行期值，不写盘）。"""
        self.colors = self._default_colors()
        self.bg_custom = False
        self.background = self._builtin_bg()
        self.foreground = theme_tokens.log_colors_for(self.dark)[1]
        self.bg_box.setCurrentIndex(0)
        self._refresh_all()

    def _on_save(self) -> None:
        payload = {
            "dark_ansi" if self.dark else "light_ansi": self.colors,
            "dark_bg" if self.dark else "light_bg": self.background if self.bg_custom else "",
            "dark_fg" if self.dark else "light_fg":
                self.foreground if self.foreground != theme_tokens.log_colors_for(self.dark)[1]
                else "",
        }
        if not theme_tokens.save_custom_colors(None, **payload):
            QMessageBox.warning(self, "保存失败", "写设置失败，请看控制台输出。")
            return
        QMessageBox.information(
            self, "已保存",
            "{}的自定义配色已保存。\n重启管理器（python main.py）后生效。".format(
                "深色模式" if self.dark else "浅色模式"))

    def _on_clear(self) -> None:
        answer = QMessageBox.question(
            self, "清除自定义", "清除后回到内置配色（深浅两套一起清）。继续？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        theme_tokens.clear_custom_colors(None)
        self.state = {"light": None, "dark": None}
        self._load_state()
        self.bg_box.setCurrentIndex(0)
        self._refresh_all()
        QMessageBox.information(self, "已清除", "已回到内置配色，重启管理器生效。")

    def _on_copy(self) -> None:
        """把当前色板复制成可直接粘进 theme.py 的代码。"""
        name = "ANSI_DARK_COLORS" if self.dark else "ANSI_LIGHT_COLORS"
        rows = [
            ", ".join('"{}"'.format(color) for color in self.colors[index:index + 4])
            for index in range(0, 16, 4)
        ]
        code = '{name}: Tuple[str, ...] = (\n    {body},\n)\n'.format(
            name=name, body=",\n    ".join(rows))
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(code)
        QMessageBox.information(self, "已复制", "色板代码已放进剪贴板：\n\n" + code)

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------

    def _apply_to_theme(self) -> None:
        """把工作台里的值塞给 theme（运行期覆盖），用于即时预览。"""
        if self.dark:
            theme_tokens.set_custom_colors(
                dark_ansi=self.colors, dark_bg=self.background, dark_fg=self.foreground)
        else:
            theme_tokens.set_custom_colors(
                light_ansi=self.colors, light_bg=self.background, light_fg=self.foreground)

    def _refresh_all(self) -> None:
        """改完颜色后：塞进 theme → 重套日志区样式 → 重画预览 → 刷新色块与统计。"""
        self._apply_to_theme()
        theme_tokens.apply_theme(self.app, "dark" if self.dark else "light")
        self.preview.apply_theme()
        self.preview.load_raw_text(build_sample(self.colors, self.background))
        self._refresh_swatches()
        self._refresh_summary()

    def _refresh_swatches(self) -> None:
        for index, swatch in enumerate(self.swatches):
            color = self.colors[index]
            ratio = theme_tokens.contrast_ratio(color, self.background)
            swatch.update_color(color, "{:.2f}".format(ratio), warn=ratio < 4.5)

    def _refresh_summary(self) -> None:
        ratios = sorted(theme_tokens.contrast_ratio(color, self.background)
                        for color in self.colors)
        worst, median = ratios[0], ratios[len(ratios) // 2]
        weak = [ANSI_NAMES[index] for index, color in enumerate(self.colors)
                if theme_tokens.contrast_ratio(color, self.background) < 4.5]
        fg_ratio = theme_tokens.contrast_ratio(self.foreground, self.background)
        self.summary.setText(
            "底色 {}　文字 {}（对比度 {:.2f}）\n16 色对比度：最低 {:.2f}　中位 {:.2f}{}".format(
                self.background, self.foreground, fg_ratio, worst, median,
                "" if not weak else "\n⚠ 偏淡（< 4.5）：" + "、".join(weak))
        )
        bad = bool(weak) or fg_ratio < 4.5
        self.summary.setStyleSheet("color: {};".format(
            theme_tokens.COLOR_FAILED if bad else theme_tokens.COLOR_RUNNING))
        custom = theme_tokens.custom_colors()
        self.status.setText("设置里的自定义：{}".format(
            "、".join(sorted(custom)) if custom else "（无，用内置配色）"))


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
