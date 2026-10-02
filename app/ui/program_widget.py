# -*- coding: utf-8 -*-
"""程序日志控件：用只读的 QPlainTextEdit 显示某个程序的实时输出。

设计要点
--------
1. 只读 QPlainTextEdit + 等宽字体，纯日志用途，不承载启动/停止按钮。
2. 对外只暴露一个核心方法：append_log(text)；另附少量辅助方法
   （clear_log / copy_all / line_count / set_status_text 等）。
3. 通过 setMaximumBlockCount 限制最大行数，长时间运行不会吃掉内存。
4. 追加时使用规范化的 \\n，避免 Windows 上出现空行翻倍。
5. 自动滚动：默认贴在底部；用户手动向上翻看历史时会自动暂停跟随，
   重新滚到底部即恢复（工具栏里也有开关）。
6. **还原终端颜色**：机器人往往往管道里也写 ANSI 转义序列，日志区以前会
   原样显示成 `←[32;20m…←[0m`。现在由 `app/ansi.py` 解析成"文字 + 样式"，
   这里按主题上色 —— 具体颜色由 `theme.ansi_color()` 给（深浅两套色板），
   终端里什么样，这里基本就什么样。同时保留一份**含序列的原文**，
   换主题 / 切布局时用它重画，颜色不会跟着旧主题留下来。
7. 不依赖 app.config / app.process_manager，可单独实例化使用：
       widget = ProgramWidget("主程序", "主程序", r"D:\\bots\\example")
       widget.append_log("启动完成")
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPalette,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ansi import AnsiParser, AnsiStyle, has_ansi, strip_ansi  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 默认保留的最大日志行数（超出后自动丢弃最旧的行）
DEFAULT_MAX_LINES = 5000

#: 输入框最小高度
MIN_EDITOR_HEIGHT = 160

#: 默认等宽字体候选（Windows 优先 Consolas）
MONOSPACE_FAMILIES = (
    "Consolas",
    "Cascadia Mono",
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Courier New",
    "monospace",
)


def is_dark_theme(widget: Optional[QWidget] = None) -> bool:
    """当前是否为深色主题。

    R2 起统一由 app.ui.theme 判定（显式模式 > 系统 colorScheme > 调色板亮度），
    这里保留原名转发，避免历史调用点全部改名。
    """
    return theme_tokens.is_dark(widget)


def log_style_sheet(editor: QPlainTextEdit) -> str:
    """按当前主题生成日志区样式（深色用固定配色，浅色取调色板）。"""
    return theme_tokens.log_editor_qss(editor)


def muted_text_color(widget: Optional[QWidget] = None) -> str:
    """次要文字颜色（跟随主题）。"""
    return theme_tokens.muted_text_color(widget)


def normalize_log_text(text: object) -> str:
    """把任意输入规范化为可追加的文本：统一换行符、去掉首尾多余空行。"""
    if text is None:
        return ""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            text = text.decode("gbk", errors="replace")
    if not isinstance(text, str):
        text = str(text)

    # QProcess / 重定向输出里常见 \r\n 与单独 \r，统一成 \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return text

    # 去掉结尾多余换行，由 append_log 统一补一个
    while text.endswith("\n\n"):
        text = text[:-1]
    if text.endswith("\n"):
        return text
    return text + "\n"


def normalize_raw_text(text: object) -> str:
    """"搬运级"规范化：解码 + 统一换行，**不**补结尾换行、不删结尾空行。

    与 :func:`normalize_log_text` 的分工：
      · normalize_log_text 处理"新来的一块输出"—— 要保证以换行结尾（见上面的说明）；
      · 本函数处理"整段原文搬回来"（布局切换时的重建）—— 原文可能正好停在半行上，
        照原样放回去，接下来的输出才能接着那一行往下写。
    """
    if text is None:
        return ""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            text = text.decode("gbk", errors="replace")
    if not isinstance(text, str):
        text = str(text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def monospace_font(point_size: int = 9) -> QFont:
    """构造一个可用的等宽字体。"""
    app = QApplication.instance()
    font = QFont()
    if app is not None:
        font = QFont(QApplication.font())
    else:
        font = QFont(MONOSPACE_FAMILIES[0])
    font.setStyleHint(QFont.StyleHint.Monospace)
    for family in MONOSPACE_FAMILIES:
        font.setFamily(family)
        try:
            from PyQt6.QtGui import QFontInfo

            if QFontInfo(font).family().lower() == family.lower():
                break
        except Exception:
            break
    font.setPointSize(point_size)
    font.setFixedPitch(True)
    return font


# ---------------------------------------------------------------------------
# 主控件
# ---------------------------------------------------------------------------

class ProgramWidget(QWidget):
    """只读日志视图：标题栏（程序名 / 状态 / 行数）+ 日志文本区 + 工具栏。"""

    #: 用户点击"打开目录"时发出，参数为目录路径
    openDirectoryRequested = pyqtSignal(str)
    #: 日志被清空时发出
    logCleared = pyqtSignal()

    def __init__(
        self,
        program_name: str = "程序",
        role_text: str = "",
        working_dir: str = "",
        parent: Optional[QWidget] = None,
        max_lines: int = DEFAULT_MAX_LINES,
        show_toolbar: bool = True,
    ) -> None:
        super().__init__(parent)

        self._program_name = str(program_name or "程序")
        self._role_text = str(role_text or "")
        self._working_dir = str(working_dir or "")
        self._max_lines = max(50, int(max_lines))
        self._auto_scroll = True
        self._line_count = 0
        #: ANSI 解析器（**跨块保留状态**：序列可能被 QProcess 从中间切开，
        #: 颜色也可能跨行延续 —— 终端就是这样）
        self._ansi = AnsiParser()
        #: 含转义序列的原文，只留最近 _max_lines 行（换主题/切布局时重画用）
        self._raw_text = ""
        self._raw_lines = 0

        self._build_ui(show_toolbar)
        self._apply_style()
        self._update_header()

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_ui(self, show_toolbar: bool) -> None:
        """构建界面。

        布局（**只有一行头部**，真机反馈"两栏功能重复"后合并）：

            ① 头部/工具栏行：程序名（粗体） 状态 … [按钮组] 行数
            ② 日志区（占满剩余空间）

        为什么把"标题行"与"工具栏行"并成一行：
        以前是两行 —— 第一行放 程序名 + 状态 + 行数，第二行放 清空/复制/打开目录/自动滚动。
        而 PaneWidget（分屏窗格）自己还有一条标题栏（程序名 + 状态 + PID + 启停按钮），
        于是同一个界面上**同一个程序名与状态出现两次**（截图为证）。

        合并后只有一个行头，把"行数"放到最右、按钮放中间：
        · 嵌在窗格里时（show_toolbar=False）这一行只剩一个"行数"标签，
          程序名与状态由窗格标题栏负责，不再重复；
        · 单独作为大日志窗口时这一行同时承担标题与工具栏。
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # --- 唯一的头部行（标题 + 状态 + 按钮 + 行数）---
        self.header = QWidget(self)
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        self.title_label = QLabel(self._program_name, self.header)
        self.title_label.setObjectName("programTitle")
        title_font = self.title_label.font()
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        self.status_label = QLabel("未启动", self.header)
        self.status_label.setObjectName("programStatus")
        self.status_label.setToolTip("进程状态")

        self.count_label = QLabel("0 行", self.header)
        self.count_label.setObjectName("programCount")
        self.count_label.setToolTip("当前日志行数")

        header_row.addWidget(self.title_label)
        header_row.addWidget(self.status_label)
        header_row.addStretch(1)

        # --- 按钮组（与标题同一行）---
        self.toolbar = QWidget(self.header)
        toolbar_row = QHBoxLayout(self.toolbar)
        toolbar_row.setContentsMargins(0, 0, 0, 0)
        toolbar_row.setSpacing(6)

        self.clear_button = QPushButton("清空日志", self.toolbar)
        self.clear_button.setToolTip("清空当前程序的日志显示")
        self.clear_button.clicked.connect(self.clear_log)

        self.copy_button = QPushButton("复制全部", self.toolbar)
        self.copy_button.setToolTip("把全部日志复制到剪贴板")
        self.copy_button.clicked.connect(self.copy_all)

        self.open_dir_button = QPushButton("打开目录", self.toolbar)
        self.open_dir_button.setToolTip("用资源管理器打开该程序的工作目录")
        self.open_dir_button.clicked.connect(self._on_open_directory)

        self.auto_scroll_box = QCheckBox("自动滚动", self.toolbar)
        self.auto_scroll_box.setToolTip("取消勾选可暂停跟随最新输出")
        self.auto_scroll_box.setChecked(True)
        self.auto_scroll_box.toggled.connect(self.set_auto_scroll)

        toolbar_row.addWidget(self.clear_button)
        toolbar_row.addWidget(self.copy_button)
        toolbar_row.addWidget(self.open_dir_button)
        toolbar_row.addWidget(self.auto_scroll_box)

        header_row.addWidget(self.toolbar)
        header_row.addWidget(self.count_label)
        layout.addWidget(self.header)

        self.toolbar.setVisible(bool(show_toolbar))
        # 嵌在窗格里时标题/状态由窗格标题栏显示，这里隐藏以免重复；
        # 行数标签始终保留（窗格标题栏没有这个信息）。
        self.title_label.setVisible(bool(show_toolbar))
        self.status_label.setVisible(bool(show_toolbar))
        # 若这一行最终什么都不显示（行数被窗格搬走、按钮组也隐藏），
        # 就连同它一起收起来 —— 否则会留一条空白带占高度。
        self._sync_header_visible()

        # --- 日志区 ---
        self.editor = QPlainTextEdit(self)
        self.editor.setObjectName("programLog")
        self.editor.setReadOnly(True)
        self.editor.setUndoRedoEnabled(False)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setMaximumBlockCount(self._max_lines)
        self.editor.setFont(monospace_font(9))
        self._apply_tab_stops()
        self.editor.setPlaceholderText("（暂无日志输出）")
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.editor.setMinimumHeight(MIN_EDITOR_HEIGHT)
        self.editor.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        # 用户滚动时更新"是否贴底"
        self.editor.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        layout.addWidget(self.editor, 1)

        self._update_open_dir_state()

    def _sync_header_visible(self) -> None:
        """头部行里没有任何可见控件时，把整行也隐藏掉。

        场景：嵌在窗格里（``show_toolbar=False``）时，标题与状态隐藏、
        "行数"标签被窗格标题栏接管（``take_count_label`` 把它 setParent(None)）
        —— 此时这一行已经空了，但 QWidget + QVBoxLayout 仍会保留一点高度，
        屏幕上就是一条空白带。这里按需收起。
        """
        header = getattr(self, "header", None)
        if header is None:
            return
        try:
            children = (
                self.title_label,
                self.status_label,
                getattr(self, "toolbar", None),
                getattr(self, "count_label", None),
            )
            any_visible = any(
                child is not None and child.isVisible() and child.parent() is not None
                for child in children
            )
            header.setVisible(any_visible)
        except (RuntimeError, AttributeError):
            pass

    def count_text(self) -> str:
        """当前"行数"文字的对外只读访问（窗格需要把它显示到自己的标题栏用）。"""
        try:
            return self.count_label.text()
        except (AttributeError, RuntimeError):
            return ""

    def take_count_label(self) -> Optional[QLabel]:
        """把"行数"标签的**所有权**交给调用方（窗格把它并进自己的标题栏）。

        真机反馈："这两栏功能上是有重复的，建议合并" —— 窗格标题栏已经有
        程序名/状态/PID/启停按钮，唯一缺的就是"行数"，所以把它搬过去，
        让窗格标题栏成为**唯一**的一行；搬走之后这里不再显示它。

        标签对象本身保留（``self.count_label`` 仍然有效，``_update_count_label()``
        照旧更新文字），只是换了父控件；换父后样式表需要重新套一次（见下）。
        """
        label = getattr(self, "count_label", None)
        if label is None:
            return None
        try:
            label.setParent(None)
            self._apply_style()          # 换父后重新上色
            self._sync_header_visible()  # 行数没了 → 这一行可能整行收起
        except (RuntimeError, AttributeError):
            return None
        return label

    def _apply_style(self) -> None:
        """应用样式：日志区与提示文字都跟随当前主题（深色 / 浅色）。

        N2.3：日志区改为**直接设置控件调色板**（`apply_log_palette`）。
        真机实测：只靠样式表时，`QPlainTextEdit` 的底色/文字色在切换主题后
        可能不重绘或被子控件的重新 polish 覆盖，表现为"日志区永远停在启动时的颜色"。
        控件调色板 + 强制 viewport 重绘是可靠路径；样式表只保留边框与圆角。
        """
        try:
            theme_tokens.apply_log_palette(self.editor, theme_tokens.is_dark(self.editor))
        except (AttributeError, TypeError, RuntimeError):
            # 兜底：回到纯样式表方式（至少不会崩）
            self.editor.setStyleSheet(log_style_sheet(self.editor))
        muted = "color: {};".format(muted_text_color(self))
        self.status_label.setStyleSheet(muted)
        self.count_label.setStyleSheet(muted)

    def apply_theme(self) -> None:
        """主题变化时重新套用样式（由主窗口在切换/系统变化时调用）。

        日志里可能带 ANSI 颜色 —— 那些颜色是**按旧主题**选的（深色下配的亮黄
        亮白挪到浅色底上根本看不见），所以有颜色的日志要用新色板重画一遍；
        纯文本日志没有颜色，直接跳过（省一次全量重绘）。
        """
        try:
            self._apply_style()
        except RuntimeError:
            return
        self._rerender_if_colored()

    def _rerender_if_colored(self) -> None:
        """用当前主题把日志重画一遍（滚动位置按比例还原）。"""
        if not has_ansi(self._raw_text):
            return
        try:
            scrollbar = self.editor.verticalScrollBar()
            maximum = scrollbar.maximum()
            ratio = (scrollbar.value() / float(maximum)) if maximum > 0 else 1.0
            raw = self._raw_text
            self.editor.clear()
            self._ansi.reset()
            self._raw_text = ""
            self._raw_lines = 0
            self._remember_raw(raw)
            self._write_runs(raw)
            scrollbar.setValue(int(round(ratio * scrollbar.maximum())))
        except (RuntimeError, AttributeError):
            pass

    # ------------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------------

    def append_log(self, text: object) -> None:
        """追加一段日志文本（自动补换行、限制行数、按需滚动到底部）。

        这是本控件的主入口，可以直接连接 ProcessManager 的 output_text 信号：
            manager.output_text.connect(
                lambda key, chunk, channel: widget.append_log(chunk)
            )

        文本里带 ANSI 转义序列时（机器人程序很常见）会按终端颜色上色，
        序列本身不会出现在界面上。
        """
        content = normalize_log_text(text)
        if not content:
            return

        # 记录追加前是否贴底，避免插入过程中触发滚动判断
        stick = self._auto_scroll or self.is_at_bottom()

        scrollbar = self.editor.verticalScrollBar()
        previous = scrollbar.value()

        self._remember_raw(content)     # 先留原文（换主题时要靠它重画）
        self._write_runs(content)

        self._line_count += content.count("\n")
        self._update_count_label()

        if stick:
            self.scroll_to_bottom()
        else:
            # 保持用户当前的浏览位置（插入后行数变化，尽量还原）
            scrollbar.setValue(min(previous, scrollbar.maximum()))

    def append_line(self, line: object) -> None:
        """追加单行（等价于 append_log，便于语义清晰）。"""
        self.append_log(line)

    def clear_log(self) -> None:
        """清空日志显示与行数统计。"""
        self.editor.clear()
        self._ansi.reset()
        self._raw_text = ""
        self._raw_lines = 0
        self._line_count = 0
        self._update_count_label()
        self.logCleared.emit()

    # ------------------------------------------------------------------
    # 原始日志（含 ANSI 序列）：换主题 / 切布局时重建
    # ------------------------------------------------------------------

    def raw_text(self) -> str:
        """返回**含 ANSI 序列**的原始日志（最近 max_lines 行）。

        切布局时窗格会被重建，只有把原文交出去，新窗格才能连颜色一起还原。
        """
        return self._raw_text

    def load_raw_text(self, text: object) -> None:
        """用原始日志**重建**整个日志区（重新解析 ANSI、重新按当前主题上色）。"""
        raw = normalize_raw_text(text)
        self.editor.clear()
        self._ansi.reset()
        self._raw_text = ""
        self._raw_lines = 0
        self._line_count = 0
        if raw:
            self._remember_raw(raw)
            self._write_runs(raw)
            self._line_count = raw.count("\n")
        self._update_count_label()

    def _remember_raw(self, content: str) -> None:
        """记住含序列的原文，并裁掉超出 max_lines 的最旧部分。

        日志区本来就只显示 max_lines 行，再往上留着只是白占内存；
        裁剪点固定在换行处，半截序列只会出现在**结尾**，重画时被解析器自然忽略。
        """
        self._raw_text += content
        self._raw_lines += content.count("\n")
        self._trim_raw()

    def _trim_raw(self) -> None:
        """把原文缓存裁到 max_lines 行以内（按换行切，不切碎行）。"""
        if self._raw_lines <= self._max_lines:
            return
        drop = self._raw_lines - self._max_lines
        index = 0
        for _ in range(drop):
            found = self._raw_text.find("\n", index)
            if found < 0:
                break
            index = found + 1
        self._raw_text = self._raw_text[index:]
        self._raw_lines -= drop

    def _write_runs(self, content: str) -> None:
        """把一块文本写进日志区：**每一段都带完整格式**写入，不靠 Qt 的继承。

        为什么连纯文本段也要给格式（真机验证，2026-10-02）：
        Qt 里"没有设置"的字符属性会**继承上一段文字**，而 `ESC[0m`（恢复默认）
        在我们这边就是"结束彩色段、后面按纯文本写" —— 如果那段不显式给默认色，
        它就会继续用上一段的颜色。真机表现：
            `ESC[32m绿色一行ESC[0m` 之后紧接着的普通行，整行都是绿的。
        所以纯文本段也走 `_format_for()`（它会把 前景/背景/粗体/斜体/下划线 写全）。

        另一条底线：**上色失败绝不允许影响"文字写进去"**（真机事故，2026-10-02）：
        调用链是 `output_text` 信号 → `append_log` → 这里，属于 **Qt 信号槽**；
        PyQt6 对槽里的未捕获异常会直接终止进程 —— 用户看到的就是"启动实例后闪退"。
        显示层的问题不该把整个启动管理器带走，所以解析/上色都各有兜底。
        """
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if not has_ansi(content) and not self._ansi.pending:
            # 快速路径：这一段没有序列，但**当前样式**可能还带着颜色（跨块延续）
            self._insert(cursor, content, self._ansi.style)
            return

        try:
            runs = self._ansi.feed(content)
        except Exception:  # noqa: BLE001 - 见 docstring：显示层不许拖垮程序
            self._ansi.reset()
            runs = [(strip_ansi(content), AnsiStyle())]

        for part, style in runs:
            if part:
                self._insert(cursor, part, style)

    def _insert(self, cursor: QTextCursor, text: str, style: AnsiStyle) -> None:
        """按样式插入一段文字；上色出任何问题都退回默认格式（字必须留下）。"""
        try:
            cursor.insertText(text, self._format_for(style))
        except Exception:  # noqa: BLE001 - 同上：颜色没了也要把文字写进去
            cursor.insertText(text, self._plain_format())

    def _plain_format(self) -> QTextCharFormat:
        """默认格式（日志区文字色 + 底色）。"""
        return self._format_for(AnsiStyle())

    def _format_for(self, style: AnsiStyle) -> QTextCharFormat:
        """ANSI 样式 → QTextCharFormat（颜色全部来自 theme，深浅各一套）。

        **每个属性都写全**：Qt 里没设的属性会继承上一段文字，少写一个就会出现
        "`ESC[0m` 之后颜色褪不掉"或"上一行的反显背景跟着跑"。
        """
        fmt = QTextCharFormat()
        # theme.log_colors() 返回的是**四色**：(底色, 文字色, 边框, 选中背景)，
        # 这里只用前两个 —— 别再写 `a, b = log_colors(...)`，
        # 真机事故（2026-10-02）：那样写会 ValueError，而异常发生在 Qt 信号槽里，
        # PyQt6 会直接终止进程 → 表现为"一启动就闪退"。
        base_bg, base_fg = theme_tokens.log_colors(self)[:2]
        fg = theme_tokens.ansi_color(style.fg, self) if style.fg is not None else ""
        bg = theme_tokens.ansi_color(style.bg, self) if style.bg is not None else ""
        if style.inverse:
            # 反显：前景背景对调；没显式给的那一半用日志区的默认色补上
            fg, bg = (bg or base_bg), (fg or base_fg)
        if style.faint and fg:
            fg = theme_tokens.mix_colors(fg, base_bg, 0.45)
        fmt.setForeground(QColor(fg or base_fg))
        fmt.setBackground(QColor(bg or base_bg))
        fmt.setFontWeight(QFont.Weight.Bold if style.bold else QFont.Weight.Normal)
        fmt.setFontItalic(bool(style.italic))
        fmt.setFontUnderline(bool(style.underline))
        return fmt

    def _apply_tab_stops(self) -> None:
        """把制表符宽度对齐成"8 个等宽字符"（终端的默认值）。

        QPlainTextEdit 默认按 80 像素算 tab，和等宽字体的 8 字符宽度不一致，
        于是终端里对齐的列（日志的时间戳/级别）在日志区会错位。
        """
        try:
            metrics = QFontMetricsF(self.editor.font())
            width = metrics.horizontalAdvance(" ")
            if width > 0:
                self.editor.setTabStopDistance(width * 8)
        except (AttributeError, TypeError, RuntimeError):
            pass

    def copy_all(self) -> None:
        """把全部日志复制到剪贴板。"""
        text = self.text()
        if not text:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def text(self) -> str:
        """返回当前全部日志文本。"""
        return self.editor.toPlainText()

    def line_count(self) -> int:
        """返回已追加的日志行数（含被自动丢弃的历史行计数）。"""
        return self._line_count

    def block_count(self) -> int:
        """返回当前实际保留的文本块数量。"""
        return int(self.editor.blockCount())

    def is_empty(self) -> bool:
        """当前是否没有任何日志。"""
        return self.block_count() == 0

    # ------------------------------------------------------------------
    # 配置与状态
    # ------------------------------------------------------------------

    def set_program_name(self, name: str, role_text: str = "") -> None:
        """更新标题（程序名 / 角色）。"""
        self._program_name = str(name or "程序")
        if role_text:
            self._role_text = str(role_text)
        self._update_header()

    def set_status_text(self, text: str, color: str = "") -> None:
        """更新状态标签，例如 "运行中（PID 1234）" / "已停止"。"""
        self.status_label.setText(str(text or ""))
        if color:
            self.status_label.setStyleSheet("color: {};".format(color))
        else:
            self.status_label.setStyleSheet(
                "color: {};".format(theme_tokens.status_color("stopped"))
            )

    def set_working_dir(self, path: str) -> None:
        """更新工作目录（影响"打开目录"按钮是否可用）。"""
        self._working_dir = str(path or "")
        self._update_open_dir_state()

    def working_dir(self) -> str:
        """返回当前工作目录。"""
        return self._working_dir

    def set_max_lines(self, max_lines: int) -> None:
        """调整最大保留行数。"""
        self._max_lines = max(50, int(max_lines))
        self.editor.setMaximumBlockCount(self._max_lines)
        self._trim_raw()          # 原文缓存跟着收紧，别留着已经不会显示的内容

    def max_lines(self) -> int:
        """返回最大保留行数。"""
        return self._max_lines

    def set_auto_scroll(self, enabled: bool) -> None:
        """开关自动滚动（会同步复选框状态）。"""
        self._auto_scroll = bool(enabled)
        if self.auto_scroll_box.isChecked() != self._auto_scroll:
            self.auto_scroll_box.blockSignals(True)
            self.auto_scroll_box.setChecked(self._auto_scroll)
            self.auto_scroll_box.blockSignals(False)
        if self._auto_scroll:
            self.scroll_to_bottom()

    def auto_scroll(self) -> bool:
        """当前是否启用自动滚动。"""
        return self._auto_scroll

    def scroll_to_bottom(self) -> None:
        """滚动到最底部（最新日志处）。"""
        scrollbar = self.editor.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)

    def is_at_bottom(self) -> bool:
        """当前视图是否贴在最后一行。"""
        scrollbar = self.editor.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - 2

    def set_toolbar_visible(self, visible: bool) -> None:
        """显示/隐藏工具栏（多个程序并排时可隐藏以节省空间）。"""
        self.toolbar.setVisible(bool(visible))

    def set_monospace_size(self, point_size: int) -> None:
        """调整日志字号。"""
        self.editor.setFont(monospace_font(max(6, int(point_size))))
        self._apply_tab_stops()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _update_header(self) -> None:
        if self._role_text:
            self.title_label.setText("{}（{}）".format(self._program_name, self._role_text))
        else:
            self.title_label.setText(self._program_name)
        self.title_label.setToolTip(self._working_dir or self._program_name)

    def _update_count_label(self) -> None:
        self.count_label.setText("{} 行".format(self._line_count))

    def _update_open_dir_state(self) -> None:
        exists = bool(self._working_dir) and os.path.isdir(self._working_dir)
        self.open_dir_button.setEnabled(exists)
        if exists:
            self.open_dir_button.setToolTip("打开目录：{}".format(self._working_dir))
        else:
            self.open_dir_button.setToolTip("工作目录不存在或未配置")

    def _on_scrolled(self, _value: int) -> None:
        """滚动条变化：贴底时恢复自动滚动跟随。"""
        if self.is_at_bottom() and not self._auto_scroll:
            # 用户手动滚回底部，视为希望继续跟随
            self.auto_scroll_box.setChecked(True)

    def _on_open_directory(self) -> None:
        """请求打开工作目录（优先交给外部处理，其次自己打开）。"""
        path = self._working_dir
        if not path:
            return
        self.openDirectoryRequested.emit(path)
        if not os.path.isdir(path):
            return
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 自检：可用 QT_QPA_PLATFORM=offscreen 无界面运行
# ---------------------------------------------------------------------------

def _selftest() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    widget = ProgramWidget("主程序", "主程序", os.path.dirname(os.path.abspath(__file__)))
    widget.resize(720, 420)
    widget.show()

    widget.append_log("UTF-8 输出：机器人已启动\n")
    widget.append_log("CRLF 输出\r\n第二行\r\n")
    widget.append_log("单独 CR\r被当作换行")
    widget.append_log("")            # 空内容应被忽略
    widget.append_log(None)          # None 应被忽略

    text = widget.text()
    print("行数统计 =", widget.line_count(), "| 文本块数 =", widget.block_count())
    print("内容 =", repr(text))
    assert widget.line_count() == 5, widget.line_count()
    assert "机器人已启动" in text
    assert "\r" not in text, "不应残留回车符"
    assert text.endswith("被当作换行\n")

    # --- ANSI：真机日志里的两种序列（INFO 绿色 / DEBUG 残缺扩展色）---
    info = "\x1b[32;20m10-02 13:22:52 [INFO] 插件已加载\x1b[0m\n"
    debug = "\x1b[38;20m10-02 13:22:53 [DEBUG] 群相关事件\x1b[0m\n"
    widget.append_log(info)
    widget.append_log(debug)
    shown = widget.text()
    assert "\x1b" not in shown, "转义序列不该出现在界面上"
    assert "10-02 13:22:52 [INFO] 插件已加载" in shown
    assert "\x1b[32;20m" not in shown and "\x1b[0m" not in shown
    assert widget.raw_text().count("\x1b") >= 4, "原文里要保留序列（换主题重画用）"
    print("ANSI：界面文字 =", repr(shown.splitlines()[-2]))

    # 跨块切开的序列也要认（QProcess 会随机切块）
    widget.clear_log()
    widget.append_log("\x1b[3")
    widget.append_log("1m红色文字\x1b[0m\n")
    assert widget.text().strip() == "红色文字", repr(widget.text())

    # 换主题要重画（有颜色时才重画，纯文本不折腾）
    widget.apply_theme()
    assert widget.text().strip() == "红色文字", repr(widget.text())

    # 重建：布局切换时把原文灌回来，颜色一起还原
    widget.load_raw_text("\x1b[32m绿\x1b[0m + \x1b[31m红\x1b[0m\n")
    assert widget.text().strip() == "绿 + 红", repr(widget.text())
    assert widget.line_count() == 1, widget.line_count()

    widget.clear_log()
    assert widget.raw_text() == "" and widget.line_count() == 0, "清空要连原文一起清"
    assert widget.is_empty()

    widget.set_status_text("运行中（PID 1234）", theme_tokens.status_color("running"))
    widget.set_max_lines(100)
    assert widget.max_lines() == 100
    widget.set_auto_scroll(False)
    assert widget.auto_scroll() is False
    widget.set_auto_scroll(True)
    widget.copy_all()
    print("自检通过：append_log / clear_log / 行数统计 / 自动滚动开关均正常。")
    widget.close()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
