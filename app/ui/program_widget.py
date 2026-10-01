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
6. 不依赖 app.config / app.process_manager，可单独实例化使用：
       widget = ProgramWidget("主程序", "主程序", r"D:\\bots\\example")
       widget.append_log("启动完成")
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QDesktopServices,
    QFont,
    QGuiApplication,
    QPalette,
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

        self._build_ui(show_toolbar)
        self._apply_style()
        self._update_header()

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_ui(self, show_toolbar: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # --- 标题行 ---
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self.title_label = QLabel(self._program_name, self)
        self.title_label.setObjectName("programTitle")
        title_font = self.title_label.font()
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        self.status_label = QLabel("未启动", self)
        self.status_label.setObjectName("programStatus")
        self.status_label.setToolTip("进程状态")

        self.count_label = QLabel("0 行", self)
        self.count_label.setObjectName("programCount")
        self.count_label.setToolTip("当前日志行数")

        header.addWidget(self.title_label)
        header.addWidget(self.status_label)
        header.addStretch(1)
        header.addWidget(self.count_label)
        layout.addLayout(header)

        # --- 日志区 ---
        self.editor = QPlainTextEdit(self)
        self.editor.setObjectName("programLog")
        self.editor.setReadOnly(True)
        self.editor.setUndoRedoEnabled(False)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setMaximumBlockCount(self._max_lines)
        self.editor.setFont(monospace_font(9))
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

        # --- 工具栏 ---
        self.toolbar = QWidget(self)
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
        toolbar_row.addStretch(1)

        layout.addWidget(self.toolbar)
        self.toolbar.setVisible(bool(show_toolbar))
        self._update_open_dir_state()

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
        """主题变化时重新套用样式（由主窗口在切换/系统变化时调用）。"""
        try:
            self._apply_style()
        except RuntimeError:
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
        """
        content = normalize_log_text(text)
        if not content:
            return

        # 记录追加前是否贴底，避免插入过程中触发滚动判断
        stick = self._auto_scroll or self.is_at_bottom()

        scrollbar = self.editor.verticalScrollBar()
        previous = scrollbar.value()

        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(content)

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
        self._line_count = 0
        self._update_count_label()
        self.logCleared.emit()

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

    widget.clear_log()
    assert widget.is_empty() and widget.line_count() == 0

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
