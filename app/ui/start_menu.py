# -*- coding: utf-8 -*-
"""「启动全部」旁边的勾选下拉：挑这一次要启动哪些实例。

真机需求（2026-10-05）
----------------------
> 能否在「启动全部」那里添加一个类似于「布局 ▾」的下拉栏，里面浅色字写"选择启动的程序"，
> 保留完整的实例框架，类似于分支排布（类似于 cmd 中 tree 指令，跟左侧栏差不多就行），
> 然后每个选项都有右侧勾选框。然后需要有"如果勾选 ATRI bot，则其下的三个小程序默认勾选"的功能。

对应实现：
  · `StartSelectionMenu` 就是一个 QMenu，挂在「启动全部」右边的 `▾` 按钮上；
  · 每一行都是**自己画的**：缩进 + 名称（+ 浅色后缀）…… 右侧勾选框。
    为什么不用 QAction：Qt 的 action 勾选框在**左边**，而需求要右边，还要层级缩进；
    层级只靠缩进 + 机器人名加粗（真机反馈：不要 cmd tree 那种制表符号）。
  · 勾/取消**机器人**那一行 → 它下面所有程序跟着变；子项部分勾选时父项显示"半选"；
  · 底部「全选 / 清空」+ 主操作「启动勾选的」。
"""

from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from app.process_manager import build_manager_key
from app.ui.program_widget import muted_text_color

#: 每级缩进的像素（层级只靠缩进表达 —— 真机反馈：不要 cmd `tree` 那种制表符号）
LEVEL_INDENT = 22
#: 菜单最小宽度 / 列表最大高度（机器人多的时候别顶出屏幕）
MENU_MIN_WIDTH = 340
LIST_MAX_HEIGHT = 420


def startable_programs(bot) -> List:
    """机器人下**可以启动**的程序：主程序在前，其余保持配置顺序。

    与 BotTab._sorted_programs() 同一顺序，勾选列表和界面上看到的一致。
    """
    from app.config import ROLE_PRIMARY

    programs = [program for program in getattr(bot, "programs", []) if program.enabled]
    programs.sort(key=lambda item: 0 if item.role == ROLE_PRIMARY else 1)
    return programs


class _CheckRow(QWidget):
    """一行：缩进 + 名称 ……（浅色后缀）+ 右侧勾选框（点整行也能切换）。

    层级只靠**缩进**表达 —— 真机反馈（2026-10-05）：不要 cmd `tree` 那种 `├──`
    制表符号，去掉之后间距调好就很干净；机器人名加粗、后缀（当前 / 主程序）用次要文字色。
    """

    def __init__(self, text: str, box: QCheckBox, indent: int = 0,
                 suffix: str = "", bold: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.box = box
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12 + indent, 3, 12, 3)
        layout.setSpacing(8)

        label = QLabel(text, self)
        if bold:
            font = QFont(label.font())
            font.setBold(True)
            label.setFont(font)
        layout.addWidget(label, 0)

        if suffix:
            hint = QLabel(suffix, self)
            hint.setStyleSheet("color: {};".format(muted_text_color(self)))
            layout.addWidget(hint, 0)

        layout.addStretch(1)
        layout.addWidget(box, 0, Qt.AlignmentFlag.AlignRight)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """点整行 = 切换这一行的勾选（不用非得瞄准那个小方框）。"""
        if self.box.isEnabled():
            self.box.setChecked(not self.box.isChecked())
        super().mousePressEvent(event)


class StartSelectionMenu(QMenu):
    """树形勾选菜单：`selected_keys()` 返回这次要启动的 manager key 列表。"""

    #: 用户点了「启动勾选的」：参数是要启动的 manager key 列表（复用启动全部那条路径）
    startRequested = pyqtSignal(list)

    def __init__(self, bots, current_bot_id: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        #: manager key -> 勾选框（程序行）
        self._boxes: Dict[str, QCheckBox] = {}
        #: bot_id -> (勾选框, [它下面所有程序的 key])
        self._bot_rows: Dict[str, object] = {}
        self._current_bot_id = current_bot_id or ""
        self._bots = [bot for bot in bots if getattr(bot, "enabled", True)]

        self.setMinimumWidth(MENU_MIN_WIDTH)
        self._build_header()
        self._build_tree()
        self._build_footer()
        self._sync_bot_rows()

    # ------------------------------------------------------------------
    # 结构
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        header = QAction("选择启动的程序", self)
        header.setEnabled(False)
        self.addAction(header)
        self.addSeparator()

    def _build_tree(self) -> None:
        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 2, 0, 2)
        column.setSpacing(0)

        for bot_index, bot in enumerate(self._bots):
            programs = startable_programs(bot)
            bot_box = QCheckBox(holder)
            bot_box.setTristate(True)
            bot_box.setToolTip("勾选后，这个机器人下面的程序一起启动")
            bot_keys: List[str] = []

            # 机器人之间留一点空，视觉上分组（机器人名加粗，不用符号也能看出层级）
            if bot_index:
                column.addSpacing(6)
            column.addWidget(_CheckRow(
                getattr(bot, "name", bot.id), bot_box, indent=0, bold=True,
                suffix="（当前）" if bot.id == self._current_bot_id else "",
                parent=holder))

            for program in programs:
                key = build_manager_key(bot.id, program.id)
                bot_keys.append(key)
                box = QCheckBox(holder)
                box.setChecked(True)                      # 默认全勾
                box.toggled.connect(self._sync_bot_rows)
                self._boxes[key] = box
                column.addWidget(_CheckRow(
                    program.name, box, indent=LEVEL_INDENT,
                    suffix="（主程序）" if program.role == "primary" else "",
                    parent=holder))

            bot_box.setChecked(bool(bot_keys))
            bot_box.setEnabled(bool(bot_keys))
            if bot_keys:
                bot_box.toggled.connect(
                    lambda checked, keys=bot_keys: self._set_group(keys, checked))
            self._bot_rows[bot.id] = (bot_box, bot_keys)

        # 机器人多的时候给个滚动区，别让菜单长过屏幕
        if len(self._bots) > 4:
            area = QScrollArea(self)
            area.setWidgetResizable(True)
            area.setFrameShape(QScrollArea.Shape.NoFrame)
            area.setMaximumHeight(LIST_MAX_HEIGHT)
            area.setWidget(holder)
            action = QWidgetAction(self)
            action.setDefaultWidget(area)
            self.addAction(action)
        else:
            action = QWidgetAction(self)
            action.setDefaultWidget(holder)
            self.addAction(action)

    def _build_footer(self) -> None:
        self.addSeparator()
        pick_all = QAction("全选", self)
        pick_all.triggered.connect(lambda: self._set_all(True))
        self.addAction(pick_all)
        clear = QAction("清空", self)
        clear.triggered.connect(lambda: self._set_all(False))
        self.addAction(clear)
        self.addSeparator()
        start = QAction("启动勾选的", self)
        start.triggered.connect(self._emit_start)
        self.addAction(start)
        self.start_action = start                 # 供外部（按钮）读取/触发

    # ------------------------------------------------------------------
    # 勾选联动
    # ------------------------------------------------------------------

    def _set_group(self, keys: List[str], checked: bool) -> None:
        """机器人那一行被勾/取消：它下面的程序一起变。"""
        for key in keys:
            box = self._boxes.get(key)
            if box is not None and box.isChecked() != bool(checked):
                box.blockSignals(True)
                box.setChecked(bool(checked))
                box.blockSignals(False)
        self._sync_bot_rows()

    def _set_all(self, checked: bool) -> None:
        for box in self._boxes.values():
            box.blockSignals(True)
            box.setChecked(checked)
            box.blockSignals(False)
        self._sync_bot_rows()

    def _sync_bot_rows(self) -> None:
        """子项变化后刷新父项的 勾选 / 半选 / 未勾 三态。"""
        for box, keys in self._bot_rows.values():
            checked = [self._boxes[key].isChecked() for key in keys if key in self._boxes]
            if not checked:
                continue
            state = Qt.CheckState.PartiallyChecked if any(checked) and not all(checked) \
                else (Qt.CheckState.Checked if all(checked) else Qt.CheckState.Unchecked)
            box.blockSignals(True)
            box.setCheckState(state)
            box.blockSignals(False)

    # ------------------------------------------------------------------
    # 结果
    # ------------------------------------------------------------------

    def selected_keys(self) -> List[str]:
        """勾选到的 manager key（保持界面顺序）。"""
        return [key for key in self._boxes if self._boxes[key].isChecked()]

    def focus_on(self, bot_id: str) -> None:
        """只勾这个机器人下面的程序，其它机器人不勾。

        从某个窗口的「启动全部 ▾」点开时的默认状态 —— 这样"点开直接确定"
        仍然等于"启动这个机器人"，和左边那个「启动全部」按钮语义一致。
        想跨机器人一起启动就点「全选」，或自己勾。
        """
        prefix = "{}:".format(bot_id or "")
        for key, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(bot_id) and key.startswith(prefix))
            box.blockSignals(False)
        self._sync_bot_rows()

    def _emit_start(self) -> None:
        keys = self.selected_keys()
        if keys:
            self.startRequested.emit(keys)
