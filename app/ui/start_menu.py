# -*- coding: utf-8 -*-
"""「启动全部」旁边的勾选菜单：挑这一次要启动哪些程序。

真机需求（2026-10-05）
----------------------
> 在「启动全部」那里加一个类似于「布局 ▾」的下拉栏，里面浅色字写"选择启动的程序"，
> 保留完整的实例框架……然后每个选项都有勾选框。勾选 ATRI bot，其下三个小程序默认勾选。

风格（真机二次反馈："感觉还是很违和，跟其他的下拉栏统一风格"）
--------------------------------------------------------------
第一版为了让勾选框在**右侧**，每一行都用 QWidgetAction 自己画（QWidget + QLabel +
QCheckBox）。功能对，但长相和「布局 ▾」「视图」那些菜单**不一样** —— 那些是菜单
**原生项**（同一个 QMenu、同一个勾选列、同一套行高与悬停高亮）。

所以这一版改成**纯原生 QAction**：

  · 勾选标记回到**菜单原生的左边那一列**（和「布局 ▾」里的"只看主程序"一模一样）；
  · 层级只用**缩进**表达（全角空格 U+3000，中文界面字体下宽度稳定）；
  · 机器人那一行点一下 = 这一组全选 / 全不选（子项没选全时父项不打勾）；
  · **点勾选行不关菜单**（想连勾三个不用开三次）—— 见 mouseReleaseEvent；
  · 不再自己画任何控件 —— 也因此不需要滚动区（菜单过长时 Qt 自己会滚）。
"""

from typing import Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QWidget

from app.process_manager import build_manager_key

#: 一级缩进（全角空格；普通空格在比例字体里太窄，看不出层级）
INDENT = "\u3000\u3000"

#: 标了这个属性的 action：点完**不关菜单**（见 mouseReleaseEvent）
KEEP_OPEN_FLAG = "keep_menu_open"


def startable_programs(bot) -> List:
    """机器人下**可以启动**的程序：主程序在前，其余保持配置顺序。

    与 BotTab._sorted_programs() 同一顺序，勾选列表和界面上看到的一致。
    """
    from app.config import ROLE_PRIMARY

    programs = [program for program in getattr(bot, "programs", []) if program.enabled]
    programs.sort(key=lambda item: 0 if item.role == ROLE_PRIMARY else 1)
    return programs


class StartSelectionMenu(QMenu):
    """挑程序的菜单：`selected_keys()` 返回这次要启动的 manager key 列表。"""

    #: 用户点了「启动勾选的」：参数是要启动的 manager key 列表（复用启动全部那条路径）
    startRequested = pyqtSignal(list)

    def __init__(self, bots, current_bot_id: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        #: manager key -> 程序行的 QAction
        self._actions: Dict[str, QAction] = {}
        #: bot_id -> (机器人行的 QAction, [它下面所有程序的 key])
        self._bot_rows: Dict[str, tuple] = {}
        #: bot_id -> 机器人名（改文本时要用，别从文本里反解析）
        self._bot_names: Dict[str, str] = {}
        self._current_bot_id = current_bot_id or ""
        self._bots = [bot for bot in bots if getattr(bot, "enabled", True)]

        self._build_header()
        self._build_tree()
        self._build_footer()
        self._sync_bot_rows()

    # ------------------------------------------------------------------
    # 结构（全部是原生菜单项，和「布局 ▾」同一个长相）
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        header = QAction("选择启动的程序", self)
        header.setEnabled(False)          # 和「布局 ▾」的"右侧分屏方式"一样当小标题
        self.addAction(header)
        self.addSeparator()

    def _build_tree(self) -> None:
        for bot in self._bots:
            programs = startable_programs(bot)
            bot_keys: List[str] = []

            bot_action = QAction(getattr(bot, "name", bot.id), self)
            bot_action.setCheckable(True)
            bot_action.setToolTip("点一下 = 这组全选 / 全不选")
            self.addAction(bot_action)
            self._bot_names[bot.id] = getattr(bot, "name", bot.id)

            for program in programs:
                key = build_manager_key(bot.id, program.id)
                bot_keys.append(key)
                label = INDENT + program.name
                if program.role == "primary":
                    label += "  （主程序）"
                action = QAction(label, self)
                action.setCheckable(True)
                action.setChecked(True)               # 默认全勾
                action.toggled.connect(self._sync_bot_rows)
                self.addAction(action)
                self._actions[key] = action

            if bot_keys:
                bot_action.toggled.connect(
                    lambda checked, keys=bot_keys: self._set_group(keys, checked))
            self._bot_rows[bot.id] = (bot_action, bot_keys)

    def _build_footer(self) -> None:
        self.addSeparator()
        pick_all = QAction("全选", self)
        pick_all.setProperty(KEEP_OPEN_FLAG, True)
        pick_all.triggered.connect(lambda: self._set_all(True))
        self.addAction(pick_all)
        clear = QAction("清空", self)
        clear.setProperty(KEEP_OPEN_FLAG, True)
        clear.triggered.connect(lambda: self._set_all(False))
        self.addAction(clear)
        self.addSeparator()
        #: 只有这一项点完关菜单（要的就是"选完就走"）
        start = QAction("启动勾选的", self)
        start.triggered.connect(self._emit_start)
        self.addAction(start)
        self.start_action = start

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """点勾选行 = 只切换勾选，**不关菜单**。

        真机反馈（2026-10-05）："选择时会出现点一下即下拉栏消失的情况" ——
        QMenu 的默认行为是"激活一个 action 就关菜单"，对勾选框来说很难用
        （想连勾三个得开三次）；更糟的是菜单关掉后这一下还可能**穿透**到
        下面的按钮上（用户看到的就是"点了就直接启动"）。

        所以这里拦下这一下：可勾选的行只 toggle、不激活、不关闭；
        「全选 / 清空」也留在菜单里（标了 KEEP_OPEN_FLAG）；
        其余项（启动勾选的）走默认行为，点完正常关闭。
        """
        action = self.activeAction()
        if action is not None and action.isEnabled() and (
                action.isCheckable() or action.property(KEEP_OPEN_FLAG)):
            action.trigger()          # 可勾选的会自己 toggle，并发出 toggled 信号
            event.accept()
            return                    # ← 不调用 super()：菜单保持打开
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # 勾选联动
    # ------------------------------------------------------------------

    def _set_group(self, keys: List[str], checked: bool) -> None:
        """机器人那一行被勾/取消：它下面的程序一起变。"""
        for key in keys:
            action = self._actions.get(key)
            if action is not None and action.isChecked() != bool(checked):
                action.blockSignals(True)
                action.setChecked(bool(checked))
                action.blockSignals(False)
        self._sync_bot_rows()

    def _set_all(self, checked: bool) -> None:
        for action in self._actions.values():
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
        self._sync_bot_rows()

    def _sync_bot_rows(self) -> None:
        """子项变化后刷新父项：勾选状态（全勾才打勾）+「（当前）」后缀。"""
        for bot_id, (action, keys) in self._bot_rows.items():
            states = [self._actions[key].isChecked()
                      for key in keys if key in self._actions]
            if not states:
                action.setEnabled(False)
                action.setChecked(False)
                continue
            chosen = sum(1 for item in states if item)
            current = "（当前）" if bot_id == self._current_bot_id else ""
            action.blockSignals(True)
            # 行尾不再写"已选 n/m"（真机反馈：多余）—— 勾选列本身就能看出全选与否；
            # 只勾了一部分时父项不打勾，这已经足够表达"这组没选全"。
            action.setText("{}{}".format(
                self._bot_names.get(bot_id, bot_id), current))
            action.setChecked(chosen == len(states))
            action.blockSignals(False)

    # ------------------------------------------------------------------
    # 结果
    # ------------------------------------------------------------------

    def selected_keys(self) -> List[str]:
        """勾选到的 manager key（保持界面顺序）。"""
        return [key for key in self._actions if self._actions[key].isChecked()]

    def focus_on(self, bot_id: str) -> None:
        """只勾这个机器人下面的程序，其它机器人不勾。

        从「启动全部 ▾」点开时的默认状态 —— 这样"点开直接确定"仍然等于
        "启动当前机器人"，和左边那个「启动全部」按钮语义一致。
        想跨机器人一起启动就点「全选」，或自己勾。
        """
        prefix = "{}:".format(bot_id or "")
        for key, action in self._actions.items():
            action.blockSignals(True)
            action.setChecked(bool(bot_id) and key.startswith(prefix))
            action.blockSignals(False)
        self._sync_bot_rows()

    def _emit_start(self) -> None:
        keys = self.selected_keys()
        if keys:
            self.startRequested.emit(keys)
