# -*- coding: utf-8 -*-
"""「查看已有 bot」对话框（Phase B）。

用途
----
左侧面板是"正在运行的 bot 窗口"，关掉之后就没有集中管理入口了；本对话框提供
一个**不依赖任何已打开窗口**的控制台：列出 bots_config.json 里的全部机器人、
它们每个程序的状态、窗口是否已打开，并直接对选中的 bot 执行
打开窗口 / 启动 / 停止 / 重启 / 编辑配置 / 删除 / 移动顺序。

数据来源
--------
全部走宿主（MainWindow）的 Phase A 公共状态层：

    host.bot_status_list()        每个 bot 的状态快照（BotStatus）
    host.bot_status_tooltip(bot)  逐程序的详细文字

因此本对话框与左侧导航栏（Phase C）、状态栏的颜色与文案天然一致，
这里不重复实现任何"数进程"的逻辑。

宿主约定
--------
构造时传入的 host 需要提供下面这些方法（MainWindow 均已实现）：

    config / manager                                 属性
    bot_status_list() -> List[BotStatus]
    bot_status_tooltip(bot) -> str
    open_bot_tab(bot_id, focus=True) -> BotTab | None
    close_bot_window(bot_id, confirm=True) -> bool
    start_bot(bot_id) / stop_bot(bot_id) / restart_bot(bot_id) -> bool
    edit_bot(bot_id) -> None
    new_bot() -> None
    move_bot_up(bot_id) / move_bot_down(bot_id) -> bool
    remove_bot(bot_id, ask=True, stop_running=True) -> bool
    start_all_bots() -> int

运行自检（无需 PyQt6 之外的东西，但需要 QApplication）：
    python app/ui/bot_list_dialog.py --selftest
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# 允许 "python app/ui/bot_list_dialog.py" 直接运行自检
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import ROLE_PRIMARY, Bot  # noqa: E402
from app.process_manager import ProcessManager  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402
from app.ui.main_window import (  # noqa: E402
    BOT_STATUS_TEXTS,
    BotStatus,
    BotStatusFlags,
    bot_status_color,
    bot_status_dot,
    bot_status_text,
    format_bot_status,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

COL_NAME = 0
COL_STATE = 1
COL_RUNNING = 2
COL_ENABLED = 3
COL_WINDOW = 4
COL_PROGRAMS = 5

COLUMN_TITLES = ("名称", "状态", "运行", "启用", "窗口", "程序")

#: 表格里自定义数据角色的键
ROLE_BOT_ID = Qt.ItemDataRole.UserRole
ROLE_SORT = Qt.ItemDataRole.UserRole + 1

#: 自动刷新间隔（毫秒）——状态列保持"活"的
AUTO_REFRESH_MS = 1500


# ---------------------------------------------------------------------------
# 纯逻辑：把 BotStatus 转成表格一行（可独立测试）
# ---------------------------------------------------------------------------

def status_sort_key(status: BotStatus) -> int:
    """排序权重：有问题的排前面，方便一眼看到需要处理的 bot。"""
    order = {
        BotStatusFlags.FAILED: 0,
        "partial": 1,
        BotStatusFlags.DISABLED: 2,
        BotStatusFlags.STARTING: 3,
        BotStatusFlags.STOPPING: 4,
        BotStatusFlags.RUNNING: 5,
        BotStatusFlags.STOPPED: 6,
    }
    return order.get(status.status, 9)


def row_values(status: BotStatus, sort_by_state: bool = False) -> List[str]:
    """生成一行的显示文本（顺序与 COLUMN_TITLES 一致）。"""
    return [
        status.name or status.bot_id,
        bot_status_text(status),
        status.counter_text,
        "是" if status.enabled else "否",
        "已打开" if status.opened else "已关闭",
        programs_summary(status, use_newline=True),
    ]


def programs_summary(status: BotStatus, use_newline: bool = False, limit: int = 24) -> str:
    """把程序列表拼成 "★主程序(运行中), ollama(未运行), …"。

    ★ 表示 primary 角色；use_newline=True 时每个程序一行（表格里更好读）。
    limit 为最多显示的程序数，超出部分折叠成 "…等 N 个"。
    """
    items: List[str] = []
    for key, name in status.program_names.items():
        is_primary = status.roles.get(key) == ROLE_PRIMARY
        state = status.program_states.get(key, "")
        state_text = BOT_STATUS_TEXTS.get(state, "未运行")
        prefix = "★" if is_primary else ""
        items.append("{}{}({})".format(prefix, name, state_text))

    if len(items) > limit:
        hidden = len(items) - limit
        items = items[:limit] + ["…等 {} 个".format(hidden)]

    separator = "\n" if use_newline else "、"
    return separator.join(items)


def filtering_rows(rows: List[Tuple[BotStatus, List[str]]], keyword: str,
                   only_running: bool) -> List[Tuple[BotStatus, List[str]]]:
    """按关键字与"只看运行中"过滤（纯函数，便于测试）。"""
    text = (keyword or "").strip().lower()
    result: List[Tuple[BotStatus, List[str]]] = []
    for status, values in rows:
        if only_running and not status.any_running:
            continue
        if text:
            haystack = " ".join(values).lower()
            if text not in haystack:
                continue
        result.append((status, values))
    return result


# ---------------------------------------------------------------------------
# 对话框
# ---------------------------------------------------------------------------

class BotListDialog(QDialog):
    """列出全部机器人及其状态，并提供常用操作。"""

    #: 请求宿主执行某个动作：action 名称, bot_id（批量操作为 None）
    actionRequested = pyqtSignal(str, object)

    def __init__(
        self,
        host: Optional[Any] = None,
        parent: Optional[QWidget] = None,
        auto_refresh: bool = True,
    ) -> None:
        super().__init__(parent or (host if isinstance(host, QWidget) else None))

        self.host = host
        self._auto_refresh_enabled = bool(auto_refresh)
        self._build_ui()
        self.refresh()

        if self._auto_refresh_enabled:
            self._timer = QTimer(self)
            self._timer.setInterval(AUTO_REFRESH_MS)
            self._timer.timeout.connect(self._on_auto_refresh)
            self._timer.start()

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("查看已有 bot")
        self.resize(1080, 660)
        self.setMinimumSize(820, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ---- 顶部：过滤 + 刷新 ----
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("按名称 / 程序 / 状态过滤…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(lambda _text: self.refresh())

        self.only_running_box = QCheckBox("只看有进程的", self)
        self.only_running_box.setToolTip("只显示至少有一个程序在运行（含启动中/停止中）的机器人")
        self.only_running_box.toggled.connect(lambda _checked: self.refresh())

        self.live_box = QCheckBox("自动刷新", self)
        self.live_box.setChecked(self._auto_refresh_enabled)
        self.live_box.setToolTip("每 {:.1f} 秒刷新一次状态列".format(AUTO_REFRESH_MS / 1000.0))
        self.live_box.toggled.connect(self._on_live_toggled)

        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.clicked.connect(lambda: self.refresh())

        self.sort_button = QPushButton("按状态排序", self)
        self.sort_button.setCheckable(True)
        self.sort_button.setToolTip("勾选后把异常 / 部分运行 / 已禁用的排到前面")
        self.sort_button.toggled.connect(lambda _checked: self.refresh())

        top_row.addWidget(QLabel("过滤：", self))
        top_row.addWidget(self.filter_edit, 1)
        top_row.addWidget(self.only_running_box)
        top_row.addWidget(self.live_box)
        top_row.addWidget(self.sort_button)
        top_row.addWidget(self.refresh_button)
        layout.addLayout(top_row)

        # ---- 中间：表格 + 详情 ----
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)

        self.table = QTableWidget(0, len(COLUMN_TITLES), self.splitter)
        self.table.setHorizontalHeaderLabels(list(COLUMN_TITLES))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.doubleClicked.connect(lambda _index: self._do("open"))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_STATE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_RUNNING, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_ENABLED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_WINDOW, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_PROGRAMS, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_NAME, 180)
        self.splitter.addWidget(self.table)

        self.detail_view = QPlainTextEdit(self.splitter)
        self.detail_view.setReadOnly(True)
        self.detail_view.setPlaceholderText("选中一行后这里显示该机器人的程序明细")
        self.detail_view.setMinimumHeight(90)
        self.detail_view.setMaximumHeight(220)
        self.splitter.addWidget(self.detail_view)
        self.splitter.setSizes([430, 150])
        layout.addWidget(self.splitter, 1)

        # ---- 选中项操作 ----
        row_one = QHBoxLayout()
        row_one.setSpacing(6)
        self.open_button = QPushButton("打开窗口", self)
        self.open_button.setToolTip("在右侧打开该机器人的窗口（不启动程序）")
        self.open_button.clicked.connect(lambda: self._do("open"))

        self.start_button = QPushButton("启动", self)
        self.start_button.setToolTip("启动选中机器人的所有程序（会先打开窗口）")
        self.start_button.clicked.connect(lambda: self._do("start"))

        self.stop_button = QPushButton("停止", self)
        self.stop_button.setToolTip("停止选中机器人的所有程序（含进程树）")
        self.stop_button.clicked.connect(lambda: self._do("stop"))

        self.restart_button = QPushButton("重启", self)
        self.restart_button.setToolTip("先停止再启动选中的机器人")
        self.restart_button.clicked.connect(lambda: self._do("restart"))

        self.edit_button = QPushButton("编辑配置", self)
        self.edit_button.setToolTip("打开编辑对话框（保存后写回 bots_config.json）")
        self.edit_button.clicked.connect(lambda: self._do("edit"))

        self.close_window_button = QPushButton("关闭窗口", self)
        self.close_window_button.setToolTip("只关闭界面窗口，程序继续在后台运行")
        self.close_window_button.clicked.connect(lambda: self._do("close_window"))

        self.up_button = QPushButton("上移", self)
        self.up_button.setToolTip("调整该机器人在配置中的顺序")
        self.up_button.clicked.connect(lambda: self._do("up"))

        self.down_button = QPushButton("下移", self)
        self.down_button.setToolTip("调整该机器人在配置中的顺序")
        self.down_button.clicked.connect(lambda: self._do("down"))

        self.remove_button = QPushButton("删除", self)
        self.remove_button.setToolTip("从 bots_config.json 中删除该机器人（会先确认）")
        self.remove_button.clicked.connect(lambda: self._do("remove"))

        for button in (self.open_button, self.start_button, self.stop_button,
                       self.restart_button, self.edit_button, self.close_window_button,
                       self.up_button, self.down_button, self.remove_button):
            row_one.addWidget(button)
        row_one.addStretch(1)
        layout.addLayout(row_one)

        # ---- 全局操作 ----
        row_all = QHBoxLayout()
        row_all.setSpacing(6)
        self.new_button = QPushButton("新建 bot…", self)
        self.new_button.clicked.connect(lambda: self._do("new"))

        self.open_all_button = QPushButton("打开全部窗口", self)
        self.open_all_button.clicked.connect(lambda: self._do("open_all"))

        self.start_all_button = QPushButton("启动全部", self)
        self.start_all_button.setToolTip("按设定间隔依次启动配置里所有启用的机器人")
        self.start_all_button.clicked.connect(lambda: self._do("start_all"))

        self.stop_all_button = QPushButton("停止全部", self)
        self.stop_all_button.clicked.connect(lambda: self._do("stop_all"))

        self.close_all_button = QPushButton("关闭全部窗口", self)
        self.close_all_button.setToolTip("只关闭界面窗口，程序继续运行")
        self.close_all_button.clicked.connect(lambda: self._do("close_all"))

        row_all.addWidget(self.new_button)
        row_all.addSpacing(12)
        row_all.addWidget(self.open_all_button)
        row_all.addWidget(self.start_all_button)
        row_all.addWidget(self.stop_all_button)
        row_all.addWidget(self.close_all_button)
        row_all.addStretch(1)
        layout.addLayout(row_all)

        # ---- 底部：提示 + 关闭 ----
        footer = QHBoxLayout()
        self.hint_label = QLabel("", self)
        self.hint_label.setStyleSheet(theme_tokens.dialog_hint_qss(self))
        self.hint_label.setWordWrap(True)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_button = self.button_box.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText("关闭")
        self.button_box.rejected.connect(self.reject)
        self.button_box.accepted.connect(self.accept)

        footer.addWidget(self.hint_label, 1)
        footer.addWidget(self.button_box)
        layout.addLayout(footer)

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------

    def apply_theme(self) -> None:
        """主题切换时刷新本对话框（R2）。

        表格里的状态色与提示文字都是"写上去"的，不会自己跟着调色板变，
        所以这里重新套一遍：提示文字取令牌，表格整体重刷一次行颜色。
        """
        try:
            self.hint_label.setStyleSheet(theme_tokens.dialog_hint_qss(self))
        except (RuntimeError, AttributeError):
            pass
        detail = getattr(self, "detail_label", None)
        if detail is not None:
            try:
                detail.setStyleSheet(theme_tokens.dialog_hint_qss(detail))
            except (RuntimeError, AttributeError):
                pass
        # 重刷行颜色（保留选中项与滚动位置由 refresh 内部处理）
        try:
            self.refresh(keep_selection=True)
        except (RuntimeError, AttributeError):
            pass

    def refresh(self, keep_selection: bool = True) -> None:
        """重新读取状态并重建表格。"""
        selected = self.selected_bot_ids() if keep_selection else []

        rows: List[Tuple[BotStatus, List[str]]] = []
        for status in self._statuses():
            rows.append((status, row_values(status, self.sort_button.isChecked())))

        rows = filtering_rows(rows, self.filter_edit.text(), self.only_running_box.isChecked())

        sort_by_state = self.sort_button.isChecked()
        if sort_by_state:
            # 稳定排序：先按状态权重，再按名称
            rows.sort(key=lambda item: (status_sort_key(item[0]), item[0].name.lower()))

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for status, values in rows:
            self._append_row(status, values, sort_by_state)
        self.table.blockSignals(False)

        # 恢复选中
        if selected:
            for row in range(self.table.rowCount()):
                if self._bot_id_at(row) in selected:
                    self.table.selectRow(row)
        if self.table.currentRow() < 0 and self.table.rowCount() > 0:
            self.table.setCurrentCell(0, COL_NAME)

        self._on_selection_changed()

    def _statuses(self) -> List[BotStatus]:
        """取全部 bot 的状态快照；宿主不可用时返回空列表。"""
        host = self.host
        if host is None:
            return []
        try:
            return list(host.bot_status_list())
        except (RuntimeError, AttributeError):
            return []

    def _append_row(self, status: BotStatus, values: List[str], sort_by_state: bool) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        color = bot_status_color(status) or theme_tokens.COLOR_IDLE
        bold = QFont(self.table.font())
        bold.setBold(True)

        for column, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setData(ROLE_BOT_ID, status.bot_id)
            if column == COL_NAME:
                item.setFont(bold)
                if not status.enabled:
                    item.setForeground(QBrush(QColor(theme_tokens.COLOR_DISABLED)))
            elif column == COL_STATE:
                item.setText("{} {}".format(bot_status_dot(status), bot_status_text(status)))
                item.setForeground(QBrush(QColor(color)))
            elif column == COL_RUNNING:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            elif column == COL_ENABLED:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if not status.enabled:
                    item.setForeground(QBrush(QColor(theme_tokens.COLOR_FAILED)))
            elif column == COL_WINDOW:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(QBrush(QColor(
                    theme_tokens.COLOR_RUNNING if status.opened
                    else theme_tokens.COLOR_IDLE
                )))
            item.setToolTip(self._tooltip_for(status))
            self.table.setItem(row, column, item)

        self.table.setRowHeight(row, 34)
        # 保存排序键，供"按状态排序"之外的自定义排序使用
        self.table.item(row, COL_NAME).setData(ROLE_SORT, status_sort_key(status))

    def _tooltip_for(self, status: BotStatus) -> str:
        host = self.host
        bot = self._bot_of(status.bot_id)
        if host is not None and bot is not None:
            try:
                return host.bot_status_tooltip(bot)
            except (RuntimeError, AttributeError):
                pass
        return format_bot_status(status)

    # ------------------------------------------------------------------
    # 选中项
    # ------------------------------------------------------------------

    def _bot_id_at(self, row: int) -> str:
        item = self.table.item(row, COL_NAME)
        if item is None:
            return ""
        return str(item.data(ROLE_BOT_ID) or "")

    def selected_bot_ids(self) -> List[str]:
        """当前选中的 bot id（按表格顺序，去重）。"""
        result: List[str] = []
        for index in self.table.selectionModel().selectedRows() if self.table.selectionModel() else []:
            bot_id = self._bot_id_at(index.row())
            if bot_id and bot_id not in result:
                result.append(bot_id)
        return result

    def current_bot_id(self) -> str:
        """当前活动行（单选/批量操作都以此为主）。"""
        row = self.table.currentRow()
        if row < 0:
            selected = self.selected_bot_ids()
            return selected[0] if selected else ""
        return self._bot_id_at(row)

    def _targets(self) -> List[str]:
        """操作对象：有选中就用选中，没选中就作用于全部（批量语义）。"""
        selected = self.selected_bot_ids()
        if selected:
            return selected
        return [status.bot_id for status in self._statuses()]

    def _both(self) -> List[str]:
        """单选按钮的操作对象：只认选中项，没选中返回空。"""
        selected = self.selected_bot_ids()
        if selected:
            return selected
        current = self.current_bot_id()
        return [current] if current else []

    def _bot_of(self, bot_id: str) -> Optional[Bot]:
        host = self.host
        if host is None or not bot_id:
            return None
        try:
            return host.config.get_bot(bot_id)
        except (RuntimeError, AttributeError):
            return None

    # ------------------------------------------------------------------
    # 选中变化：更新按钮与详情
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        ids = self.selected_bot_ids()
        current = self.current_bot_id()
        single = bool(current) and len(ids) <= 1
        multi = len(ids) > 1

        for button in (self.open_button, self.edit_button, self.close_window_button,
                       self.up_button, self.down_button, self.remove_button):
            button.setEnabled(single)
        for button in (self.start_button, self.stop_button, self.restart_button):
            button.setEnabled(single or multi or self.table.rowCount() > 0)

        self.hint_label.setText(self._hint_text(ids, single))
        self._update_detail(ids, current)

    def _hint_text(self, ids: List[str], single: bool) -> str:
        if not self.table.rowCount():
            if self._statuses():
                return "当前过滤条件下没有匹配的机器人。"
            return "配置里还没有任何机器人，点「新建 bot…」创建。"
        if single:
            return ("已选中 1 个机器人。启动 / 停止 / 重启可直接点击；"
                    "双击一行等于打开窗口（与主窗口左栏的单击一致）。")
        if ids:
            return "已选中 {} 个机器人：启动 / 停止 / 重启会作用于这 {} 个。".format(
                len(ids), len(ids)
            )
        return "未选中任何行：启动 / 停止 / 重启将作用于列出的全部机器人（{} 个）。".format(
            self.table.rowCount()
        )

    def _update_detail(self, ids: List[str], current: str) -> None:
        target = current or (ids[0] if ids else "")
        bot = self._bot_of(target)
        host = self.host
        if bot is None or host is None:
            if self.table.rowCount() and not target:
                lines = ["（未选中具体机器人，显示全部概览）", ""]
                for status in self._statuses():
                    lines.append("{}  {}".format(
                        bot_status_dot(status), format_bot_status(status, with_opened=True)
                    ))
                    lines.append("    " + programs_summary(status, use_newline=False))
                    lines.append("")
                self.detail_view.setPlainText("\n".join(lines).rstrip())
            else:
                self.detail_view.setPlainText("")
            return
        try:
            self.detail_view.setPlainText(host.bot_status_tooltip(bot))
        except (RuntimeError, AttributeError):
            self.detail_view.setPlainText("")

    # ------------------------------------------------------------------
    # 动作分发
    # ------------------------------------------------------------------

    def _on_live_toggled(self, checked: bool) -> None:
        self._auto_refresh_enabled = bool(checked)
        timer = getattr(self, "_timer", None)
        if timer is None:
            if checked:
                self._timer = QTimer(self)
                self._timer.setInterval(AUTO_REFRESH_MS)
                self._timer.timeout.connect(self._on_auto_refresh)
                self._timer.start()
            return
        if checked:
            timer.start()
        else:
            timer.stop()

    def _on_auto_refresh(self) -> None:
        """定时刷新：只重建表格数据，保持选中与滚动位置。"""
        if not self.isVisible():
            return
        self.refresh(keep_selection=True)

    def _do(self, action: str) -> None:
        """执行一个动作：优先交给宿主，宿主不可用时退回发信号。"""
        bot_ids = self._targets() if action in ("start", "stop", "restart") else self._both()
        current = self.current_bot_id()

        handler = self._builtin_handler(action)
        if handler is not None:
            try:
                handler(bot_ids, current)
            except Exception as exc:  # 宿主内部出错不应让对话框崩掉
                QMessageBox.warning(self, "操作失败", "执行「{}」时出错：{}".format(action, exc))
            self.refresh(keep_selection=True)
            return

        # 没有宿主：交给外部（信号），由调用方处理
        if action in ("new", "open_all", "start_all", "stop_all", "close_all"):
            self.actionRequested.emit(action, None)
        elif bot_ids:
            for bot_id in bot_ids:
                self.actionRequested.emit(action, bot_id)
        elif current:
            self.actionRequested.emit(action, current)
        self.refresh(keep_selection=True)

    def _builtin_handler(self, action: str):
        """把动作名映射到宿主方法；宿主不支持时返回 None。"""
        host = self.host
        if host is None:
            return None
        mapping = {
            "open": ("open_bot_tab", True),
            "start": ("start_bot", False),
            "stop": ("stop_bot", False),
            "restart": ("restart_bot", False),
            "edit": ("edit_bot", False),
            "close_window": ("close_bot_window", False),
            "up": ("move_bot_up", False),
            "down": ("move_bot_down", False),
            "remove": ("remove_bot", False),
        }
        if action in mapping:
            name, _single = mapping[action]
            method = getattr(host, name, None)
            if not callable(method):
                return None

            def _call(bot_ids: List[str], current: str, _method=method, _action=action) -> None:
                targets = bot_ids or ([current] if current else [])
                if not targets:
                    QMessageBox.information(self, "没有选中项", "请先在列表里选中一个机器人。")
                    return
                for bot_id in targets:
                    if _action == "open":
                        _method(bot_id, True)
                    else:
                        _method(bot_id)

            return _call

        simple = {
            "new": "new_bot",
            "open_all": "open_all_windows",
            "start_all": "start_all_bots",
            "stop_all": "stop_all_bots",
            "close_all": "close_all_windows",
        }
        if action in simple:
            method = getattr(host, simple[action], None)
            if not callable(method):
                return None

            def _call_all(_bot_ids: List[str], _current: str, _method=method) -> None:
                _method()

            return _call_all
        return None

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row >= 0:
            self.table.selectRow(row)
            self.table.setCurrentCell(row, COL_NAME)
        bot_id = self.current_bot_id()
        bot = self._bot_of(bot_id)

        menu = QMenu(self)
        name = bot.name if bot is not None else "（未选中）"
        header = QAction("机器人：{}".format(name), menu)
        header.setEnabled(False)
        menu.addAction(header)
        menu.addSeparator()

        for text, action, enabled in (
            ("打开窗口", "open", bool(bot_id)),
            ("启动", "start", bool(bot_id)),
            ("停止", "stop", bool(bot_id)),
            ("重启", "restart", bool(bot_id)),
            ("编辑配置…", "edit", bool(bot_id)),
            ("关闭窗口（不停程序）", "close_window", bool(bot_id)),
            ("删除…", "remove", bool(bot_id)),
            ("上移", "up", bool(bot_id)),
            ("下移", "down", bool(bot_id)),
        ):
            item = QAction(text, menu)
            item.setEnabled(enabled)
            item.triggered.connect(lambda _checked=False, a=action: self._do(a))
            menu.addAction(item)

        menu.addSeparator()
        refresh_item = QAction("刷新列表", menu)
        refresh_item.triggered.connect(lambda: self.refresh())
        menu.addAction(refresh_item)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # 快捷操作（供外部直接调用）
    # ------------------------------------------------------------------

    def select_bot(self, bot_id: str) -> bool:
        """选中指定机器人所在行。"""
        for row in range(self.table.rowCount()):
            if self._bot_id_at(row) == bot_id:
                self.table.selectRow(row)
                self.table.setCurrentCell(row, COL_NAME)
                return True
        return False

    def visible_bot_ids(self) -> List[str]:
        """当前表格里显示的 bot id 顺序。"""
        return [self._bot_id_at(row) for row in range(self.table.rowCount())]


# ---------------------------------------------------------------------------
# 自检：可用 QT_QPA_PLATFORM=offscreen 无界面运行
# ---------------------------------------------------------------------------

class _FakeHost:
    """自检用的假宿主：只实现本对话框需要的方法。"""

    def __init__(self) -> None:
        from app.config import BotConfig, Program, new_id
        from app.process_manager import ProcessManager

        self.config = BotConfig.from_dict({"version": 1, "bots": []}, path=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_selftest_bots_config.json")
        )
        self.manager = ProcessManager()
        self.calls: List[Tuple[str, object]] = []

        bot = Bot(id="bot_a", name="ATRI", qq="1")
        bot.add_program(Program(id="p1", name="主程序", role=ROLE_PRIMARY,
                                cwd="", command="echo hi"))
        bot.add_program(Program(id="p2", name="ollama", role="secondary",
                                cwd="", command="echo hi"))
        bot.add_program(Program(id="p3", name="gptsovits", role="secondary",
                                cwd="", command="echo hi"))
        self.config.add_bot(bot)

        bot2 = Bot(id="bot_b", name="OsuAtri", qq="2")
        bot2.add_program(Program(id="p9", name="OsuAtri 主程序", role=ROLE_PRIMARY,
                                 cwd="", command="echo hi"))
        self.config.add_bot(bot2)

        self._statuses = [self._make_status("bot_a", "ATRI", 3, running=2, opened=False),
                          self._make_status("bot_b", "OsuAtri", 1, running=0, opened=True)]

    @staticmethod
    def _make_status(bot_id, name, total, running, opened):
        from app.ui.main_window import BotStatus

        status = BotStatus(bot_id=bot_id, name=name, enabled=True, total=total,
                           enabled_total=total, running=running, opened=opened)
        for index in range(total):
            key = "k{}_{}".format(bot_id, index)
            status.roles[key] = ROLE_PRIMARY if index == 0 else "secondary"
            status.program_names[key] = ["主程序", "ollama", "gptsovits"][index % 3]
            status.program_states[key] = (
                ProcessManager.STATE_RUNNING if index < running else ProcessManager.STATE_STOPPED
            )
        return status

    # ---- 宿主接口 ----

    def bot_status_list(self):
        return list(self._statuses)

    def bot_status_tooltip(self, bot):
        return "{} 的明细（自检用假数据）\n程序 {} 个".format(bot.name, len(bot.programs))

    def open_bot_tab(self, bot_id, focus=True):
        self.calls.append(("open", bot_id))
        return None

    def start_bot(self, bot_id):
        self.calls.append(("start", bot_id))
        return True

    def stop_bot(self, bot_id):
        self.calls.append(("stop", bot_id))
        return True

    def restart_bot(self, bot_id):
        self.calls.append(("restart", bot_id))
        return True

    def edit_bot(self, bot_id):
        self.calls.append(("edit", bot_id))

    def close_bot_window(self, bot_id, confirm=True):
        self.calls.append(("close_window", bot_id))
        return True

    def move_bot_up(self, bot_id):
        self.calls.append(("up", bot_id))
        return True

    def move_bot_down(self, bot_id):
        self.calls.append(("down", bot_id))
        return True

    def remove_bot(self, bot_id, ask=True, stop_running=True):
        self.calls.append(("remove", bot_id))
        return True

    def new_bot(self):
        self.calls.append(("new", None))

    def open_all_windows(self):
        self.calls.append(("open_all", None))

    def start_all_bots(self):
        self.calls.append(("start_all", None))
        return 2

    def stop_all_bots(self):
        self.calls.append(("stop_all", None))
        return 2

    def close_all_windows(self):
        self.calls.append(("close_all", None))


def _selftest() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    host = _FakeHost()
    dialog = BotListDialog(host, auto_refresh=False)
    dialog.show()

    print("[1] 表格行数 =", dialog.table.rowCount(), "| 列数 =", dialog.table.columnCount())
    assert dialog.table.rowCount() == 2
    assert dialog.table.columnCount() == len(COLUMN_TITLES)

    print("[2] 第一行内容 =", [dialog.table.item(0, c).text().replace("\n", " / ")
                                for c in range(dialog.table.columnCount())])
    assert dialog.table.item(0, COL_NAME).text() == "ATRI"
    assert "部分运行" in dialog.table.item(0, COL_STATE).text()
    assert dialog.table.item(0, COL_RUNNING).text() == "2/3"
    assert dialog.table.item(0, COL_WINDOW).text() == "已关闭"
    assert "★主程序" in dialog.table.item(0, COL_PROGRAMS).text()

    print("[3] 过滤：只看有进程的")
    dialog.only_running_box.setChecked(True)
    print("    可见 =", dialog.visible_bot_ids())
    assert dialog.visible_bot_ids() == ["bot_a"]
    dialog.only_running_box.setChecked(False)

    print("[4] 过滤：关键字")
    dialog.filter_edit.setText("osuatri")
    print("    可见 =", dialog.visible_bot_ids())
    assert dialog.visible_bot_ids() == ["bot_b"]
    dialog.filter_edit.setText("ollama")
    print("    可见 =", dialog.visible_bot_ids())
    assert dialog.visible_bot_ids() == ["bot_a"]
    dialog.filter_edit.setText("")

    print("[5] 按状态排序")
    dialog.sort_button.setChecked(True)
    print("    可见顺序 =", dialog.visible_bot_ids())
    assert dialog.visible_bot_ids() == ["bot_a", "bot_b"], dialog.visible_bot_ids()
    dialog.sort_button.setChecked(False)

    print("[6] 单选动作走宿主")
    dialog.select_bot("bot_b")
    assert dialog.current_bot_id() == "bot_b"
    dialog._do("start")
    dialog._do("stop")
    dialog._do("restart")
    dialog._do("edit")
    dialog._do("open")
    dialog._do("close_window")
    dialog._do("up")
    dialog._do("remove")
    print("    宿主调用 =", [item for item in host.calls if item[1] == "bot_b"])
    for action in ("start", "stop", "restart", "edit", "open", "close_window",
                   "up", "remove"):
        assert (action, "bot_b") in host.calls, action

    print("[7] 批量动作（无选中时作用于全部）")
    host.calls.clear()
    dialog.table.clearSelection()
    dialog.table.setCurrentCell(-1, -1)
    dialog._do("stop")
    print("    宿主调用 =", host.calls)
    assert ("stop", "bot_a") in host.calls and ("stop", "bot_b") in host.calls

    print("[8] 全局动作")
    host.calls.clear()
    dialog._do("new")
    dialog._do("open_all")
    dialog._do("start_all")
    dialog._do("stop_all")
    dialog._do("close_all")
    names = [item[0] for item in host.calls]
    print("    宿主调用 =", names)
    assert names == ["new", "open_all", "start_all", "stop_all", "close_all"], names

    print("[9] 纯函数")
    status = host._statuses[0]
    print("    sort_key =", status_sort_key(status), "| summary =",
          programs_summary(status, use_newline=False))
    assert status_sort_key(status) == 1  # partial
    assert "★主程序(运行中)" in programs_summary(status, use_newline=False)
    rows = [(status, row_values(status))]
    assert filtering_rows(rows, "", False) == rows
    assert filtering_rows(rows, "ATRI", False) == rows
    assert filtering_rows(rows, "不存在", False) == []
    assert filtering_rows(rows, "", True) == rows
    assert row_values(status)[COL_ENABLED] == "是"

    print("[10] 详情与提示")
    dialog.select_bot("bot_a")
    detail = dialog.detail_view.toPlainText()
    print("    详情 =", repr(detail[:60]))
    assert "明细" in detail
    print("    提示 =", dialog.hint_label.text())
    assert "已选中 1 个" in dialog.hint_label.text()

    print("\n自检通过：表格渲染、过滤、排序、单选/批量动作分发、纯函数均正常。")
    dialog.close()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
