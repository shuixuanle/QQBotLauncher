# -*- coding: utf-8 -*-
"""新建 / 编辑机器人的对话框。

字段与结构
----------
机器人级：
    Bot 名称、是否自动选最新 jar（勾选后会同步到所有程序）、备注
程序列表（可动态增删）：
    名称、角色(primary/secondary)、工作目录（可浏览选择）、启动命令、
    环境变量（键值对表格）、启动延迟（秒）、是否自动选最新 jar、启用开关

保存
----
    dialog.accept() 后由调用方读取 dialog.bot() 取得结果；
    需要自己落盘时调用 dialog.apply_and_save()，会写入 bots_config.json。

用法
----
    dialog = EditBotDialog(parent=window, config=config, bot=some_bot)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        new_bot = dialog.bot()
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# 允许 "python app/ui/edit_bot_dialog.py" 直接运行自检
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import (  # noqa: E402
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    Bot,
    BotConfig,
    Program,
    new_id,
    role_display,
)
from app import layout as layout_model  # noqa: E402
from app.ui import theme as theme_tokens  # noqa: E402

#: 说明文字用的 objectName（主题切换时按它批量刷新，R2）
HINT_LABEL_NAME = "hintLabel"

#: 布局下拉里"不指定"那一项的显示文字（存 QSettings 时用空串表示）
LAYOUT_AUTO_LABEL = "自动（按程序数量决定）"

#: 布局下拉里可选的模板（顺序即下拉顺序；custom 不在其中 —— 它只能由拖拽产生）
LAYOUT_CHOICES = (
    layout_model.KIND_V,
    layout_model.KIND_H,
    layout_model.KIND_H_LEFT_V,
    layout_model.KIND_H_RIGHT_V,
    layout_model.KIND_TABS,
    layout_model.KIND_SINGLE,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ROLE_CHOICES = (
    (ROLE_PRIMARY, "主程序（primary）"),
    (ROLE_SECONDARY, "副程序（secondary）"),
)

#: 启动延迟上限（秒）
MAX_DELAY_SECONDS = 3600.0

#: 环境变量表格列
ENV_COL_KEY = 0
ENV_COL_VALUE = 1


# ---------------------------------------------------------------------------
# 通用控件
# ---------------------------------------------------------------------------

class DirectoryPicker(QWidget):
    """一行输入框 + 浏览按钮，用于选择目录。"""

    def __init__(
        self,
        text: str = "",
        parent: Optional[QWidget] = None,
        caption: str = "选择工作目录",
        placeholder: str = "可为空，默认为配置文件所在目录",
    ) -> None:
        super().__init__(parent)
        self._caption = caption

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.edit = QLineEdit(text, self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)

        self.button = QPushButton("浏览…", self)
        self.button.setToolTip("选择工作目录")
        self.button.clicked.connect(self._browse)

        row.addWidget(self.edit, 1)
        row.addWidget(self.button, 0)

    def text(self) -> str:
        """当前填写的目录（原样，未做绝对化）。"""
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802 (Qt 风格命名)
        self.edit.setText(value or "")

    def _browse(self) -> None:
        current = self.text()
        start_dir = current if current and os.path.isdir(current) else os.getcwd()
        chosen = QFileDialog.getExistingDirectory(self, self._caption, start_dir)
        if chosen:
            self.edit.setText(os.path.normpath(chosen))


class EnvEditor(QWidget):
    """环境变量编辑器：两列表格 + 增删按钮。"""

    def __init__(self, env: Optional[Dict[str, str]] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["变量名", "值"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(ENV_COL_KEY, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(ENV_COL_VALUE, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(110)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)

        self.add_button = QPushButton("添加变量", self)
        self.add_button.clicked.connect(self._add_row)
        self.remove_button = QPushButton("删除选中", self)
        self.remove_button.clicked.connect(self.remove_selected)
        hint = QLabel("示例：JAVA_TOOL_OPTIONS = -Dfile.encoding=UTF-8", self)
        hint.setObjectName(HINT_LABEL_NAME)
        hint.setStyleSheet(theme_tokens.dialog_hint_qss(hint))

        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(hint)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.set_env(env or {})

    # ---- 数据接口 ----

    def env(self) -> Dict[str, str]:
        """读取为字典（忽略空变量名，后面的同名变量覆盖前面的）。"""
        result: Dict[str, str] = {}
        for row in range(self.table.rowCount()):
            key_item = self.table.item(row, ENV_COL_KEY)
            value_item = self.table.item(row, ENV_COL_VALUE)
            key = key_item.text().strip() if key_item is not None else ""
            if not key:
                continue
            value = value_item.text() if value_item is not None else ""
            result[key] = value
        return result

    def set_env(self, env: Dict[str, str]) -> None:
        """写入数据（会清空原有内容）。"""
        self.table.setRowCount(0)
        for key, value in (env or {}).items():
            self._append_row(str(key), "" if value is None else str(value))

    # ---- 行操作 ----

    def _append_row(self, key: str = "", value: str = "") -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, ENV_COL_KEY, QTableWidgetItem(key))
        self.table.setItem(row, ENV_COL_VALUE, QTableWidgetItem(value))
        return row

    def _add_row(self) -> None:
        row = self._append_row("", "")
        self.table.setCurrentCell(row, ENV_COL_KEY)
        item = self.table.item(row, ENV_COL_KEY)
        if item is not None:
            self.table.editItem(item)

    def remove_selected(self) -> None:
        """删除选中的变量行（从下往上删，避免行号错位）。"""
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.rowCount():
            rows = [self.table.rowCount() - 1]
        for row in rows:
            self.table.removeRow(row)


# ---------------------------------------------------------------------------
# 单个程序的编辑页
# ---------------------------------------------------------------------------

class ProgramEditor(QWidget):
    """一个程序的编辑表单。"""

    changed = pyqtSignal()

    def __init__(self, program: Optional[Program] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.program: Program = program.copy() if program is not None else Program()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        form_box = QGroupBox("程序设置", self)
        form = QFormLayout(form_box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        # 名称
        self.name_edit = QLineEdit(self.program.name, form_box)
        self.name_edit.setPlaceholderText("例如：主程序 / 签名服务")
        self.name_edit.textChanged.connect(self._on_changed)
        form.addRow("程序名称：", self.name_edit)

        # 角色
        self.role_combo = QComboBox(form_box)
        for value, label in ROLE_CHOICES:
            self.role_combo.addItem(label, value)
        self.role_combo.setCurrentIndex(
            0 if self.program.role == ROLE_PRIMARY else 1
        )
        self.role_combo.setToolTip("primary 为机器人主体，secondary 为辅助程序")
        self.role_combo.currentIndexChanged.connect(self._on_changed)
        form.addRow("角色：", self.role_combo)

        # 工作目录
        self.cwd_picker = DirectoryPicker(self.program.cwd, form_box)
        self.cwd_picker.edit.textChanged.connect(self._on_changed)
        form.addRow("工作目录：", self.cwd_picker)

        # 启动命令
        self.command_edit = QLineEdit(self.program.command, form_box)
        self.command_edit.setPlaceholderText("例如：java -jar [LATEST_JAR]  或  python main.py")
        self.command_edit.setToolTip(
            "命令中可用 [LATEST_JAR] 表示工作目录下最新的 jar 包；\n"
            "也支持 {jar} 写法。勾选「自动选最新 jar」后会自动替换。"
        )
        self.command_edit.textChanged.connect(self._on_changed)
        form.addRow("启动命令：", self.command_edit)

        # 启动延迟
        self.delay_spin = QDoubleSpinBox(form_box)
        self.delay_spin.setRange(0.0, MAX_DELAY_SECONDS)
        self.delay_spin.setDecimals(1)
        self.delay_spin.setSingleStep(1.0)
        self.delay_spin.setSuffix(" 秒")
        self.delay_spin.setValue(max(0.0, float(self.program.delay)))
        self.delay_spin.setToolTip("该程序相对同一机器人内其他程序的启动延迟")
        self.delay_spin.valueChanged.connect(self._on_changed)
        form.addRow("启动延迟：", self.delay_spin)

        # 自动选最新 jar
        self.auto_jar_box = QCheckBox("自动选择工作目录中最新的 jar 包", form_box)
        self.auto_jar_box.setChecked(bool(self.program.auto_latest_jar))
        self.auto_jar_box.setToolTip("按修改时间取最新的 .jar，替换命令中的 [LATEST_JAR]")
        self.auto_jar_box.toggled.connect(self._on_changed)
        form.addRow("", self.auto_jar_box)

        # 启用开关
        self.enabled_box = QCheckBox("启用该程序（取消勾选则整体启动时跳过）", form_box)
        self.enabled_box.setChecked(bool(self.program.enabled))
        self.enabled_box.toggled.connect(self._on_changed)
        form.addRow("", self.enabled_box)

        # 通过命令解释器启动（支持 && 、| 、> 等 cmd 语法）
        self.via_shell_box = QCheckBox(
            "通过命令解释器启动（支持 && 、| 、> ，如 chcp 65001 && yarn start）", form_box
        )
        self.via_shell_box.setChecked(bool(self.program.via_shell))
        self.via_shell_box.setToolTip(
            "勾选后整条命令交给 cmd.exe /c 执行；不勾选则按参数直接启动可执行文件。\n"
            "需要使用 && 、管道、重定向或 chcp 时必须勾选。"
        )
        self.via_shell_box.toggled.connect(self._on_changed)
        form.addRow("", self.via_shell_box)

        outer.addWidget(form_box)

        # 环境变量
        env_box = QGroupBox("环境变量", self)
        env_layout = QVBoxLayout(env_box)
        env_layout.setContentsMargins(8, 8, 8, 8)
        self.env_editor = EnvEditor(self.program.env, env_box)
        self.env_editor.table.itemChanged.connect(lambda _item: self._on_changed())
        env_layout.addWidget(self.env_editor)
        outer.addWidget(env_box)

        # 备注
        note_box = QGroupBox("备注（可选）", self)
        note_layout = QVBoxLayout(note_box)
        note_layout.setContentsMargins(8, 8, 8, 8)
        self.description_edit = QLineEdit(self.program.description, note_box)
        self.description_edit.setPlaceholderText("例如：主机器人本体，端口 8080")
        self.description_edit.textChanged.connect(self._on_changed)
        note_layout.addWidget(self.description_edit)
        outer.addWidget(note_box)

        outer.addStretch(1)

    # ---- 数据接口 ----

    def display_name(self) -> str:
        """列表里显示的名字。"""
        name = self.name_edit.text().strip() or "未命名程序"
        return "{}（{}）".format(name, role_display(self.current_role()))

    def current_role(self) -> str:
        """当前选择的角色。"""
        value = self.role_combo.currentData()
        return value if value in (ROLE_PRIMARY, ROLE_SECONDARY) else ROLE_SECONDARY

    def apply_to(self, program: Program) -> Program:
        """把界面上的内容写回 Program 对象（保持原 id 不变）。"""
        program.name = self.name_edit.text().strip() or "未命名程序"
        program.role = self.current_role()
        program.cwd = self.cwd_picker.text()
        program.command = self.command_edit.text().strip()
        program.env = self.env_editor.env()
        program.delay = float(self.delay_spin.value())
        program.auto_latest_jar = bool(self.auto_jar_box.isChecked())
        program.enabled = bool(self.enabled_box.isChecked())
        program.description = self.description_edit.text().strip()
        program.via_shell = bool(self.via_shell_box.isChecked())
        return program

    def result_program(self) -> Program:
        """返回当前编辑结果（不修改原始对象）。"""
        self.program = self.apply_to(self.program)
        return self.program.copy()

    def set_auto_latest_jar(self, value: bool) -> None:
        """批量同步「自动选最新 jar」；控件已被销毁时静默跳过。"""
        try:
            self.auto_jar_box.setChecked(bool(value))
        except RuntimeError:
            # 底层 C++ 对象已析构（例如对话框正在重建界面）
            pass

    def set_cwd(self, value: str) -> None:
        """设置工作目录（常与「统一设置目录」配合）。"""
        try:
            self.cwd_picker.setText(value)
        except RuntimeError:
            pass

    def _on_changed(self, *_args) -> None:
        self.changed.emit()


# ---------------------------------------------------------------------------
# 主对话框
# ---------------------------------------------------------------------------

class EditBotDialog(QDialog):
    """新建 / 编辑机器人的对话框。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        config: Optional[BotConfig] = None,
        bot: Optional[Bot] = None,
    ) -> None:
        super().__init__(parent)

        self.config = config
        self._editors: List[ProgramEditor] = []

        #: 源配置的副本：对话框内所有修改都发生在这个副本上，取消不会影响原对象
        source = bot.copy() if bot is not None else Bot(name="新机器人")
        self._source_bot = source
        self._bot_id = source.id
        #: True 表示这是一个尚未保存到配置里的新机器人
        self._is_new = bot is None or not self.is_in_config(source.id)

        self.setWindowTitle(
            "编辑机器人 - {}".format(source.name) if bot is not None else "新建机器人"
        )
        self.resize(880, 640)
        self.setMinimumSize(720, 520)

        self._build_ui(source)
        #: 打开对话框时的布局模板（用于判断下拉有没有被改过）
        self._initial_layout_kind = self.selected_layout_kind()
        self._sync_list_from_editors(select_row=0 if self._editors else -1)

    def is_in_config(self, bot_id: str) -> bool:
        """该 id 是否已存在于配置文件中（用于区分新建与编辑）。"""
        if self.config is None:
            return False
        return self.config.index_of(bot_id) >= 0

    def bot_id(self) -> str:
        """当前编辑的机器人 id（新建时在对话框构造时就已经生成）。

        布局模板要按 id 存进 QSettings，所以新建的机器人也有 id —— 即使它还
        没保存到配置里（未保存时写入的布局偏好，主窗口会在取消/删除时清掉）。
        """
        return self._bot_id or ""

    # ------------------------------------------------------------------
    # 布局模板（N4）—— 只写本机偏好 QSettings，不动 bots_config.json
    # ------------------------------------------------------------------

    def _layout_settings(self) -> QSettings:
        """本机界面偏好的 QSettings（与主窗口同一份，组织名/应用名一致）。"""
        return QSettings(theme_tokens.ORG_NAME, theme_tokens.APP_NAME)

    def _saved_layout_kind(self) -> str:
        """当前这个机器人已保存的布局模板（没有则空串）。"""
        if not self._bot_id:
            return ""
        try:
            value = self._layout_settings().value(
                layout_model.settings_layout_key(self._bot_id), ""
            )
        except (TypeError, ValueError):
            return ""
        return str(value or "").strip().lower()

    def _select_saved_layout(self) -> None:
        """把下拉选中项设为已保存的模板（找不到就选「自动」）。"""
        saved = self._saved_layout_kind()
        index = self.layout_combo.findData(saved) if saved else 0
        self.layout_combo.setCurrentIndex(index if index >= 0 else 0)

    def selected_layout_kind(self) -> str:
        """下拉当前选中的模板名（「自动」返回空串）。"""
        data = self.layout_combo.currentData()
        return str(data or "").strip().lower()

    def save_layout_preference(self) -> bool:
        """把下拉选择写进 QSettings（返回是否成功）。

        语义：
          · 选「自动」→ **删掉**模板键与自定义树键（回到默认布局）
          · 选模板   → 写模板键，并删掉自定义树键（模板与自定义互斥）
          · 选自定义（若下拉里有）→ 只保留 flag，树不动

        只在对话框被「保存」时调用，取消不会留下痕迹。
        """
        if not self._bot_id:
            return False
        try:
            settings = self._layout_settings()
            kind = self.selected_layout_kind()
            key_layout = layout_model.settings_layout_key(self._bot_id)
            key_tree = layout_model.settings_tree_key(self._bot_id)
            if not kind:
                settings.remove(key_layout)
                settings.remove(key_tree)
            elif kind == layout_model.KIND_CUSTOM:
                settings.setValue(key_layout, layout_model.KIND_CUSTOM)
            else:
                settings.setValue(key_layout, kind)
                # 模板布局不再需要自定义树（与主窗口 _remember_layout_kind 一致）
                settings.remove(key_tree)
            settings.sync()
            return True
        except (TypeError, ValueError):
            return False

    def layout_template_applied(self) -> bool:
        """下拉选择是否与保存前的值不同（主窗口据此决定要不要实时改已开窗口）。"""
        return self.selected_layout_kind() != self._initial_layout_kind

    def apply_layout_to_config(self) -> bool:
        """（占位）兼容钩子：布局写在 QSettings，不需要改配置对象。

        保留这个方法是为了让调用方明确"布局不进 bots_config.json"，
        将来若要改成"配置里也存一份出厂默认"，只需要在这里补写。
        """
        return False

    # ------------------------------------------------------------------
    # 主题（R2）
    # ------------------------------------------------------------------

    def apply_theme(self) -> None:
        """主题切换时刷新本对话框里的说明文字。

        说明文字是写死的灰色字符串，不会自己跟着调色板变；这里按 objectName
        把所有说明标签重刷一遍（EnvEditor 里的那个也会被 findChildren 找到）。
        """
        labels = []
        try:
            labels = self.findChildren(QLabel, HINT_LABEL_NAME)
        except (RuntimeError, AttributeError, TypeError):
            labels = []
        for label in labels:
            try:
                label.setStyleSheet(theme_tokens.dialog_hint_qss(label))
            except (RuntimeError, AttributeError):
                continue

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_ui(self, source: Bot) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ---- 机器人基本信息 ----
        bot_box = QGroupBox("机器人", self)
        bot_form = QFormLayout(bot_box)
        bot_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.bot_name_edit = QLineEdit(source.name, bot_box)
        self.bot_name_edit.setPlaceholderText("例如：主号机器人")
        bot_form.addRow("Bot 名称：", self.bot_name_edit)

        self.qq_edit = QLineEdit(source.qq, bot_box)
        self.qq_edit.setPlaceholderText("可选，例如：123456789")
        bot_form.addRow("QQ 号：", self.qq_edit)

        self.bot_auto_jar_box = QCheckBox("默认对所有程序自动选择最新 jar", bot_box)
        self.bot_auto_jar_box.setChecked(
            bool(source.programs) and all(item.auto_latest_jar for item in source.programs)
        )
        self.bot_auto_jar_box.setToolTip("勾选后会同步到下方所有程序；之后仍可单独调整")
        self.bot_auto_jar_box.toggled.connect(self._on_bot_auto_jar_toggled)
        bot_form.addRow("", self.bot_auto_jar_box)

        self.bot_enabled_box = QCheckBox("启用该机器人", bot_box)
        self.bot_enabled_box.setChecked(bool(source.enabled))
        bot_form.addRow("", self.bot_enabled_box)

        # ---- 布局模板（N4）----
        #     只写本机偏好（QSettings 的 pane/layout/<bot_id>），
        #     与「视图 → 分屏布局」菜单同一份记忆，不改 bots_config.json 格式。
        self.layout_combo = QComboBox(bot_box)
        self.layout_combo.addItem(LAYOUT_AUTO_LABEL, "")
        for kind in LAYOUT_CHOICES:
            self.layout_combo.addItem(
                layout_model.LAYOUT_KIND_LABELS.get(kind, kind), kind
            )
        # 「自定义」只在已有自定义树时出现（模板下拉里不提供）
        if self._saved_layout_kind() == layout_model.KIND_CUSTOM:
            self.layout_combo.addItem(
                layout_model.LAYOUT_KIND_LABELS.get(layout_model.KIND_CUSTOM,
                                                    layout_model.KIND_CUSTOM),
                layout_model.KIND_CUSTOM,
            )
        self._select_saved_layout()
        self.layout_combo.setToolTip(
            "打开这个机器人的窗口时用哪种分屏。\n"
            "选「自动」＝按程序数量决定（1 个单窗格 / 2 个上下分 / 3 个以上主程序在上）。\n"
            "这项是**本机界面偏好**，存在注册表里，不会写进 bots_config.json。"
        )
        bot_form.addRow("布局模板：", self.layout_combo)

        self.layout_hint = QLabel(
            "布局模板只影响这台机器上的显示方式；换机器或删掉注册表里的 "
            "pane/layout 键就回到「自动」。",
            bot_box,
        )
        self.layout_hint.setObjectName(HINT_LABEL_NAME)
        self.layout_hint.setWordWrap(True)
        self.layout_hint.setStyleSheet(theme_tokens.dialog_hint_qss(self.layout_hint))
        bot_form.addRow("", self.layout_hint)

        layout.addWidget(bot_box)

        # ---- 程序列表 + 编辑区 ----
        split_row = QHBoxLayout()
        split_row.setSpacing(8)

        # 左：程序列表
        list_column = QVBoxLayout()
        list_column.setSpacing(4)
        list_label = QLabel("程序列表（一个机器人可包含多个程序）", self)
        list_column.addWidget(list_label)

        self.program_list = QListWidget(self)
        self.program_list.setMinimumWidth(210)
        self.program_list.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.program_list.currentRowChanged.connect(self._on_program_selected)
        list_column.addWidget(self.program_list, 1)

        list_buttons = QHBoxLayout()
        list_buttons.setSpacing(4)
        self.add_program_button = QPushButton("添加", self)
        self.add_program_button.setToolTip("新增一个程序")
        self.add_program_button.clicked.connect(self.add_program)
        self.remove_program_button = QPushButton("删除", self)
        self.remove_program_button.setToolTip("删除当前选中的程序")
        self.remove_program_button.clicked.connect(self.remove_current_program)
        list_buttons.addWidget(self.add_program_button)
        list_buttons.addWidget(self.remove_program_button)
        list_column.addLayout(list_buttons)

        order_buttons = QHBoxLayout()
        order_buttons.setSpacing(4)
        self.up_button = QPushButton("上移", self)
        self.up_button.clicked.connect(lambda: self.move_current_program(-1))
        self.down_button = QPushButton("下移", self)
        self.down_button.clicked.connect(lambda: self.move_current_program(1))
        order_buttons.addWidget(self.up_button)
        order_buttons.addWidget(self.down_button)
        list_column.addLayout(order_buttons)

        split_row.addLayout(list_column)

        # 右：当前程序的编辑表单（可滚动）
        self.editor_area = QScrollArea(self)
        self.editor_area.setWidgetResizable(True)
        self.editor_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._editor_host = QWidget(self.editor_area)
        self._editor_host_layout = QVBoxLayout(self._editor_host)
        self._editor_host_layout.setContentsMargins(0, 0, 0, 0)
        self._editor_host_layout.setSpacing(6)
        self._editor_host_layout.addStretch(1)
        self.editor_area.setWidget(self._editor_host)
        split_row.addWidget(self.editor_area, 1)

        layout.addLayout(split_row, 1)

        # ---- 底部按钮 ----
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_button is not None:
            save_button.setText("保存")
            save_button.setDefault(True)
        cancel_button = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setText("取消")
        self.button_box.accepted.connect(self._on_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        hint = QLabel(
            "提示：命令中的 [LATEST_JAR] 会被替换为工作目录下修改时间最新的 .jar；"
            "相对路径以 bots_config.json 所在目录为基准。",
            self,
        )
        hint.setObjectName(HINT_LABEL_NAME)
        hint.setWordWrap(True)
        hint.setStyleSheet(theme_tokens.dialog_hint_qss(hint))
        layout.addWidget(hint)

        # 载入已有程序
        for program in source.programs:
            self._append_editor(program)

    # ------------------------------------------------------------------
    # 程序列表操作
    # ------------------------------------------------------------------

    def _append_editor(self, program: Optional[Program]) -> ProgramEditor:
        editor = ProgramEditor(program, self._editor_host)
        editor.changed.connect(lambda ed=editor: self._sync_list_item(ed))
        self._editors.append(editor)
        self._editor_host_layout.insertWidget(self._editor_host_layout.count() - 1, editor)
        editor.setVisible(False)
        return editor

    def add_program(self) -> None:
        """新增一个程序（默认副程序）。"""
        program = Program(
            id=new_id("prog"),
            name="副程序{}".format(len(self._editors)),
            role=ROLE_SECONDARY,
            cwd=self._default_cwd(),
            command="",
            delay=0.0,
            auto_latest_jar=bool(self.bot_auto_jar_box.isChecked()),
        )
        editor = self._append_editor(program)
        self._sync_list_from_editors(select_row=len(self._editors) - 1)
        editor.name_edit.setFocus()
        editor.name_edit.selectAll()

    def _default_cwd(self) -> str:
        """新程序的默认工作目录：沿用最后一个已填写的目录。"""
        for editor in reversed(self._editors):
            value = editor.cwd_picker.text()
            if value:
                return value
        return ""

    def remove_current_program(self) -> None:
        """删除当前选中的程序（至少保留一个）。"""
        row = self.program_list.currentRow()
        if row < 0 or row >= len(self._editors):
            return
        if len(self._editors) <= 1:
            QMessageBox.information(self, "无法删除", "至少需要保留一个程序。")
            return
        editor = self._editors[row]
        name = editor.name_edit.text().strip() or "该程序"
        answer = QMessageBox.question(
            self,
            "删除程序",
            "确定删除「{}」吗？".format(name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._editors.pop(row)
        self._editor_host_layout.removeWidget(editor)
        editor.setParent(None)
        editor.deleteLater()
        self._sync_list_from_editors(select_row=min(row, len(self._editors) - 1))

    def move_current_program(self, offset: int) -> None:
        """上移 / 下移当前程序。"""
        row = self.program_list.currentRow()
        if row < 0 or row >= len(self._editors):
            return
        target = row + offset
        if target < 0 or target >= len(self._editors):
            return
        self._editors[row], self._editors[target] = self._editors[target], self._editors[row]
        self._sync_list_from_editors(select_row=target)

    def _on_program_selected(self, row: int) -> None:
        """切换程序时显示对应的编辑表单。"""
        for index, editor in enumerate(self._editors):
            editor.setVisible(index == row)
        self._update_buttons()

    def _sync_list_item(self, editor: ProgramEditor) -> None:
        """某个编辑表单内容变化时更新列表文字。"""
        row = self._row_of(editor)
        if row < 0:
            return
        item = self.program_list.item(row)
        if item is not None:
            item.setText(editor.display_name())

    def _sync_list_from_editors(self, select_row: int = -1) -> None:
        """按编辑表单重建列表。"""
        self.program_list.blockSignals(True)
        self.program_list.clear()
        for editor in self._editors:
            item = QListWidgetItem(editor.display_name())
            item.setToolTip(editor.command_edit.text().strip() or "未填写启动命令")
            self.program_list.addItem(item)
        self.program_list.blockSignals(False)

        if self._editors:
            if select_row < 0 or select_row >= len(self._editors):
                select_row = 0
            self.program_list.setCurrentRow(select_row)
            self._on_program_selected(select_row)
        else:
            self._on_program_selected(-1)
        self._update_buttons()

    def _row_of(self, editor: ProgramEditor) -> int:
        for index, item in enumerate(self._editors):
            if item is editor:
                return index
        return -1

    def _update_buttons(self) -> None:
        row = self.program_list.currentRow()
        has_current = 0 <= row < len(self._editors)
        self.remove_program_button.setEnabled(has_current and len(self._editors) > 1)
        self.up_button.setEnabled(has_current and row > 0)
        self.down_button.setEnabled(has_current and row < len(self._editors) - 1)

    def _on_bot_auto_jar_toggled(self, checked: bool) -> None:
        """机器人级「自动选最新 jar」同步到所有程序（单个失败不影响其它）。"""
        for editor in self._editors:
            try:
                editor.set_auto_latest_jar(checked)
            except RuntimeError:
                continue

    # ------------------------------------------------------------------
    # 校验与保存
    # ------------------------------------------------------------------

    def collect_bot(self) -> Bot:
        """把界面内容收集为 Bot 对象（不落盘）。"""
        bot = Bot(
            id=self._bot_id or new_id("bot"),
            name=self.bot_name_edit.text().strip() or "未命名机器人",
            qq=self.qq_edit.text().strip(),
            working_dir="",
            enabled=bool(self.bot_enabled_box.isChecked()),
            auto_start=False,
            description=self._bot_description,
            programs=[],
        )
        for editor in self._editors:
            program = editor.result_program()
            if not program.id:
                program.id = new_id("prog")
            bot.programs.append(program)
        bot.sort_programs()
        return bot

    def bot(self) -> Bot:
        """返回编辑结果（保存后同 collect_bot）。"""
        return self.collect_bot()

    def validate(self) -> List[str]:
        """返回阻断保存的问题列表。"""
        problems: List[str] = []
        if not self.bot_name_edit.text().strip():
            problems.append("请填写 Bot 名称。")
        if not self._editors:
            problems.append("请至少添加一个程序。")
        for index, editor in enumerate(self._editors, start=1):
            name = editor.name_edit.text().strip() or "程序{}".format(index)
            if not editor.command_edit.text().strip():
                problems.append("「{}」未填写启动命令。".format(name))
        return problems

    def warnings(self) -> List[str]:
        """返回不阻断保存的提醒（例如目录不存在）。"""
        notes: List[str] = []
        base_dir = self.config.base_dir if self.config is not None else None
        for editor in self._editors:
            name = editor.name_edit.text().strip() or "未命名程序"
            raw = editor.cwd_picker.text()
            if not raw:
                continue
            path = raw
            if base_dir is not None and not os.path.isabs(path):
                path = os.path.join(str(base_dir), path)
            if not os.path.isdir(path):
                notes.append("「{}」的工作目录不存在：{}".format(name, path))

        # 命令里的 \" 是"从别处复制带进来的多余转义"：cmd 与 PowerShell 都**不**认识它
        # （见 app/config.py 里 format_argv 的说明："绝不用 \"，因为 cmd 不认识反斜杠转义"），
        # 会被当成普通字符导致语法错误、进程以退出码 1 立即结束。
        # 真机事故：消防栓那条命令里的 `\"\"…\"\"` 就是这样导致"打开依旧不能实现"。
        for editor in self._editors:
            name = editor.name_edit.text().strip() or "未命名程序"
            command = editor.command_edit.text().strip()
            if '\\"' in command:
                notes.append(
                    "「{}」的启动命令里出现了反斜杠转义的反引号（\\\"），"
                    "cmd 与 PowerShell 都不认识这种写法，会当成普通字符导致语法错误。\n"
                    "    内层引号请用**两个双引号**表示，例如：\n"
                    '      cmd.exe /c ""C:\\路径 含空格\\x.bat""\n'
                    "    更省事的做法：把复杂命令写进 .bat 脚本，"
                    "命令只写 `cmd.exe /c x.bat`，再用「工作目录」指向脚本所在目录。".format(name)
                )
        return notes

    def _on_accept(self) -> None:
        """保存按钮：校验 -> 询问提醒 -> 落盘 -> 关闭。"""
        problems = self.validate()
        if problems:
            QMessageBox.warning(self, "配置不完整", "\n".join(problems))
            return

        notes = self.warnings()
        if notes:
            answer = QMessageBox.question(
                self,
                "目录提醒",
                "{}\n\n仍要保存吗？".format("\n".join(notes)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        if self.config is not None:
            ok, message = self.apply_and_save()
            if not ok:
                QMessageBox.critical(self, "保存失败", message or "写入 bots_config.json 失败。")
                return

        self.accept()

    def apply_and_save(self):
        """把结果写入 bots_config.json（新增或替换同名 id 的机器人）。

        返回 (是否成功, 错误信息)。未提供 config 时返回 (False, 提示)。

        N4：同时把「布局模板」下拉的选择写进**本机偏好**（QSettings 的
        `pane/layout/<bot_id>`），与「视图 → 分屏布局」菜单共用同一份记忆；
        `bots_config.json` 的格式**不变**（布局跟着机器走，不跟着配置走）。
        """
        if self.config is None:
            return False, "未提供 BotConfig，无法写入 bots_config.json。"

        bot = self.collect_bot()
        index = self.config.index_of(bot.id)
        if index >= 0:
            self.config.bots[index] = bot
        else:
            self.config.add_bot(bot)
        ok, message = self.config.save()
        if ok:
            # 配置写成功后再写本机偏好，避免"配置没存上但布局改了"
            self.save_layout_preference()
            self._initial_layout_kind = self.selected_layout_kind()
        return ok, message


# ---------------------------------------------------------------------------
# 自检：可用 QT_QPA_PLATFORM=offscreen 无界面运行
# ---------------------------------------------------------------------------

def _demo_config() -> BotConfig:
    base = os.path.join(os.getcwd(), "bots_config.json")
    config = BotConfig.from_dict({"version": 1, "bots": []}, path=base)
    return config


def _demo_bot() -> Bot:
    bot = Bot(id=new_id("bot"), name="演示机器人", qq="123456")
    bot.programs.append(
        Program(
            id=new_id("prog"),
            name="主程序",
            role=ROLE_PRIMARY,
            cwd="bots/example",
            command="java -jar [LATEST_JAR]",
            env={"JAVA_TOOL_OPTIONS": "-Dfile.encoding=UTF-8"},
            delay=0.0,
            auto_latest_jar=True,
        )
    )
    bot.programs.append(
        Program(
            id=new_id("prog"),
            name="副程序",
            role=ROLE_SECONDARY,
            cwd="bots/example",
            command="python plugin_host.py --port 8080",
            env={"PYTHONIOENCODING": "utf-8"},
            delay=5.0,
            auto_latest_jar=False,
        )
    )
    bot.sort_programs()
    return bot


def _selftest() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    config = _demo_config()

    # 1) 载入已有机器人
    dialog = EditBotDialog(None, config, _demo_bot())
    print("[1] 程序数 =", len(dialog._editors), "| 列表行数 =", dialog.program_list.count())
    assert len(dialog._editors) == 2
    assert dialog.program_list.count() == 2
    assert dialog.program_list.currentRow() == 0

    # 2) 新增 / 删除程序
    dialog.add_program()
    assert len(dialog._editors) == 3
    assert dialog.program_list.count() == 3
    assert dialog.program_list.currentRow() == 2
    dialog.program_list.setCurrentRow(2)
    # 直接删除（跳过确认框）：临时替换确认函数
    original_question = QMessageBox.question
    QMessageBox.question = staticmethod(
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    try:
        dialog.remove_current_program()
    finally:
        QMessageBox.question = original_question
    assert len(dialog._editors) == 2, len(dialog._editors)

    # 3) 字段收集
    result = dialog.collect_bot()
    print("[3] Bot =", result.name, "| 程序 =",
          [(item.name, item.role, item.delay, item.auto_latest_jar) for item in result.programs])
    assert result.name == "演示机器人"
    assert result.primary_program is not None
    assert result.primary_program.command == "java -jar [LATEST_JAR]"
    assert result.programs[1].env.get("PYTHONIOENCODING") == "utf-8"

    # 4) 机器人级开关同步到所有程序
    dialog.bot_auto_jar_box.setChecked(False)
    dialog.bot_auto_jar_box.setChecked(True)
    assert all(editor.auto_jar_box.isChecked() for editor in dialog._editors)

    # 5) 校验规则
    dialog.bot_name_edit.setText("")
    assert dialog.validate(), "空名称应报错"
    dialog.bot_name_edit.setText("演示机器人")
    for editor in dialog._editors:
        editor.command_edit.setText("")
    assert len(dialog.validate()) == len(dialog._editors)
    print("[5] 校验问题数 =", len(dialog.validate()))

    # 6) 写入 bots_config.json（写到临时路径，避免污染项目配置）
    config.path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_selftest_bots_config.json"
    )
    ok, message = dialog.apply_and_save()
    print("[6] 保存结果 =", ok, message)
    assert ok, message
    assert os.path.isfile(str(config.path))
    reloaded = BotConfig.load(config.path, create_if_missing=False)
    assert len(reloaded.bots) == 1
    assert len(reloaded.bots[0].programs) == 2
    os.remove(str(config.path))

    # 7) 布局模板下拉（N4）：只写 QSettings，不改配置格式
    print("[7] 布局模板下拉")
    settings = dialog._layout_settings()
    key_layout = layout_model.settings_layout_key(dialog.bot_id())
    key_tree = layout_model.settings_tree_key(dialog.bot_id())
    keep_layout = settings.value(key_layout, None)
    keep_tree = settings.value(key_tree, None)
    try:
        # ① 下拉条目：「自动」+ 6 个模板（没有自定义树时不出现 custom）
        labels = [dialog.layout_combo.itemText(i) for i in range(dialog.layout_combo.count())]
        print("    下拉条目 =", labels)
        assert labels[0] == LAYOUT_AUTO_LABEL
        assert len(labels) == 1 + len(LAYOUT_CHOICES), labels
        assert layout_model.KIND_CUSTOM not in [
            dialog.layout_combo.itemData(i) for i in range(dialog.layout_combo.count())
        ], "没有自定义树时不该出现 custom"

        # ② 选一个模板 → 保存 → 注册表里有它、且没有自定义树
        dialog.layout_combo.setCurrentIndex(
            dialog.layout_combo.findData(layout_model.KIND_H_RIGHT_V)
        )
        assert dialog.selected_layout_kind() == layout_model.KIND_H_RIGHT_V
        assert dialog.layout_template_applied() is True, "改过下拉应报告已变更"
        settings.setValue(key_tree, "{\"type\":\"pane\"}")  # 先塞个自定义树
        assert dialog.save_layout_preference() is True
        saved = settings.value(key_layout)
        print("    选模板后注册表 = {!r} | 自定义树 = {!r}".format(
            saved, settings.value(key_tree)))
        assert str(saved) == layout_model.KIND_H_RIGHT_V
        assert settings.value(key_tree) is None, "选模板时必须清掉自定义树"
        assert dialog.layout_template_applied() is False, "保存后应视为已同步"

        # ③ 选回「自动」→ 两个键都该被删掉
        dialog.layout_combo.setCurrentIndex(0)
        assert dialog.selected_layout_kind() == ""
        assert dialog.save_layout_preference() is True
        print("    选自动后注册表 = {!r} | 自定义树 = {!r}".format(
            settings.value(key_layout), settings.value(key_tree)))
        assert settings.value(key_layout) is None
        assert settings.value(key_tree) is None

        # ④ 配置格式不变：布局不写进 bots_config.json
        payload = json.dumps(reloaded.to_dict(), ensure_ascii=False)
        assert "layout" not in payload, "布局不该进 bots_config.json"
        print("    bots_config.json 中无 layout 字段 = True")
    finally:
        if keep_layout is None:
            settings.remove(key_layout)
        else:
            settings.setValue(key_layout, keep_layout)
        if keep_tree is None:
            settings.remove(key_tree)
        else:
            settings.setValue(key_tree, keep_tree)
        settings.sync()

    print("自检通过：载入/增删程序、字段收集、开关同步、校验、落盘、布局模板均正常。")
    dialog.close()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
