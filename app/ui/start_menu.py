# -*- coding: utf-8 -*-
"""「启动全部」旁边的勾选菜单：挑这一次要启动哪些程序。

需求与两次外观调整（都在真机上验过）
------------------------------------
1. 首次："在「启动全部」那里加一个类似于「布局 ▾」的下拉栏，里面浅色字写
   选择启动的程序，保留完整的实例框架，每个选项都有勾选框；勾 ATRI bot 则其下三个
   小程序默认一起勾上。"
2. 二次："感觉还是很违和，跟其他的下拉栏统一风格" —— 第一版用自绘控件把勾选框放在
   右侧，长相和其它菜单不一样 → 改成**菜单原生项**。
3. 三次："点击后直接会启动所有点击前的上面勾选的程序"（菜单关闭那一下**穿透**到下面的
   按钮上）、"点一下即下拉栏消失"（QMenu 默认激活即关闭，勾选框很难用）、
   "「已选 3/3」多余"、"前面或者后面能画个框更好，不让 √ 这么突兀"。

最终形态
--------
  · 纯原生 QAction（和「布局 ▾」同一个长相、同一套行高与悬停高亮）；
  · 勾选状态用**文字方块**：`■` 选中 / `□` 未选 —— 等宽方块天然对齐、GBK 安全，
    而且和左栏的 ■ / ★ / ● 是同一套图标语言；不用菜单原生的 ✓，也就没有"很突兀"的勾；
  · 层级只用缩进（全角空格），机器人那一行点一下 = 整组切换；
  · **点勾选行不关菜单**（见 mouseReleaseEvent），点「启动勾选的」才关。
"""

from typing import Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QWidget

from app.process_manager import build_manager_key

#: 一级缩进（全角空格；普通空格在比例字体里太窄，看不出层级）
INDENT = "\u3000\u3000"

#: 勾选框：等宽方块（GBK 安全，且与左栏的 ■ / ★ / ● 同一套图标语言）
BOX_ON = "\u25a0"       # ■ 选中
BOX_OFF = "\u25a1"      # □ 未选中

#: action 标记：点完**不关菜单**（勾选行 / 全选 / 清空）
KEEP_OPEN_FLAG = "keep_menu_open"
#: action 标记：这一行对应哪个程序 key / 哪个机器人
ROW_KEY = "row_key"
ROW_BOT = "row_bot_id"
#: 主程序后缀（放在 action 的 tooltip 上做标记，改文字时要拼回去）
PRIMARY_HINT = "主程序"


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
        #: manager key -> 勾选状态（**自己记**：action 不设 checkable，免得出现原生 ✓）
        self._checked: Dict[str, bool] = {}
        #: manager key -> 程序行 QAction（改文字用）
        self._rows: Dict[str, QAction] = {}
        #: manager key -> 程序名（不靠拆 action 文字反解析）
        self._names: Dict[str, str] = {}
        #: manager key -> 是否主程序
        self._primary: Dict[str, bool] = {}
        #: bot_id -> (机器人行 QAction, 子项 key 列表, 机器人名)
        self._bot_rows: Dict[str, tuple] = {}
        self._current_bot_id = current_bot_id or ""
        self._bots = [bot for bot in bots if getattr(bot, "enabled", True)]

        self._build_header()
        self._build_tree()
        self._build_footer()
        self._refresh_rows()

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
            bot_name = getattr(bot, "name", bot.id)
            programs = startable_programs(bot)
            bot_keys: List[str] = []

            bot_action = QAction(bot_name, self)
            bot_action.setProperty(ROW_BOT, bot.id)
            bot_action.setToolTip("点一下 = 这一组全选 / 全不选")
            self.addAction(bot_action)

            for program in programs:
                key = build_manager_key(bot.id, program.id)
                bot_keys.append(key)
                self._checked[key] = True          # 默认全勾
                self._names[key] = program.name
                is_primary = program.role == "primary"
                self._primary[key] = is_primary
                action = QAction(INDENT + program.name, self)
                action.setProperty(ROW_KEY, key)
                if is_primary:
                    action.setToolTip(PRIMARY_HINT)
                self.addAction(action)
                self._rows[key] = action

            self._bot_rows[bot.id] = (bot_action, bot_keys, bot_name)

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

    # ------------------------------------------------------------------
    # 文字刷新（方块 + 名字 + 后缀）
    # ------------------------------------------------------------------

    def _refresh_rows(self) -> None:
        """按当前勾选状态重写每一行文字：`■/□` + 名字（+ 「（当前）」/「（主程序）」）。"""
        for bot_id, (action, keys, name) in self._bot_rows.items():
            states = [self._checked.get(key, False) for key in keys]
            if not states:
                action.setText("{}  {}".format(BOX_OFF, name))
                action.setEnabled(False)
                continue
            action.setEnabled(True)
            current = "（当前）" if bot_id == self._current_bot_id else ""
            action.setText("{}  {}{}".format(
                BOX_ON if all(states) else BOX_OFF, name, current))
        for key, action in self._rows.items():
            label = INDENT + self._names.get(key, key)
            if self._primary.get(key):
                label += "  （{}）".format(PRIMARY_HINT)
            action.setText("{} {}".format(
                BOX_ON if self._checked.get(key) else BOX_OFF, label))

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """点勾选行 = 只切换勾选，**不关菜单**。

        真机反馈："选择时会出现点一下即下拉栏消失的情况" —— QMenu 的默认行为是
        "激活一个 action 就关菜单"，对勾选框来说很难用（想连勾三个得开三次）；
        更糟的是菜单关掉后这一下还可能**穿透**到下面的按钮上（用户看到的就是
        "点了就自己启动了"）。

        所以这里拦下这一下：
          · 程序行 / 机器人行 —— 只改勾选，菜单保持打开；
          · 「全选 / 清空」    —— 标了 KEEP_OPEN_FLAG，也留在菜单里；
          · 「启动勾选的」     —— 走默认行为，点完正常关闭。
        """
        action = self.activeAction()
        if action is not None and action.isEnabled():
            key = action.property(ROW_KEY)
            bot_id = action.property(ROW_BOT)
            if key:
                self._checked[key] = not self._checked.get(key, False)
                self._refresh_rows()
                event.accept()
                return
            if bot_id:
                self._toggle_group(bot_id)
                event.accept()
                return
            if action.property(KEEP_OPEN_FLAG):
                action.trigger()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _toggle_group(self, bot_id: str) -> None:
        """机器人那一行：整组在"全选 / 全不选"之间切。"""
        entry = self._bot_rows.get(bot_id)
        if entry is None:
            return
        _action, keys, _name = entry
        target = not all(self._checked.get(key, False) for key in keys)
        for key in keys:
            self._checked[key] = target
        self._refresh_rows()

    def _set_all(self, checked: bool) -> None:
        for key in self._checked:
            self._checked[key] = bool(checked)
        self._refresh_rows()

    # ------------------------------------------------------------------
    # 结果
    # ------------------------------------------------------------------

    def selected_keys(self) -> List[str]:
        """勾选到的 manager key（保持界面顺序）。"""
        return [key for key in self._rows if self._checked.get(key)]

    def focus_on(self, bot_id: str) -> None:
        """只勾这个机器人下面的程序，其它机器人不勾。

        从「启动全部 ▾」点开时的默认状态 —— 这样"点开直接确定"仍然等于
        "启动当前机器人"，和左边那个「启动全部」按钮语义一致。
        想跨机器人一起启动就点「全选」，或自己勾。
        """
        prefix = "{}:".format(bot_id or "")
        for key in self._checked:
            self._checked[key] = bool(bot_id) and key.startswith(prefix)
        self._refresh_rows()

    def _emit_start(self) -> None:
        keys = self.selected_keys()
        if keys:
            self.startRequested.emit(keys)
