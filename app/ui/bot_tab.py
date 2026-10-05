# -*- coding: utf-8 -*-
"""Bot 标签页：一个机器人对应右侧一整块实例区，按"布局树"渲染成若干窗格。

布局（步骤 2 起）
----------------
右侧区域由一棵**布局树**（app.layout.PaneNode）递归生成：

    split（分割）  -> 一个 QSplitter（orientation = h 左右 / v 上下）
    pane（窗格）   -> 一个 PaneWidget
                      · 1 个程序：该程序的日志视图独占整格
                      · N 个程序：QTabWidget（标签在底部）承载 N 个日志视图

每个窗格（PaneWidget）自带标题栏与操作按钮：
    程序名（角色） · 状态 · PID     [启动] [停止] [重启] [清空日志]
因此"每个程序的 cmd / 终端实时内容"都是**独立一格**，日志按 manager key 分发，互不串台。

默认布局（用户没设置过时）与改造前完全一致：
    1 个程序 -> 独占一格
    2 个程序 -> 上下两格
    ≥3 个程序 -> 主程序占上半格，其余程序在下半格的标签页里

对外接口（主窗口用）
--------------------
    all_keys()                 所有程序的 manager key（主程序在前）
    log_view(key)              某个程序的日志视图
    append_log(key, text)      写日志（视图还没建时先缓冲）
    refresh_statuses()         刷新窗格标题与状态点
    apply_bot(bot)             配置变更后重建
    set_layout(kind)           切换模板：v / h / h_left_v / h_right_v / tabs / single
    apply_layout_tree(node)    直接套一棵布局树（custom）
    layout_tree() / layout_kind() / layout_description()
    pane_sizes() / set_pane_sizes(dict)   各分割节点比例（QSettings 用）
    focus_program(key) / focused_program()   焦点（左栏单击跳转用）
    pane_count() / pane_handles() / pane_paths()
    build_layout_menu()        布局菜单（控制条按钮与左栏右键共用）

信号
----
    startRequested(list) / stopRequested(list) / restartRequested(list)
        —— 作用于一组程序（控制条"全部"按钮、右键菜单）
    programActionRequested(str, str)
        —— (manager key, action)，action 为 start / stop / restart，来自单个窗格按钮
    editRequested(str) / closeRequested(str) / openDirectoryRequested(str)
    layoutChanged(str)     —— 布局或比例发生变化（主窗口据此写 QSettings）
    focusChanged(str)      —— 焦点程序变化（manager key）
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

# 允许 "python app/ui/bot_tab.py" 直接运行自检
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import layout as layout_model  # noqa: E402
from app.config import (  # noqa: E402
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    Bot,
    BotConfig,
    Program,
    new_id,
    role_display,
)
from app.layout import PaneNode  # noqa: E402
from app.process_manager import (  # noqa: E402
    DEFAULT_STOP_TIMEOUT_MS,
    ProcessManager,
    build_manager_key,
)
from app.ui.program_widget import ProgramWidget, muted_text_color  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 默认分割比例（主程序 : 其余）
DEFAULT_SPLIT_RATIO = (65, 35)

#: 分隔条拖动结束后多久算"停下来了"（毫秒）
SPLIT_SETTLE_MS = 300

#: 状态文字与颜色（颜色统一取自 app.ui.theme 的状态色表，R2）
STATE_COLORS = theme_tokens.status_colors()

STATE_TEXTS = {
    ProcessManager.STATE_IDLE: "未启动",
    ProcessManager.STATE_STARTING: "启动中…",
    ProcessManager.STATE_RUNNING: "运行中",
    ProcessManager.STATE_STOPPING: "停止中…",
    ProcessManager.STATE_STOPPED: "已停止",
    ProcessManager.STATE_FAILED: "异常",
}

#: 布局模板在菜单里的展示顺序
LAYOUT_MENU_ORDER = (
    layout_model.KIND_V,
    layout_model.KIND_H,
    layout_model.KIND_H_LEFT_V,
    layout_model.KIND_H_RIGHT_V,
    layout_model.KIND_TABS,
    layout_model.KIND_SINGLE,
)


def node_paths(root: Optional[PaneNode]) -> Dict[int, str]:
    """给布局树的每个节点生成稳定路径：根为 "h0"，子节点为 "h0/v1" 等。

    路径只取决于"结构 + 方向 + 序号"，同一棵树每次重建得到的路径一致，
    因此可以直接作为 QSettings 的键（pane/sizes/<bot_id>/<path>）。
    """
    paths: Dict[int, str] = {}
    if root is None:
        return paths

    def walk(node: PaneNode, path: str) -> None:
        paths[id(node)] = path
        if not node.is_split:
            return
        for index, child in enumerate(node.children):
            walk(child, "{}/{}{}".format(path, child.orientation, index))

    walk(root, "{}{}".format(root.orientation, 0))
    return paths


def collect_pane_widgets(host: Optional[QWidget]) -> List["PaneWidget"]:
    """递归收集容器里的所有 PaneWidget（按界面顺序）。"""
    if host is None:
        return []
    if isinstance(host, PaneWidget):
        return [host]
    result: List[PaneWidget] = []
    for child in host.findChildren(
        PaneWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
    ):
        result.append(child)
    for child in host.findChildren(
        QSplitter, options=Qt.FindChildOption.FindDirectChildrenOnly
    ):
        result.extend(collect_pane_widgets(child))
    return result


def collect_splitter_orientations(host: Optional[QWidget]) -> List[str]:
    """按层级读出所有 splitter 的方向（"h" / "v"），供自检与调试使用。"""
    names: List[str] = []

    def walk(widget: QWidget) -> None:
        for splitter in widget.findChildren(
            QSplitter, options=Qt.FindChildOption.FindDirectChildrenOnly
        ):
            names.append(
                "h" if splitter.orientation() == Qt.Orientation.Horizontal else "v"
            )
            walk(splitter)

    if isinstance(host, QSplitter):
        names.append("h" if host.orientation() == Qt.Orientation.Horizontal else "v")
    if host is not None:
        walk(host)
    return names


# ---------------------------------------------------------------------------
# 带右键菜单的日志视图
# ---------------------------------------------------------------------------

class BotLogView(ProgramWidget):
    """ProgramWidget 的薄封装：把右键菜单请求转交给所属的 BotTab。"""

    contextMenuRequested = pyqtSignal(str)

    def __init__(
        self,
        program_key: str,
        program_name: str,
        role_text: str = "",
        working_dir: str = "",
        parent: Optional[QWidget] = None,
        max_lines: int = 5000,
        show_toolbar: bool = True,
    ) -> None:
        super().__init__(
            program_name=program_name,
            role_text=role_text,
            working_dir=working_dir,
            parent=parent,
            max_lines=max_lines,
            show_toolbar=show_toolbar,
        )
        self.program_key = program_key
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos) -> None:
        global_pos = self.mapToGlobal(pos)
        self.contextMenuRequested.emit(self.program_key)
        # 由 BotTab 负责弹出菜单（保持动作定义集中在一处）
        parent = self.parent()
        while parent is not None and not isinstance(parent, BotTab):
            parent = parent.parent()
        if isinstance(parent, BotTab):
            parent.show_context_menu(global_pos, key=self.program_key)


# ---------------------------------------------------------------------------
# 窗格：一个程序（或若干程序的标签页）+ 标题栏 + 操作按钮
# ---------------------------------------------------------------------------

def _note_slot_error(where: str, exc: BaseException) -> None:
    """槽里出错时留一行提示（不弹窗、不打断）。

    为什么要有它：拖动分隔条这类高频槽必须兜底（异常冒出去 = PyQt6 终止进程），
    但"悄悄吞掉"会让真因永远查不到。所以退化处理的同时往 stderr 写一行 ——
    用 `python main.py --console` 或管理器自带的控制台就能看到。
    """
    try:
        sys.stderr.write("[界面] {} 出错（已忽略，界面继续）：{}: {}\n".format(
            where, type(exc).__name__, exc))
        sys.stderr.flush()
    except (AttributeError, ValueError, OSError):
        pass


class PaneWidget(QWidget):
    """一个窗格。

    - 单个程序：直接放一个 BotLogView
    - 多个程序：QTabWidget（标签在底部）承载多个 BotLogView，标签文字只显示程序名
    - 标题栏显示"当前程序名（角色）  状态  PID"，右侧是 启动/停止/重启/清空日志
    """

    #: 请求对某个程序执行动作：(manager key, action)
    programAction = pyqtSignal(str, str)
    #: 用户点击窗格（日志区/空白处）：参数为当前 manager key
    activated = pyqtSignal(str)
    #: 请求打开某个目录（转发）
    openDirectoryRequested = pyqtSignal(str)

    def __init__(
        self,
        node: PaneNode,
        bot_id: str,
        programs: Dict[str, Program],
        program_keys: Dict[str, str],
        working_dirs: Dict[str, str],
        parent: Optional[QWidget] = None,
        max_lines: int = 5000,
    ) -> None:
        super().__init__(parent)

        self.node: PaneNode = node
        self.path: str = ""
        self.focused: bool = False
        self._bot_id: str = str(bot_id or "")
        self._max_lines: int = max(50, int(max_lines))
        self._programs: Dict[str, Program] = programs
        self._program_keys: Dict[str, str] = program_keys
        self._working_dirs: Dict[str, str] = working_dirs
        self._views: Dict[str, BotLogView] = {}
        self._tabs: Optional[QTabWidget] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addWidget(self._build_title_bar())

        if len(node.programs) > 1:
            self._build_tabs(outer)
        else:
            program_id = node.current or (node.programs[0] if node.programs else "")
            if program_id:
                view = self._create_view(program_id, self)
                outer.addWidget(view, 1)

        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._apply_pane_style()
        self.refresh_title()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _build_title_bar(self) -> QWidget:
        """标题栏：程序名（角色）+ 状态 + PID，右侧四个小按钮。"""
        bar = QWidget(self)
        bar.setObjectName("paneTitleBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)
        # 记住布局：_adopt_count_label 要把日志视图的"行数"标签插进来
        self._title_bar_layout = row

        self.title_label = QLabel("（空窗格）", bar)
        self.title_label.setObjectName("paneTitle")
        font = QFont(bar.font())
        font.setBold(True)
        self.title_label.setFont(font)

        self.status_label = QLabel("", bar)
        self.status_label.setObjectName("paneStatus")

        self.pid_label = QLabel("", bar)
        self.pid_label.setObjectName("panePid")

        # 行数标签：由当前显示的程序窗格"搬"进来（见 _adopt_count_label），
        # 这样窗格标题栏就是**唯一**的一行（真机反馈过"两栏功能重复"）。
        self.count_label = QLabel("", bar)
        self.count_label.setObjectName("paneCount")
        self.count_label.setToolTip("当前日志行数")

        self.start_button = QPushButton("启动", bar)
        self.start_button.setToolTip(
            "只启动本窗格**当前显示**的这个程序\n"
            "（要启停全部程序：用窗格空白处右键菜单，或顶部「启动全部」）"
        )
        self.start_button.clicked.connect(lambda: self._emit_action("start"))

        self.stop_button = QPushButton("停止", bar)
        self.stop_button.setToolTip(
            "只停止本窗格**当前显示**的这个程序（含进程树）\n"
            "（要启停全部程序：用窗格空白处右键菜单，或顶部「停止全部」）"
        )
        self.stop_button.clicked.connect(lambda: self._emit_action("stop"))

        self.restart_button = QPushButton("重启", bar)
        self.restart_button.setToolTip(
            "只重启本窗格**当前显示**的这个程序\n"
            "（要启停全部程序：用窗格空白处右键菜单，或顶部「重启全部」）"
        )
        self.restart_button.clicked.connect(lambda: self._emit_action("restart"))

        self.clear_button = QPushButton("清空日志", bar)
        self.clear_button.setToolTip("清空该窗格里的日志显示")
        self.clear_button.clicked.connect(self.clear_log)

        row.addWidget(self.title_label)
        row.addWidget(self.status_label)
        row.addWidget(self.pid_label)
        row.addWidget(self.count_label)
        row.addStretch(1)
        for button in (self.start_button, self.stop_button, self.restart_button,
                       self.clear_button):
            button.setMaximumHeight(22)
            row.addWidget(button)
        return bar

    def _build_tabs(self, outer: QVBoxLayout) -> None:
        """多程序：底部标签页承载各自的日志视图。"""
        tabs = QTabWidget(self)
        tabs.setObjectName("paneTabs")
        tabs.setTabPosition(QTabWidget.TabPosition.South)
        tabs.setDocumentMode(True)
        tabs.setMovable(False)
        tabs.setUsesScrollButtons(True)
        for program_id in self.node.programs:
            view = self._create_view(program_id, tabs)
            tabs.addTab(view, self._short_label(program_id))
        tabs.currentChanged.connect(lambda _index: self._on_tab_changed())
        self._tabs = tabs
        outer.addWidget(tabs, 1)

        index = self._index_of(self.node.current or self.node.programs[0])
        if index >= 0:
            tabs.blockSignals(True)
            tabs.setCurrentIndex(index)
            tabs.blockSignals(False)

    def _create_view(self, program_id: str, parent: QWidget) -> BotLogView:
        """为一个程序建立日志视图。"""
        program = self._programs.get(self._key_of(program_id))
        key = self._key_of(program_id)
        view = BotLogView(
            program_key=key,
            program_name=program.name if program is not None else program_id,
            role_text=role_display(program.role) if program is not None else "",
            working_dir=self._working_dirs.get(program_id, ""),
            parent=parent,
            max_lines=self._max_lines,
            show_toolbar=False,
        )
        view.contextMenuRequested.connect(self._on_view_context_menu)
        view.openDirectoryRequested.connect(self.openDirectoryRequested.emit)
        # 把程序窗格的"行数"标签并进本窗格标题栏（唯一一行）
        self._adopt_count_label(view)
        # 点击日志区 = 把焦点切到本窗格
        view.editor.installEventFilter(self)
        self._views[key] = view
        return view

    def _adopt_count_label(self, view) -> None:
        """把某个日志视图的"行数"标签搬进本窗格标题栏。

        真机反馈："图片中框住的这两栏功能上是有重复的，建议合并" ——
        窗格标题栏已有 程序名/状态/PID/启停按钮，只缺行数；而日志视图自己也有一行
        （程序名 + 状态 + 行数）。合并方式：

          · 日志视图的那一行在窗格里只剩"行数"（程序名/状态已隐藏，见 ProgramWidget）；
          · 行数标签**搬进窗格标题栏**，于是屏幕上只剩一行。

        标签对象仍归日志视图所有（``view.count_label`` 照旧可用、照旧被
        ``_update_count_label()`` 更新），只是换了父控件。
        """
        try:
            label = view.take_count_label()
        except (AttributeError, RuntimeError):
            label = None
        if label is None:
            return
        layout = getattr(self, "_title_bar_layout", None)
        if layout is not None:
            # 插到占位标签原来的位置，再把占位标签摘掉（避免布局里留个空项）
            index = layout.indexOf(self.count_label)
            if index >= 0:
                layout.insertWidget(index, label)
                layout.removeWidget(self.count_label)
                self.count_label.setParent(None)
        self.count_label = label
        label.show()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt 命名)
        """点击日志编辑器时把焦点切到本窗格。"""
        try:
            if event.type() == QEvent.Type.MouseButtonPress:
                self.activated.emit(self.current_key())
        except (AttributeError, RuntimeError):
            return False
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """点击窗格空白处也算聚焦。"""
        self.activated.emit(self.current_key())
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    # 基本信息
    # ------------------------------------------------------------------

    def keys(self) -> List[str]:
        """本窗格内所有程序的 manager key（按标签顺序）。"""
        return [self._key_of(program_id) for program_id in self.node.programs]

    def program_ids(self) -> List[str]:
        """本窗格内所有程序的 id。"""
        return list(self.node.programs)

    def has_tabs(self) -> bool:
        """是否是多程序子标签窗格。"""
        return self._tabs is not None and self._tabs.count() > 1

    def view(self, key: str) -> Optional[BotLogView]:
        """按 key 取本窗格内的日志视图。"""
        return self._views.get(key)

    def views(self) -> List[BotLogView]:
        """本窗格内所有日志视图。"""
        return list(self._views.values())

    def contains(self, key: str) -> bool:
        """本窗格是否包含该 manager key。"""
        return key in self._views

    def current_program_id(self) -> str:
        """当前显示的程序 id（多标签时取当前标签）。"""
        if self._tabs is not None:
            index = self._tabs.currentIndex()
            if 0 <= index < len(self.node.programs):
                return self.node.programs[index]
        return self.node.current or (self.node.programs[0] if self.node.programs else "")

    def current_key(self) -> str:
        """当前显示程序的 manager key。"""
        return self._key_of(self.current_program_id())

    def title_for(self, program_id: str) -> str:
        """标题文字：程序名（角色）——空窗格给个占位。"""
        program = self._programs.get(self._key_of(program_id)) if program_id else None
        if program is None:
            return "（空窗格）"
        return "{}（{}）".format(program.name or program_id, role_display(program.role))

    def _short_label(self, program_id: str) -> str:
        """标签页文字（只放程序名，避免标签过长）。"""
        program = self._programs.get(self._key_of(program_id))
        name = program.name if program is not None else program_id
        return "★ {}".format(name) if self._is_primary(program_id) else name

    def _is_primary(self, program_id: str) -> bool:
        program = self._programs.get(self._key_of(program_id))
        return bool(program is not None and program.role == ROLE_PRIMARY)

    def _key_of(self, program_id: str) -> str:
        """程序 id -> manager key（优先查映射，找不到时按约定拼一个）。"""
        key = self._program_keys.get(program_id)
        if key:
            return key
        return build_manager_key(self._bot_id, program_id)

    def _index_of(self, program_id: str) -> int:
        try:
            return self.node.programs.index(program_id)
        except ValueError:
            return -1

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def _emit_action(self, action: str) -> None:
        """把**当前显示的那个程序**的 启动/停止/重启 请求交给主窗口。

        真机事故（很危险）：这里原来写的是 ``for key in self.keys()`` ——
        把窗格内**所有**程序都启停了一遍。于是：

            窗格标题写着「Ollama 服务（副程序）」，点它的「停止」
            → 整个 ATRI 实例下的所有程序一起被停掉

        用户实测反馈就是这个现象。窗格的按钮语义必须与标题一致：
        标题显示哪个程序，按钮就只作用在哪个程序上（"启动该窗格里的程序"这个
        tooltip 指的是"窗格里正在看的那个程序"）。

        多程序窗格要操作全部时，用窗格标题栏右键菜单里的「启动/停止/重启 全部」
        （见 ``_on_pane_context_menu``），那是显式的整体操作。
        """
        key = self.current_key()
        if not key:
            return
        self.programAction.emit(key, action)

    def _on_tab_changed(self) -> None:
        """标签切换：同步 node.current、刷新标题、上报焦点。"""
        program_id = self.current_program_id()
        if program_id:
            self.node.current = program_id
        self.refresh_title()
        self.activated.emit(self.current_key())

    def _on_view_context_menu(self, _key: str) -> None:
        """日志区右键：交给 BotTab 统一弹菜单。"""
        return

    def select_program(self, program_id: str) -> bool:
        """切到指定程序的标签（用于左栏单击跳转）。"""
        index = self._index_of(program_id)
        if index < 0:
            return False
        if self._tabs is not None:
            self._tabs.blockSignals(True)
            self._tabs.setCurrentIndex(index)
            self._tabs.blockSignals(False)
        self.node.current = program_id
        self.refresh_title()
        return True

    # ------------------------------------------------------------------
    # 状态与主题
    # ------------------------------------------------------------------

    def refresh_title(self, states: Optional[Dict[str, str]] = None) -> None:
        """刷新标题、状态文字与 PID（states 为 key -> 状态，另含 key#pid）。"""
        program_id = self.current_program_id()
        title = self.title_for(program_id)
        if self.title_label.text() != title:
            self.title_label.setText(title)
        tooltip = [title]
        if len(self.node.programs) > 1:
            tooltip.append("本窗格含 {} 个程序，用底部标签切换".format(len(self.node.programs)))
        self.title_label.setToolTip("\n".join(tooltip))

        state_map = states or {}
        key = self.current_key()
        state = state_map.get(key, ProcessManager.STATE_STOPPED)
        color = STATE_COLORS.get(state, theme_tokens.COLOR_IDLE)
        text = STATE_TEXTS.get(state, state)
        self.status_label.setText("● {}".format(text))
        self.status_label.setStyleSheet("color: {};".format(color))

        pid = state_map.get("{}#pid".format(key), "")
        self.pid_label.setText("PID {}".format(pid) if pid else "")
        self.pid_label.setStyleSheet("color: {};".format(muted_text_color(self)))

        # 多程序时顺带刷新标签文字（状态点只在标题栏显示，标签保持干净）
        if self._tabs is not None:
            for index, item in enumerate(self.node.programs):
                text_item = self._short_label(item)
                if self._tabs.tabText(index) != text_item:
                    self._tabs.setTabText(index, text_item)

    def set_status_text(self, text: str, color: str = "") -> None:
        """直接设置状态文字（供 BotTab 汇总用）。"""
        self.status_label.setText("● {}".format(text) if text else "")
        if color:
            self.status_label.setStyleSheet("color: {};".format(color))

    def clear_log(self) -> None:
        """清空本窗格内所有日志视图。"""
        for view in self._views.values():
            view.clear_log()

    def append_log(self, key: str, text: object) -> bool:
        """把日志写进对应视图；不属于本窗格时返回 False。"""
        view = self._views.get(key)
        if view is None:
            return False
        view.append_log(text)
        return True

    def apply_theme(self) -> None:
        """主题变化：刷新本窗格内所有日志视图与焦点边框。"""
        for view in self._views.values():
            try:
                view.apply_theme()
            except (RuntimeError, AttributeError):
                continue
        self.set_focused(self.focused)

    def set_focused(self, focused: bool) -> None:
        """设置焦点高亮（QSS 依据 focused 动态属性着色）。"""
        self.focused = bool(focused)
        self.setProperty("focused", self.focused)
        self._apply_pane_style()
        try:
            self.update()
        except RuntimeError:
            pass

    def _apply_pane_style(self) -> None:
        """套用窗格样式（标题栏底色 / 分隔线 / 焦点边框，颜色来自 app.ui.theme）。"""
        try:
            self.setStyleSheet(theme_tokens.pane_qss(self))
        except (RuntimeError, AttributeError):
            pass


# ---------------------------------------------------------------------------
# 窗格句柄（只读快照，便于主窗口/测试断言）
# ---------------------------------------------------------------------------

@dataclass
class PaneHandle:
    """一个窗格的公开信息。"""

    path: str = ""
    programs: List[str] = field(default_factory=list)
    keys: List[str] = field(default_factory=list)
    current: str = ""
    widget: Optional[PaneWidget] = None

    @property
    def program_count(self) -> int:
        return len(self.programs)

    @property
    def has_tabs(self) -> bool:
        return self.program_count > 1


# ---------------------------------------------------------------------------
# Bot 标签页
# ---------------------------------------------------------------------------

class BotTab(QWidget):
    """一个机器人的实例区：按布局树渲染窗格，集中显示所有程序的 cmd / 终端输出。"""

    startRequested = pyqtSignal(list)
    stopRequested = pyqtSignal(list)
    restartRequested = pyqtSignal(list)
    programActionRequested = pyqtSignal(str, str)
    editRequested = pyqtSignal(str)
    closeRequested = pyqtSignal(str)
    openDirectoryRequested = pyqtSignal(str)
    #: 布局发生变化（参数为模板名或 "custom"）
    layoutChanged = pyqtSignal(str)
    #: 焦点程序变化（参数为 manager key）
    focusChanged = pyqtSignal(str)

    def __init__(
        self,
        bot: Bot,
        config: Optional[BotConfig] = None,
        process_manager: Optional[ProcessManager] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self.bot: Bot = bot
        self.config: Optional[BotConfig] = config
        self.manager: Optional[ProcessManager] = process_manager

        self._max_lines = int(config.log_max_lines) if config is not None else 5000
        #: key -> BotLogView
        self._views: Dict[str, BotLogView] = {}
        #: 界面构建顺序（主程序在前的 key 列表）
        self._order: List[str] = []
        #: manager key -> Program 快照
        self._programs: Dict[str, Program] = {}
        #: manager key -> 程序 id
        self._key_to_program: Dict[str, str] = {}
        #: 程序 id -> manager key
        self._program_to_key: Dict[str, str] = {}
        #: 程序 id -> 解析后的工作目录
        self._working_dirs: Dict[str, str] = {}
        #: 视图创建前到达的日志，先缓冲再补写
        self._pending_logs: Dict[str, List[str]] = {}
        #: 是否显示管理器自身日志（[管理器] …）；由运行参数里的开关通过
        #: set_show_manager_log() 设置 —— 默认显示（和以前一样）
        self._show_manager_log: bool = True
        #: key -> 运行状态
        self._states: Dict[str, str] = {}

        #: 当前布局树（已经过对账）
        self._tree: PaneNode = PaneNode.pane()
        #: 布局模板名（用户显式选择过才有值；None 表示"按默认推导"）
        self._layout_kind: Optional[str] = None
        #: 节点路径（id(node) -> "h0/v1"）
        self._node_paths: Dict[int, str] = {}
        #: 路径 -> 比例（QSettings 保存/恢复用）
        self._pane_sizes: Dict[str, List[int]] = {}
        #: 上次上报时的比例快照（避免重复发信号）
        self._last_split_snapshot: Dict[str, List[int]] = {}
        #: 当前聚焦的程序 key
        self._focused_key: str = ""

        self._splitter_host: Optional[QWidget] = None
        self._root_splitter: Optional[QSplitter] = None
        self._split_debounce: Optional[QTimer] = None

        self._build_ui()
        self._connect_manager_signals()
        self.refresh_statuses()

    # ------------------------------------------------------------------
    # 基本属性
    # ------------------------------------------------------------------

    @property
    def bot_id(self) -> str:
        """机器人 id。"""
        return self.bot.id

    @property
    def bot_name(self) -> str:
        """机器人名称。"""
        return self.bot.name

    def base_dir(self):
        """相对路径基准目录（用于解析 cwd 与命令）。"""
        if self.config is not None:
            return self.config.base_dir
        from pathlib import Path

        return Path.cwd()

    def key_for(self, program: Program) -> str:
        """程序对应的 manager key。"""
        return build_manager_key(self.bot.id, program.id)

    def all_keys(self) -> List[str]:
        """所有程序的 key（界面顺序，主程序在前）。"""
        return list(self._order)

    def log_view(self, key: str) -> Optional[BotLogView]:
        """按 key 取日志视图。"""
        return self._views.get(key)

    def log_view_by_program(self, program: Program) -> Optional[BotLogView]:
        """按程序配置取日志视图。"""
        return self._views.get(self.key_for(program))

    def program_of(self, key: str) -> Optional[Program]:
        """按 key 取程序配置快照。"""
        return self._programs.get(key)

    def key_of_program(self, program_id: str) -> str:
        """程序 id -> manager key。"""
        return self._program_to_key.get(program_id) or build_manager_key(
            self.bot.id, program_id
        )

    def program_id_of_key(self, key: str) -> str:
        """manager key -> 程序 id（未知时返回空串）。"""
        return self._key_to_program.get(key, "")

    def pane_count(self) -> int:
        """当前窗格数量（= 右侧同时可见的日志窗口数）。"""
        return len(self._panes())

    def pane_handles(self) -> List[PaneHandle]:
        """所有窗格的只读快照（按界面顺序）。"""
        handles: List[PaneHandle] = []
        for widget in self._panes():
            handles.append(
                PaneHandle(
                    path=widget.path,
                    programs=list(widget.program_ids()),
                    keys=list(widget.keys()),
                    current=widget.current_program_id(),
                    widget=widget,
                )
            )
        return handles

    def pane_paths(self) -> List[str]:
        """所有窗格的路径（与 QSettings 键对应）。"""
        return [widget.path for widget in self._panes()]

    def pane_of_program(self, program_id: str) -> Optional[PaneWidget]:
        """程序所在的窗格控件。"""
        for widget in self._panes():
            if program_id in widget.program_ids():
                return widget
        return None

    def pane_of_key(self, key: str) -> Optional[PaneWidget]:
        """key 所在的窗格控件。"""
        for widget in self._panes():
            if widget.contains(key):
                return widget
        return None

    def set_process_manager(self, manager: Optional[ProcessManager]) -> None:
        """在主窗口创建管理器之后注入（构造时也可以直接传入）。"""
        if self.manager is manager:
            return
        if self.manager is not None:
            self._disconnect_manager_signals()
        self.manager = manager
        self._connect_manager_signals()
        self.refresh_statuses()

    # ------------------------------------------------------------------
    # 布局：读取 / 切换
    # ------------------------------------------------------------------

    def layout_tree(self) -> PaneNode:
        """当前布局树（深拷贝）。"""
        return layout_model.clone_tree(self._tree) or PaneNode.pane()

    def layout_kind(self) -> str:
        """当前布局模板名（用户没选过时按形状推断）。"""
        if self._layout_kind:
            return self._layout_kind
        return layout_model.layout_kind_of(self._tree)

    def layout_description(self) -> str:
        """可读的布局描述，用于状态栏/提示。"""
        labels = {
            program_id: (self._programs.get(key).name or program_id)
            for key, program_id in self._key_to_program.items()
            if self._programs.get(key) is not None
        }
        return layout_model.describe_tree(self._tree, labels)

    def set_layout(self, kind: str, notify: bool = True) -> bool:
        """按模板名切换布局（返回是否有变化）。"""
        program_ids = [program.id for program in self._programs_in_order()]
        tree = layout_model.build_tree(kind, program_ids, self._primary_program_id())
        return self._apply_tree(tree, kind=kind, notify=notify)

    def apply_layout_tree(self, node: Optional[PaneNode], notify: bool = True) -> bool:
        """直接套一棵布局树（custom 布局），会先与程序列表对账。"""
        program_ids = [program.id for program in self._programs_in_order()]
        tree, report = layout_model.reconcile(node, program_ids, self._primary_program_id())
        for reason in report.get("reset", []):
            self.append_all("[布局] {}\n".format(reason))
        return self._apply_tree(tree, kind=layout_model.layout_kind_of(tree), notify=notify)

    def reset_layout(self) -> bool:
        """恢复默认布局。"""
        program_ids = [program.id for program in self._programs_in_order()]
        tree = layout_model.build_default_tree(program_ids, self._primary_program_id())
        self._layout_kind = None
        return self._apply_tree(tree, kind="", notify=True)

    def pane_sizes(self) -> Dict[str, List[int]]:
        """各分割节点的比例（路径 -> 尺寸数组），供 QSettings 保存。"""
        self._capture_pane_sizes()
        return {path: list(sizes) for path, sizes in self._pane_sizes.items()}

    def set_pane_sizes(self, sizes: Optional[Dict[str, List[int]]]) -> bool:
        """恢复各分割节点的比例（界面构建完成之后调用）。"""
        if not sizes:
            return False
        restored: Dict[str, List[int]] = {}
        for path, values in sizes.items():
            if not isinstance(values, (list, tuple)):
                continue
            numbers = [int(value) for value in values if isinstance(value, (int, float))]
            if numbers:
                restored[str(path)] = numbers
        if not restored:
            return False
        self._pane_sizes.update(restored)
        self._apply_pane_sizes()
        # 窗口还没显示时 QSplitter 的可用空间是 0，等第一次布局完成再套一次
        QTimer.singleShot(0, self._apply_pane_sizes)
        return True

    def _capture_pane_sizes(self) -> None:
        """把当前所有 splitter 的比例记进 self._pane_sizes。

        整个包在兜底里（真机 2026-10-05：拖动分隔条时崩溃）：这是被
        splitterMoved / 去抖定时器调用**最频繁**的一段，拖动过程中窗格可能正好
        在重建（旧控件 deleteLater 了但 Python 包装还在），碰到已销毁的控件会抛
        RuntimeError —— 而它一旦冒到 Qt 槽外，PyQt6 会直接结束进程。
        """
        try:
            splitters = self._splitters()
        except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
            _note_slot_error("统计窗格比例", exc)
            return
        for splitter in splitters:
            try:
                path = self._path_of(splitter)
                if not path:
                    continue
                sizes = self._splitter_sizes(splitter)
            except (RuntimeError, AttributeError, TypeError, ValueError):
                continue
            if sizes:
                self._pane_sizes[path] = sizes

    def _apply_pane_sizes(self) -> None:
        """把 self._pane_sizes 应用到界面上。

        只有"保存时的分量个数与当前 splitter 的孩子数一致"才应用：这样主窗口给
        每一个程序调用 set_pane_sizes(旧比例) 时，不会再出现"1 分量比例把 3 个
        窗格平均分配"的副作用。
        """
        try:
            splitters = self._splitters()
        except (RuntimeError, AttributeError, TypeError, ValueError):
            return
        for splitter in splitters:
            try:
                path = self._path_of(splitter)
                if not path:
                    continue
                sizes = self._pane_sizes.get(path)
                if not sizes or len(sizes) != splitter.count():
                    continue
                self._set_splitter_sizes(splitter, sizes)
            except (RuntimeError, AttributeError, TypeError, ValueError):
                # 拖动过程中窗格可能正在重建：跳过这一格，别让异常冒到 Qt 槽外
                continue

    def _path_of(self, splitter: QSplitter) -> str:
        """取 splitter 对应的节点路径。"""
        node = getattr(splitter, "_pane_node", None)
        if node is None:
            return ""
        return self._node_paths.get(id(node), "")

    @staticmethod
    def _splitter_sizes(splitter: QSplitter) -> List[int]:
        """读取 splitter 的尺寸；宽高都为 0（还没布局）时返回空。"""
        try:
            sizes = [int(value) for value in splitter.sizes()]
        except (RuntimeError, AttributeError):
            return []
        if not sizes or sum(sizes) <= 0:
            return []
        return sizes

    def _set_splitter_sizes(self, splitter: QSplitter, sizes: List[int]) -> None:
        """设置 splitter 尺寸；界面尺寸与保存值不同时按比例换算。"""
        if len(sizes) != splitter.count() or sum(sizes) <= 0:
            return
        current = self._splitter_sizes(splitter)
        if current:
            total = sum(current)
            if abs(total - sum(sizes)) > max(2, total // 20):
                scaled = [
                    max(1, int(round(total * value / float(sum(sizes))))) for value in sizes
                ]
            else:
                scaled = list(sizes)
        else:
            scaled = list(sizes)
        try:
            splitter.setSizes(scaled)
        except (RuntimeError, AttributeError):
            pass

    def _splitters(self) -> List[QSplitter]:
        """当前界面上的所有 QSplitter（含嵌套）。"""
        host = self._splitter_host
        if host is None:
            return []
        result: List[QSplitter] = []
        if isinstance(host, QSplitter):
            result.append(host)
        try:
            for splitter in host.findChildren(QSplitter):
                if splitter not in result:
                    result.append(splitter)
        except (RuntimeError, AttributeError):
            pass
        return result

    def _apply_tree(self, tree: PaneNode, kind: str = "", notify: bool = True) -> bool:
        """套用布局树：保存旧比例 -> 重建界面 -> 恢复比例 -> 发信号。"""
        self._capture_pane_sizes()
        self._tree = tree
        self._layout_kind = (kind or "").strip() or None
        self._rebuild_panes()
        self._apply_pane_sizes()
        QTimer.singleShot(0, self._apply_pane_sizes)
        if self._focused_key not in self._views:
            self._focused_key = self._order[0] if self._order else ""
        if self._focused_key:
            self._set_focus(self._focused_key, notify=False)
        self.refresh_statuses()
        if notify:
            self.layoutChanged.emit(self.layout_kind())
        return True

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._build_control_bar())
        self._pane_host_layout = layout

        self._prepare_program_maps()
        self._rebuild_panes()

    def _build_control_bar(self) -> QWidget:
        """顶部控制条：启动 / 停止 / 重启 / 设置 / 布局 / 状态。"""
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        title = QLabel(self.bot.name, bar)
        title_font = QFont(title.font())
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        title.setFont(title_font)
        title.setToolTip("机器人：{}".format(self.bot.name))

        self.start_button = QPushButton("启动全部", bar)
        self.start_button.setToolTip("启动该机器人下的所有程序")
        self.start_button.clicked.connect(self._on_start_all)

        self.stop_button = QPushButton("停止全部", bar)
        self.stop_button.setToolTip("停止该机器人下的所有程序（含进程树）")
        self.stop_button.clicked.connect(self._on_stop_all)

        self.restart_button = QPushButton("重启全部", bar)
        self.restart_button.setToolTip("依次停止并重新启动所有程序")
        self.restart_button.clicked.connect(self._on_restart_all)

        self.edit_button = QPushButton("编辑配置", bar)
        self.edit_button.setToolTip("打开该机器人的配置对话框")
        self.edit_button.clicked.connect(lambda: self.editRequested.emit(self.bot.id))

        self.layout_button = QPushButton("布局 ▾", bar)
        self.layout_button.setToolTip("切换右侧分屏方式（左栏右键里也有）")
        self.layout_button.clicked.connect(self._on_layout_button)

        self.layout_label = QLabel("", bar)
        self.layout_label.setStyleSheet("color: {};".format(muted_text_color(self)))

        self.status_label = QLabel("未启动", bar)
        self.status_label.setToolTip("机器人整体状态")

        row.addWidget(title)
        row.addSpacing(8)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.restart_button)
        row.addWidget(self.edit_button)
        row.addWidget(self.layout_button)
        row.addWidget(self.layout_label)
        row.addStretch(1)
        row.addWidget(self.status_label)
        #  这三个标签的内容会长（机器人名、"只看主程序 | 1 个窗格"……）。QLabel 默认
        #  把"文字宽度"当成自己的最小宽度，右侧内容的 minimumSizeHint 就被撑大，
        #  主分隔条再也没法把更多宽度给左栏 —— 真机 2026-10-05："左栏最大宽度被限制
        #  得太窄，而且只在名字很长的测试实例打开时出现"。改成 Ignored：宽度交给布局。
        #  ⚠️ 必须在三个标签都创建之后再设置（上一版写在这之前，直接 AttributeError
        #     崩在启动路径上 —— 已由 check_definition_order.py 的 [3] 段盯着）。
        for label in (title, self.layout_label, self.status_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Preferred)
        return bar

    def _prepare_program_maps(self) -> None:
        """构建 key / 程序 id / 工作目录等映射（布局重建时复用）。"""
        programs = self._sorted_programs()
        self._programs = {}
        self._key_to_program = {}
        self._program_to_key = {}
        self._working_dirs = {}
        self._order = []
        base = self.base_dir()

        for program in programs:
            key = self.key_for(program)
            self._order.append(key)
            self._programs[key] = program
            self._key_to_program[key] = program.id
            self._program_to_key[program.id] = key
            try:
                self._working_dirs[program.id] = program.working_dir(base)
            except (TypeError, ValueError, OSError):
                self._working_dirs[program.id] = ""

    def _programs_in_order(self) -> List[Program]:
        """按界面顺序（主程序在前）返回 Program 列表。"""
        programs: List[Program] = []
        for key in self._order:
            program = self._programs.get(key)
            if program is not None:
                programs.append(program)
        return programs

    def _primary_program_id(self) -> str:
        """主程序 id（没有 primary 时返回空串，交给 layout 决定）。"""
        for program in self._programs_in_order():
            if program.role == ROLE_PRIMARY:
                return program.id
        return ""

    def _rebuild_panes(self) -> None:
        """按当前布局树重建右侧窗格区域。"""
        # 1) 程序映射刷新（配置可能变了）
        self._prepare_program_maps()
        program_ids = [program.id for program in self._programs_in_order()]

        # 2) 布局与配置对账：用户选过布局 -> 保留；没选过 -> 默认布局
        if self._layout_kind is not None:
            tree, _report = layout_model.reconcile(
                self._tree, program_ids, self._primary_program_id()
            )
        else:
            tree = layout_model.build_default_tree(program_ids, self._primary_program_id())
        self._tree = tree
        self._node_paths = node_paths(tree)

        # 3) 清掉旧区域 —— 但**先把每个程序的日志文本取出来**。
        # 真机反馈："切换日志区布局时日志内容会消失"。
        # 原因：重建会 self._views = {} 并 deleteLater() 掉旧控件，
        # 而日志是存在 QPlainTextEdit 里的（属于控件），控件一销毁文本就没了；
        # _pending_logs 只缓存"当时没有对应 view 的行"，帮不上忙。
        # 所以重建前抓一份快照，建完新窗格再灌回去。
        snapshot = self._capture_log_texts()
        old_host = self._splitter_host
        self._views = {}
        self._splitter_host = None
        self._root_splitter = None
        if old_host is not None:
            try:
                self._pane_host_layout.removeWidget(old_host)
                old_host.setParent(None)
                old_host.deleteLater()
            except (RuntimeError, AttributeError):
                pass

        # 4) 递归建新区域
        widget = self._create_node_widget(tree, self)
        self._splitter_host = widget
        self._root_splitter = widget if isinstance(widget, QSplitter) else None
        self._pane_host_layout.addWidget(widget, 1)

        # 4.5) 把快照灌回新窗格（这时 _views 已指向新控件）
        self._restore_log_texts(snapshot)

        # 5) 补写缓冲日志
        for key, lines in list(self._pending_logs.items()):
            view = self._views.get(key)
            if view is None:
                continue
            for line in lines:
                view.append_log(line)
            self._pending_logs.pop(key, None)

        # 6) 焦点与状态
        if self._focused_key not in self._views:
            self._focused_key = self._order[0] if self._order else ""
        if self._focused_key:
            self._set_focus(self._focused_key, notify=False)
        self.refresh_statuses()

    def _capture_log_texts(self) -> dict:
        """抓取"当前每个程序窗格的日志文本 + 行数"快照（重建布局前调用）。

        为什么需要：日志文本存在 QPlainTextEdit 里，而切换布局会重建整棵窗格树
        （旧控件 deleteLater()），控件一销毁文本就没了 —— 真机反馈的
        "切换日志区布局时日志内容消失"就是这个原因。

        抓的是 ``view.raw_text()``（**含 ANSI 序列的原文**）：新窗格拿到原文后
        会重新解析、按当前主题上色，于是连颜色一起还原；取不到原文的旧控件
        退回 ``toPlainText()`` —— 颜色没了，但文字不丢。

        返回 {manager_key: (文本, 行数)}；读不到文本的窗格不入快照。
        """
        snapshot = {}
        for key, view in list(getattr(self, "_views", {}).items()):
            text = ""
            try:
                text = view.raw_text()
            except (RuntimeError, AttributeError):
                text = ""
            if not text:
                try:
                    text = view.editor.toPlainText()
                except (RuntimeError, AttributeError):
                    # 控件已被销毁（切换过快时会遇到）：跳过，不影响其它窗格
                    continue
            if text:
                snapshot[key] = (text, int(getattr(view, "_line_count", 0)))
        return snapshot

    def _restore_log_texts(self, snapshot: dict) -> None:
        """把 :meth:`_capture_log_texts` 的快照灌回新建的窗格。

        走 ``load_raw_text()`` 而不是 ``setPlainText()``：前者会重新跑一遍 ANSI
        解析（颜色跟着回来），且不额外补换行 —— 快照本身已经是完整文本。
        同时把 auto_scroll 恢复到"贴底"状态，符合"切完布局看到最新输出"的预期。
        """
        if not snapshot:
            return
        for key, (text, line_count) in snapshot.items():
            view = self._views.get(key)
            if view is None:
                # 该程序在新布局里没有窗格（例如改成单窗格布局）：留给 _pending_logs
                continue
            try:
                view.load_raw_text(text)
                # 行数计数与控件内部状态跟着对齐（否则状态栏会显示 0 行）
                view._line_count = max(int(line_count), text.count("\n"))
                try:
                    view._update_count_label()
                except (AttributeError, RuntimeError):
                    pass
                view.scroll_to_bottom()
            except (RuntimeError, AttributeError):
                continue

    def _create_node_widget(self, node: PaneNode, parent: QWidget) -> QWidget:
        """递归把布局节点变成控件：split -> QSplitter，pane -> PaneWidget。"""
        if node.is_split:
            orientation = (
                Qt.Orientation.Horizontal if node.orientation == layout_model.ORIENT_H
                else Qt.Orientation.Vertical
            )
            splitter = QSplitter(orientation, parent)
            splitter.setObjectName("paneSplitter")
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(6)
            splitter.setOpaqueResize(True)
            setattr(splitter, "_pane_node", node)
            for child in node.children:
                splitter.addWidget(self._create_node_widget(child, splitter))
                splitter.setStretchFactor(splitter.count() - 1, 1)
            self._install_splitter_tracking(splitter)
            return splitter

        pane = PaneWidget(
            node=node,
            bot_id=self.bot.id,
            programs=self._programs,
            program_keys=self._program_to_key,
            working_dirs=self._working_dirs,
            parent=parent,
            max_lines=self._max_lines,
        )
        pane.path = self._node_paths.get(id(node), "")
        pane.programAction.connect(self._on_program_action)
        pane.activated.connect(lambda key: self._set_focus(key))
        pane.openDirectoryRequested.connect(
            lambda path: self.openDirectoryRequested.emit(path)
        )
        for key, view in pane._views.items():
            self._views[key] = view
            view.contextMenuRequested.connect(self._on_view_context_menu)
        return pane

    def _install_splitter_tracking(self, splitter: QSplitter) -> None:
        """监听分隔条拖动：停止后把比例记进缓存并发出 layoutChanged。

        QSplitter 没有"拖动结束"信号，splitterMoved 会在拖动过程中连续触发，
        这里用 300ms 去抖合并成一次保存。
        """
        if self._split_debounce is None:
            self._split_debounce = QTimer(self)
            self._split_debounce.setSingleShot(True)
            self._split_debounce.setInterval(SPLIT_SETTLE_MS)
            self._split_debounce.timeout.connect(self._on_split_settled)

        def _moved(_pos: int, _index: int) -> None:
            # 这个槽在**拖动分隔条时连续触发**（Qt 信号槽）—— 里面抛异常会被 PyQt6
            # 直接终止进程（真机踩过好几次"拖一下就闪退"）。宁可少记一次比例，
            # 也不能把管理器带走。
            try:
                self._capture_pane_sizes()
            except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                _note_slot_error("拖动分隔条", exc)
                return
            if self._split_debounce is not None:
                self._split_debounce.start()

        try:
            splitter.splitterMoved.connect(_moved)
        except (AttributeError, TypeError):
            pass

    def _on_split_settled(self) -> None:
        """分隔条停止拖动：把新的比例通知出去（主窗口写入 QSettings）。

        同样整个包在兜底里：这是 QTimer.timeout 槽，异常会让 PyQt6 终止进程
        （真机 2026-10-05：拖动分隔条时崩溃）。
        """
        try:
            self._capture_pane_sizes()
        except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
            _note_slot_error("分隔条拖动结束", exc)
            return
        if self._pane_sizes != self._last_split_snapshot:
            self._last_split_snapshot = {
                path: list(sizes) for path, sizes in self._pane_sizes.items()
            }
            self.layoutChanged.emit(self.layout_kind())

    def _sorted_programs(self) -> List[Program]:
        """主程序排最前，其余保持配置顺序。"""
        programs = list(self.bot.programs)
        programs.sort(key=lambda item: 0 if item.role == ROLE_PRIMARY else 1)
        return programs

    # ------------------------------------------------------------------
    # 窗格集合
    # ------------------------------------------------------------------

    def _panes(self) -> List[PaneWidget]:
        """当前界面上所有窗格（界面顺序）。"""
        return collect_pane_widgets(self._splitter_host)

    # ------------------------------------------------------------------
    # 焦点
    # ------------------------------------------------------------------

    def focused_program(self) -> str:
        """当前聚焦的 manager key。"""
        return self._focused_key

    def focus_program(self, key: str, scroll: bool = True) -> bool:
        """把焦点切到某个程序：选中所在窗格与标签，并可滚动到底部。

        左栏单击"某程序"时主窗口就是调这个。
        """
        if key not in self._views:
            return False
        pane = self.pane_of_key(key)
        if pane is not None:
            program_id = self._key_to_program.get(key, "")
            if program_id:
                pane.select_program(program_id)
        self._set_focus(key, notify=True)
        if scroll:
            view = self._views.get(key)
            if view is not None:
                try:
                    view.scroll_to_bottom()
                    view.setFocus(Qt.FocusReason.OtherFocusReason)
                except (RuntimeError, AttributeError):
                    pass
        return True

    def focus_first_program(self, scroll: bool = False) -> bool:
        """聚焦主程序（没有 primary 时取第一个程序）。"""
        if not self._order:
            return False
        return self.focus_program(self._order[0], scroll=scroll)

    def _set_focus(self, key: str, notify: bool = True) -> None:
        """内部：记录焦点并把高亮刷到窗格上。"""
        if key and key not in self._views:
            key = self._order[0] if self._order else ""
        self._focused_key = key
        for pane in self._panes():
            pane.set_focused(bool(key) and pane.contains(key))
        if notify and key:
            self.focusChanged.emit(key)

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------

    def append_log(self, key: str, text: object) -> None:
        """按 key 追加日志；视图还没建立时先缓冲。"""
        view = self._views.get(key)
        if view is not None:
            view.append_log(text)
            return
        self._pending_logs.setdefault(key, []).append(text)

    def append_log_by_program(self, program: Program, text: object) -> None:
        """按程序配置追加日志。"""
        self.append_log(self.key_for(program), text)

    def append_all(self, text: object) -> None:
        """向本页所有日志窗口写入同一段文本（例如整体操作提示）。"""
        for view in self._views.values():
            view.append_log(text)

    def clear_all_logs(self) -> None:
        """清空本页所有日志窗口。"""
        for view in self._views.values():
            view.clear_log()

    # ------------------------------------------------------------------
    # 与进程管理器联动
    # ------------------------------------------------------------------

    def _connect_manager_signals(self) -> None:
        if self.manager is None:
            return
        try:
            self.manager.output_text.connect(self._on_output_text)
            self.manager.state_changed.connect(self._on_state_changed)
            self.manager.process_error.connect(self._on_process_error)
            self.manager.process_started.connect(self._on_process_started)
        except (RuntimeError, TypeError):
            pass

    def _disconnect_manager_signals(self) -> None:
        if self.manager is None:
            return
        for signal, slot in (
            (self.manager.output_text, self._on_output_text),
            (self.manager.state_changed, self._on_state_changed),
            (self.manager.process_error, self._on_process_error),
            (self.manager.process_started, self._on_process_started),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _on_output_text(self, key: str, text: str, channel: str = "") -> None:
        """一段输出到了 —— 管理器自己的日志是否显示，在这里按 channel 过滤。

        真机事故（2026-10-03）：管理器日志以前有**两条**写入路径 ——
        `ProcessManager._log()` 既发 `output_text`（前缀 `[管理器] `），
        MainWindow 又把 `log_message` 转成同样的文本 append 一次。
        于是**每条管理器日志都显示两遍**，而「运行参数 → 把管理器自身日志也写入窗口」
        这个开关也形同虚设（关掉也照样显示）。

        现在只保留 `output_text` 这一条路径；开关通过 set_show_manager_log()
        传进来，按 channel == "manager" 过滤。
        """
        if channel == "manager" and not self._show_manager_log:
            return
        if key in self._views or key in self._order:
            self.append_log(key, text)

    def set_show_manager_log(self, enabled: bool) -> None:
        """是否把管理器自身日志（`[管理器] …`）显示在日志区（运行参数里的开关）。"""
        self._show_manager_log = bool(enabled)

    def _on_state_changed(self, key: str, state: str, _message: str = "") -> None:
        if key not in self._views:
            return
        self._states[key] = state
        self.refresh_statuses()

    def _on_process_error(self, key: str, _message: str) -> None:
        if key not in self._views:
            return
        self._states[key] = ProcessManager.STATE_FAILED
        self.refresh_statuses()

    def _on_process_started(self, key: str, _pid: int) -> None:
        if key not in self._views:
            return
        self.refresh_statuses()

    def _state_of(self, key: str) -> str:
        """取某个程序的状态（优先用缓存，其次问管理器）。"""
        if key in self._states:
            return self._states[key]
        if self.manager is not None:
            try:
                state = self.manager.state(key)
            except (RuntimeError, AttributeError):
                state = ProcessManager.STATE_IDLE
            if state == ProcessManager.STATE_IDLE:
                return ProcessManager.STATE_STOPPED
            return state
        return ProcessManager.STATE_STOPPED

    def _state_map(self) -> Dict[str, str]:
        """key -> 状态，并附带 key#pid（PID 字样）。"""
        result: Dict[str, str] = {}
        for key in self._order:
            state = self._state_of(key)
            result[key] = state
            if self.manager is not None:
                try:
                    pid = self.manager.pid(key)
                except (RuntimeError, AttributeError):
                    pid = 0
                if pid:
                    result["{}#pid".format(key)] = str(pid)
        return result

    def refresh_statuses(self) -> None:
        """刷新所有窗格标题与整体状态标签。"""
        states = self._state_map()
        running = 0
        failed = 0
        for key in self._order:
            state = states.get(key, ProcessManager.STATE_STOPPED)
            self._states[key] = state
            if state == ProcessManager.STATE_RUNNING:
                running += 1
            elif state == ProcessManager.STATE_FAILED:
                failed += 1
            view = self._views.get(key)
            if view is not None:
                color = STATE_COLORS.get(state, theme_tokens.COLOR_IDLE)
                text = STATE_TEXTS.get(state, state)
                pid = states.get("{}#pid".format(key), "")
                if state == ProcessManager.STATE_RUNNING and pid:
                    text = "运行中（PID {}）".format(pid)
                view.set_status_text(text, color)

        for pane in self._panes():
            pane.refresh_title(states)

        total = len(self._order)
        if total == 0:
            summary, color = "无程序", theme_tokens.COLOR_IDLE
        elif running == total:
            summary, color = "全部运行中（{}/{}）".format(
                running, total
            ), STATE_COLORS[ProcessManager.STATE_RUNNING]
        elif running > 0:
            summary, color = "部分运行（{}/{}）".format(
                running, total
            ), STATE_COLORS[ProcessManager.STATE_STARTING]
        elif failed:
            summary, color = "异常（{}/{}）".format(
                failed, total
            ), STATE_COLORS[ProcessManager.STATE_FAILED]
        else:
            summary, color = "已停止（0/{}）".format(total), STATE_COLORS[
                ProcessManager.STATE_STOPPED
            ]

        status_label = getattr(self, "status_label", None)
        if status_label is not None:
            status_label.setText("● " + summary)
            status_label.setStyleSheet("color: {};".format(color))
        layout_label = getattr(self, "layout_label", None)
        if layout_label is not None:
            layout_label.setText(
                "{}　|　{} 个窗格".format(
                    layout_model.LAYOUT_KIND_LABELS.get(
                        self.layout_kind(), self.layout_kind()
                    ),
                    self.pane_count(),
                )
            )
        self._update_controls_enabled()

    def _update_controls_enabled(self) -> None:
        has_program = bool(self._order)
        running = any(self._is_running(key) for key in self._order)
        if getattr(self, "start_button", None) is not None:
            self.start_button.setEnabled(has_program and not running)
            self.stop_button.setEnabled(has_program and running)
            self.restart_button.setEnabled(has_program)
            self.layout_button.setEnabled(len(self._order) > 1)

    # ------------------------------------------------------------------
    # 控制条动作
    # ------------------------------------------------------------------

    def _on_start_all(self) -> None:
        keys = self.all_keys()
        if keys:
            self.startRequested.emit(keys)

    def _on_stop_all(self) -> None:
        keys = [key for key in self.all_keys() if self._is_running(key)]
        if keys:
            self.stopRequested.emit(keys)

    def _on_restart_all(self) -> None:
        keys = self.all_keys()
        if keys:
            self.restartRequested.emit(keys)

    def _on_program_action(self, key: str, action: str) -> None:
        """窗格按钮：把单个程序的动作转成信号给主窗口。"""
        if not key:
            return
        self.programActionRequested.emit(key, action)

    # ------------------------------------------------------------------
    # 布局菜单
    # ------------------------------------------------------------------

    def _on_layout_button(self) -> None:
        """控制条上的「布局 ▾」：弹出模板菜单。

        构建菜单时会遍历当前窗格（可能正好在重建）—— 整个包在兜底里：
        按钮的 clicked 是 Qt 槽，异常会让 PyQt6 终止进程。
        """
        button = getattr(self, "layout_button", None)
        if button is None:
            return
        try:
            menu = self.build_layout_menu(menu_parent=button)
            menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        except (RuntimeError, AttributeError, TypeError, ValueError):
            return

    def build_layout_menu(self, menu_parent: Optional[QWidget] = None) -> QMenu:
        """构造布局菜单（控制条按钮与左栏右键共用）。"""
        menu = QMenu(menu_parent or self)
        header = QAction("右侧分屏方式", menu)
        header.setEnabled(False)
        menu.addAction(header)
        menu.addSeparator()

        current = self.layout_kind()
        for kind in LAYOUT_MENU_ORDER:
            action = QAction(layout_model.LAYOUT_KIND_LABELS.get(kind, kind), menu)
            action.setCheckable(True)
            action.setChecked(kind == current)
            action.triggered.connect(lambda _checked=False, k=kind: self.set_layout(k))
            menu.addAction(action)

        if current == layout_model.KIND_CUSTOM:
            custom = QAction("当前：自定义布局", menu)
            custom.setEnabled(False)
            menu.addAction(custom)

        menu.addSeparator()
        reset = QAction("恢复默认布局", menu)
        reset.triggered.connect(lambda: self.reset_layout())
        menu.addAction(reset)
        return menu

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------

    def _on_view_context_menu(self, _key: str) -> None:
        """BotLogView 已自行弹出菜单，这里只保留槽位便于将来扩展。"""
        return

    def _forget_view(self, key: str) -> None:
        """视图被销毁后清理映射。"""
        self._views.pop(key, None)
        self._programs.pop(key, None)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """本页空白处右键：作用于全部程序。"""
        self.show_context_menu(event.globalPos(), key=None)
        event.accept()

    def show_context_menu(self, global_pos, key: Optional[str] = None) -> None:
        """弹出右键菜单。

        - key 为 None：作用于本页全部程序
        - key 为具体程序：只作用于该程序，并额外提供"布局"子菜单
        """
        if key is not None and key in self._views:
            program = self._programs.get(key)
            scope_name = program.name if program is not None else key
            keys = [key]
        else:
            scope_name = ""
            keys = self.all_keys()

        if not keys:
            return

        running_keys = [item for item in keys if self._is_running(item)]
        menu = QMenu(self)
        suffix = "［{}］".format(scope_name) if scope_name else "（全部程序）"

        action_start = QAction("启动 {}".format(suffix), menu)
        action_start.setEnabled(bool(keys))
        action_start.triggered.connect(lambda: self.startRequested.emit(list(keys)))
        menu.addAction(action_start)

        action_stop = QAction("停止 {}".format(suffix), menu)
        action_stop.setEnabled(bool(running_keys))
        action_stop.triggered.connect(lambda: self.stopRequested.emit(list(keys)))
        menu.addAction(action_stop)

        action_restart = QAction("重启 {}".format(suffix), menu)
        action_restart.setEnabled(bool(keys))
        action_restart.triggered.connect(lambda: self.restartRequested.emit(list(keys)))
        menu.addAction(action_restart)

        menu.addSeparator()

        action_edit = QAction("编辑配置", menu)
        action_edit.triggered.connect(lambda: self.editRequested.emit(self.bot.id))
        menu.addAction(action_edit)

        layout_menu = menu.addMenu("布局")
        current = self.layout_kind()
        for kind in LAYOUT_MENU_ORDER:
            item = QAction(layout_model.LAYOUT_KIND_LABELS.get(kind, kind), layout_menu)
            item.setCheckable(True)
            item.setChecked(kind == current)
            item.triggered.connect(lambda _checked=False, k=kind: self.set_layout(k))
            layout_menu.addAction(item)
        layout_menu.addSeparator()
        reset_item = QAction("恢复默认布局", layout_menu)
        reset_item.triggered.connect(lambda: self.reset_layout())
        layout_menu.addAction(reset_item)

        action_clear = QAction("清空日志 {}".format(suffix), menu)
        action_clear.triggered.connect(lambda: self._clear_logs(keys))
        menu.addAction(action_clear)

        action_copy = QAction("复制日志 {}".format(suffix), menu)
        action_copy.triggered.connect(lambda: self._copy_logs(keys))
        menu.addAction(action_copy)

        action_open = QAction("打开工作目录…", menu)
        action_open.setEnabled(self._has_existing_dir(keys))
        action_open.triggered.connect(lambda: self._open_dir(keys))
        menu.addAction(action_open)

        menu.addSeparator()
        action_close = QAction("关闭本页", menu)
        action_close.triggered.connect(lambda: self.closeRequested.emit(self.bot.id))
        menu.addAction(action_close)

        menu.exec(global_pos)

    def _clear_logs(self, keys: List[str]) -> None:
        for key in keys:
            view = self._views.get(key)
            if view is not None:
                view.clear_log()

    def _copy_logs(self, keys: List[str]) -> None:
        chunks: List[str] = []
        for key in keys:
            view = self._views.get(key)
            if view is None:
                continue
            program = self._programs.get(key)
            title = program.name if program is not None else key
            chunks.append("===== {} =====\n{}".format(title, view.text()))
        if not chunks:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(chunks))

    def _open_dir(self, keys: List[str]) -> None:
        for key in keys:
            program = self._programs.get(key)
            if program is None:
                continue
            path = program.working_dir(self.base_dir())
            if os.path.isdir(path):
                self.openDirectoryRequested.emit(path)
                try:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                except Exception:
                    pass
                return

    def _has_existing_dir(self, keys: List[str]) -> bool:
        for key in keys:
            program = self._programs.get(key)
            if program is None:
                continue
            if os.path.isdir(program.working_dir(self.base_dir())):
                return True
        return False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def _is_running(self, key: str) -> bool:
        return bool(self.manager is not None and self.manager.is_running(key))

    def apply_bot(self, bot: Bot) -> None:
        """配置变更后重建界面（日志历史不保留）。"""
        self.bot = bot
        self._pending_logs.clear()
        self._rebuild_panes()

    def apply_theme(self) -> None:
        """系统深色/浅色切换时，把所有窗格与日志窗口的样式一起刷新。"""
        for pane in self._panes():
            try:
                pane.apply_theme()
            except (RuntimeError, AttributeError):
                continue
        layout_label = getattr(self, "layout_label", None)
        if layout_label is not None:
            try:
                layout_label.setStyleSheet("color: {};".format(muted_text_color(self)))
            except RuntimeError:
                pass

    def subscribe_process_manager(self, manager: Optional[ProcessManager]) -> None:
        """（重新）订阅进程管理器的信号。

        主窗口关闭某个机器人的窗口后又重新打开时，Qt 可能复用了同一个 BotTab
        对象；此时 __init__ 里那次订阅已经被 _disconnect_manager_signals 断开，
        需要显式补一次，避免重建后收不到日志与状态。
        """
        if self.manager is not manager:
            self.set_process_manager(manager)
            return
        if manager is None:
            return
        self._disconnect_manager_signals()
        self._connect_manager_signals()
        self.refresh_statuses()

    def color_scheme(self) -> str:
        """当前主题（"dark" / "light"），供外部判断。"""
        return theme_tokens.current_scheme(self)

    def stop_timeout_ms(self) -> int:
        """停止超时时间（毫秒），来自配置。"""
        if self.config is not None:
            return int(max(1.0, float(self.config.stop_timeout)) * 1000)
        return DEFAULT_STOP_TIMEOUT_MS

    def summary(self) -> str:
        """一句话摘要，便于状态栏显示。"""
        return "{}：{} 个程序 / {} 个窗格　{}".format(
            self.bot.name, len(self._order), self.pane_count(),
            self.status_label.text() if getattr(self, "status_label", None) else "",
        )

    # ---- 兼容旧接口（主窗口在步骤 2~4 之间仍会调用）----

    def split_sizes(self) -> List[int]:
        """旧接口：返回根分割条的比例；没有分割时返回空列表。"""
        root = self._root_splitter
        if root is None:
            splitters = self._splitters()
            root = splitters[0] if splitters else None
        if root is None:
            return []
        return self._splitter_sizes(root)

    def set_split_sizes(self, sizes: List[int]) -> None:
        """旧接口：把旧的 split/<bot_id> 比例迁移到根分割条上。"""
        if not sizes:
            return
        root = self._root_splitter
        if root is None:
            splitters = self._splitters()
            root = splitters[0] if splitters else None
        if root is None:
            return
        if len(sizes) == root.count():
            self._set_splitter_sizes(root, [int(value) for value in sizes])
            self._capture_pane_sizes()
        else:
            self._apply_default_split()

    def _apply_default_split(self) -> None:
        """给根分割条一个稳定的默认比例（首次打开窗口、无历史比例时）。"""
        root = self._root_splitter
        if root is None:
            splitters = self._splitters()
            root = splitters[0] if splitters else None
        if root is None:
            return
        count = root.count()
        if count <= 0:
            return
        if count == 1:
            root.setSizes([1])
            return
        total = max(200, root.height() or root.width() or 600)
        first = int(total * DEFAULT_SPLIT_RATIO[0] / 100.0)
        rest = max(80, total - first)
        sizes = [first] + [rest // (count - 1)] * (count - 1)
        root.setSizes(sizes)

    def reset_split(self) -> None:
        """恢复默认分割比例（双击分割条）。"""
        self._apply_default_split()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt 命名)
        """双击分割条 -> 恢复默认比例。"""
        try:
            if event.type() == QEvent.Type.MouseButtonDblClick:
                for splitter in self._splitters():
                    for index in range(splitter.count() - 1):
                        if obj is splitter.handle(index):
                            self.reset_split()
                            return True
        except (AttributeError, RuntimeError):
            return False
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# 自检：可用 QT_QPA_PLATFORM=offscreen 无界面运行
# ---------------------------------------------------------------------------

def _build_demo_bot(count: int) -> Bot:
    """构造一个用于自检的机器人（程序用 ping 占位，能真实起停）。"""
    bot = Bot(id="bot_demo", name="演示机器人", qq="123456")
    if count >= 1:
        bot.programs.append(
            Program(
                id=new_id("prog"),
                name="主程序",
                role=ROLE_PRIMARY,
                cwd=".",
                command="cmd.exe /c ping -n 30 127.0.0.1",
                delay=0.0,
                auto_latest_jar=False,
            )
        )
    for index in range(2, count + 1):
        bot.programs.append(
            Program(
                id=new_id("prog"),
                name="副程序{}".format(index - 1),
                role=ROLE_SECONDARY,
                cwd=".",
                command="cmd.exe /c ping -n 30 127.0.0.1",
                delay=float(index),
            )
        )
    return bot


def _selftest() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    config = BotConfig.from_dict(
        {"version": 1, "bots": []}, path=os.path.join(os.getcwd(), "bots_config.json")
    )
    manager = ProcessManager()

    # 1) 单程序：一个窗格，没有 splitter
    single = BotTab(_build_demo_bot(1), config, manager)
    single.resize(900, 600)
    single.show()
    print("[1] 单程序：窗格数 =", single.pane_count(),
          "| splitter =", collect_splitter_orientations(single._splitter_host),
          "| 布局 =", single.layout_kind())
    assert single.pane_count() == 1
    assert collect_splitter_orientations(single._splitter_host) == []
    assert single.layout_kind() == layout_model.KIND_SINGLE

    # 2) 三程序：默认上下 -> 上格主程序、下格标签
    multi = BotTab(_build_demo_bot(3), config, manager)
    multi.resize(900, 600)
    multi.show()
    handles = multi.pane_handles()
    print("[2] 三程序：窗格 =", [(h.path, h.program_count, h.has_tabs) for h in handles])
    print("    方向 =", collect_splitter_orientations(multi._splitter_host),
          "| 布局 =", multi.layout_kind(), "|", multi.layout_description())
    assert multi.pane_count() == 2
    assert collect_splitter_orientations(multi._splitter_host) == ["v"]
    assert multi.layout_kind() == layout_model.KIND_V
    assert handles[0].program_count == 1 and handles[0].has_tabs is False
    assert handles[1].program_count == 2 and handles[1].has_tabs is True

    # 3) 日志按 key 分发：各进各的窗格
    keys = multi.all_keys()
    assert len(keys) == 3
    for index, key in enumerate(keys):
        multi.append_log(key, "程序{}输出\n".format(index))
    for index, key in enumerate(keys):
        assert "程序{}输出".format(index) in multi.log_view(key).text()
    assert multi.log_view(keys[0]) is not multi.log_view(keys[1])
    print("[3] 日志分发：3 个程序各自独立视图且都收到内容")

    # 4) 切布局：h / h_left_v / h_right_v / tabs / single / v
    for kind, expect_panes, expect_orient in (
        ("h", 2, ["h"]),
        ("h_left_v", 3, ["h", "v"]),
        ("h_right_v", 3, ["h", "v"]),
        ("tabs", 1, []),
        ("single", 1, []),
        ("v", 2, ["v"]),
    ):
        multi.set_layout(kind, notify=False)
        got_panes = multi.pane_count()
        got_orient = collect_splitter_orientations(multi._splitter_host)
        print("    set_layout({:<10}) -> 窗格 {} 方向 {} 名称 {}".format(
            kind, got_panes, got_orient, multi.layout_kind()))
        assert got_panes == expect_panes, (kind, got_panes)
        assert got_orient == expect_orient, (kind, got_orient)
        assert multi.layout_kind() == kind, (kind, multi.layout_kind())

    # 5) 切完布局后日志仍然可用
    multi.set_layout("h_left_v", notify=False)
    assert multi.pane_count() == 3
    for key in keys:
        view = multi.log_view(key)
        assert view is not None, key
        multi.append_log(key, "重建后输出\n")
        assert "重建后输出" in view.text()
    print("[5] 布局切换后 3 个程序的视图都重建成功且仍可写入")

    # 6) 焦点：focus_program 选中所在窗格并高亮
    assert multi.focus_program(keys[2], scroll=False) is True
    assert multi.focused_program() == keys[2]
    pane_with_key = multi.pane_of_key(keys[2])
    assert pane_with_key is not None and pane_with_key.focused is True
    assert multi.pane_of_key(keys[0]).focused is False
    print("[6] focus_program ->", keys[2], "窗格 =", pane_with_key.path,
          "| 高亮 =", pane_with_key.focused)

    # 7) 自定义布局：对账（幽灵程序剔除、全部程序都在）
    program_ids = [multi.program_id_of_key(key) for key in keys]
    custom = PaneNode.split(layout_model.ORIENT_H, [
        PaneNode.split(layout_model.ORIENT_V, [
            PaneNode.pane("ghost"), PaneNode.pane(program_ids[1]),
        ]),
        PaneNode.pane(program_ids[0], program_ids[2]),
    ])
    multi.apply_layout_tree(custom, notify=False)
    placed = sorted(multi.layout_tree().program_ids())
    print("[7] 自定义布局 -> 窗格 {} 名称 {} 程序 {}".format(
        multi.pane_count(), multi.layout_kind(), placed))
    assert multi.pane_count() == 3
    assert multi.layout_kind() == layout_model.KIND_CUSTOM
    assert placed == sorted(program_ids), (placed, program_ids)
    assert "ghost" not in placed

    # 8) 比例保存/恢复
    multi.set_layout("v", notify=False)
    app.processEvents()
    sizes = multi.pane_sizes()
    print("[8] pane_sizes =", sizes)
    assert sizes, "应有至少一个分割节点的比例"
    first_path = sorted(sizes)[0]
    multi._pane_sizes[first_path] = [300, 200]
    multi._apply_pane_sizes()
    app.processEvents()
    assert multi.set_pane_sizes({}) is False
    assert multi.set_pane_sizes({first_path: [400, 200]}) is True

    # 9) 窗格按钮 -> programActionRequested 信号
    received = []
    multi.programActionRequested.connect(lambda key, action: received.append((key, action)))
    multi.pane_of_key(keys[0])._emit_action("start")
    multi.pane_of_key(keys[0])._emit_action("stop")
    assert (keys[0], "start") in received and (keys[0], "stop") in received
    print("[9] 窗格按钮发出 =", received)

    # 10) 真实起停一个占位进程，验证标题/状态/PID
    host = QWidget()
    host.resize(900, 600)
    live = BotTab(_build_demo_bot(2), config, manager, host)
    live.resize(900, 600)
    live.show()
    live_key = live.all_keys()[0]
    program = live.program_of(live_key)
    manager.start(live_key, program, config.base_dir, "演示机器人 / 主程序")
    for _ in range(300):
        app.processEvents()
        if manager.is_running(live_key):
            break
    live.refresh_statuses()
    pane0 = live.pane_of_key(live_key)
    print("[10] 运行中窗格标题 =", pane0.title_label.text(),
          "| 状态 =", pane0.status_label.text(), "| PID =", pane0.pid_label.text())
    assert "运行中" in pane0.status_label.text()
    assert manager.pid(live_key) > 0
    manager.stop(live_key, timeout_ms=1000)
    for _ in range(200):
        app.processEvents()
        if not manager.is_running(live_key):
            break
    live.refresh_statuses()

    # 11) apply_bot（配置变更重建）不崩
    live.apply_bot(_build_demo_bot(3))
    print("[11] apply_bot 后 窗格 =", live.pane_count(), "程序 =", len(live.all_keys()))
    assert live.pane_count() == 2 and len(live.all_keys()) == 3

    # 12) 主题与布局菜单
    live.apply_theme()
    menu = live.build_layout_menu()
    print("[12] 主题 =", live.color_scheme(), "| 菜单项 =",
          [action.text() for action in menu.actions() if action.text()])
    assert live.color_scheme() in ("dark", "light")
    assert len(menu.actions()) >= len(LAYOUT_MENU_ORDER)

    print("\n自检通过：窗格树（单窗格/上下/左右/嵌套/标签）、日志分发、焦点跳转、"
          "自定义布局对账、比例保存、窗格按钮信号、状态刷新、配置重建、主题与布局菜单均正常。")
    manager.stop_all(timeout_ms=1000, wait=True)
    manager.cleanup()
    single.close()
    multi.close()
    live.close()
    host.close()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
