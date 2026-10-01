# -*- coding: utf-8 -*-
"""分屏布局模型（步骤 1，纯 Python，不依赖 PyQt6）。

用途
----
一个机器人有多个程序时，右侧"实例区"可以按用户偏好切成若干窗格：每个窗格显示
某一个程序（或若干程序做成子标签）的 cmd / 终端实时输出。本模块只负责**布局本身**：

    * 布局的树形数据结构            PaneNode（split / pane）
    * 六种内置模板                  single / v / h / h_left_v / h_right_v / tabs
    * 与 JSON 的互转（QSettings 持久化用）
    * "程序对账"                    reconcile()：增删程序后自动修正布局，绝不丢程序
    * 描述与推断                    describe_tree() / layout_kind_of()

约定
----
1. 叶子一律是 **pane**（含 1 或 N 个程序 + 当前选中项），分支是 **split**（含方向与比例）。
   这样"一个窗格 = 一份日志视图"的语义在代码里处处一致。
2. orientation：``"h"`` = 左右分（splitter 横向），``"v"`` = 上下分（splitter 纵向）。
3. 程序用 **程序 id** 引用（不是 manager key），与 bots_config.json 保持一致；
   布局自身**不落配置文件**，属于本机界面偏好，由 QSettings 保存。
4. 本模块是纯函数库：不导入 Qt、不读写文件、不抛异常给调用方（非法输入一律降级）。

术语对照
--------
    pane（窗格）  = 右侧一块窗口区域
    split（分割） = 把一块区域按 h/v 切成两块或多块
    tabs（子标签）= 一个窗格内放多个程序，用标签页切换
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "LAYOUT_KINDS",
    "LAYOUT_KIND_LABELS",
    "MAX_DEPTH",
    "DEFAULT_CHILD_RATIO",
    "MAX_TABS_PER_PANE",
    "PaneNode",
    "KIND_SINGLE",
    "KIND_V",
    "KIND_H",
    "KIND_H_LEFT_V",
    "KIND_H_RIGHT_V",
    "KIND_TABS",
    "KIND_CUSTOM",
    "SETTINGS_PANE_LAYOUT",
    "SETTINGS_PANE_TREE",
    "SETTINGS_PANE_SIZES",
    "SETTINGS_PANE_FOCUS",
    "SETTINGS_PANE_PREFIXES",
    "build_default_tree",
    "build_tree",
    "reconcile",
    "layout_kind_of",
    "describe_tree",
    "programs_in_tree",
    "pane_count",
    "pane_for_program",
    "first_pane",
    "collect_panes",
    "clone_tree",
    "sanitize_tree",
    "normalize_orientation",
    "tree_from_dict",
    "tree_to_dict",
    "tree_to_json",
    "tree_from_json",
    "settings_layout_key",
    "settings_tree_key",
    "settings_sizes_key",
    "settings_focus_key",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 一个窗格最多显示的标签数（超过后只影响可读性，不影响功能）
MAX_TABS_PER_PANE = 12

#: 布局树最大深度（防止异常数据把递归搞崩）
MAX_DEPTH = 6

#: 节点类型
TYPE_SPLIT = "split"
TYPE_PANE = "pane"

#: 方向：h = 左右分（横向 splitter），v = 上下分（纵向 splitter）
ORIENT_H = "h"
ORIENT_V = "v"

#: 比例未指定时使用的默认值（交给 QSplitter 自行均分）
DEFAULT_CHILD_RATIO = 0

#: 布局模板名
KIND_SINGLE = "single"
KIND_V = "v"
KIND_H = "h"
KIND_H_LEFT_V = "h_left_v"
KIND_H_RIGHT_V = "h_right_v"
KIND_TABS = "tabs"
KIND_CUSTOM = "custom"

LAYOUT_KINDS: Tuple[str, ...] = (
    KIND_V,
    KIND_H,
    KIND_H_LEFT_V,
    KIND_H_RIGHT_V,
    KIND_TABS,
    KIND_SINGLE,
    KIND_CUSTOM,
)

#: 模板名 -> 界面文字（菜单、按钮、提示统一用这份）
LAYOUT_KIND_LABELS: Dict[str, str] = {
    KIND_V: "上下分屏（主程序在上）",
    KIND_H: "左右分屏（主程序在左）",
    KIND_H_LEFT_V: "左右分，左侧再上下分",
    KIND_H_RIGHT_V: "左右分，右侧再上下分",
    KIND_TABS: "单窗格 + 全部标签",
    KIND_SINGLE: "只看主程序",
    KIND_CUSTOM: "自定义（拖出来的布局）",
}

# ---------------------------------------------------------------------------
# QSettings 键名约定（本机界面偏好都存这里，不写进 bots_config.json）
#
#     pane/layout/<bot_id>          模板名（v / h / h_left_v / h_right_v / tabs / single / custom）
#     pane/tree/<bot_id>            custom 时的精确布局树（JSON 字符串）
#     pane/sizes/<bot_id>/<path>    某个分割节点的比例，path 形如 "h0" / "h0.v1"
#     pane/focus/<bot_id>           上次聚焦的程序 id，重开窗口时还原
#
# 旧的 split/<bot_id> 只读兼容（迁移一次后不再写）。
# ---------------------------------------------------------------------------

SETTINGS_PANE_LAYOUT = "pane/layout/"
SETTINGS_PANE_TREE = "pane/tree/"
SETTINGS_PANE_SIZES = "pane/sizes/"
SETTINGS_PANE_FOCUS = "pane/focus/"
SETTINGS_PANE_PREFIXES: Tuple[str, ...] = (
    SETTINGS_PANE_LAYOUT,
    SETTINGS_PANE_TREE,
    SETTINGS_PANE_SIZES,
    SETTINGS_PANE_FOCUS,
)


def settings_layout_key(bot_id: str) -> str:
    """QSettings 里保存"该机器人用哪个模板"的键。"""
    return SETTINGS_PANE_LAYOUT + (bot_id or "")


def settings_tree_key(bot_id: str) -> str:
    """QSettings 里保存"自定义布局树"的键。"""
    return SETTINGS_PANE_TREE + (bot_id or "")


def settings_sizes_key(bot_id: str, node_path: str = "") -> str:
    """QSettings 里保存"某个分割节点比例"的键（node_path 为空表示整页）。"""
    path = (node_path or "").strip("/")
    base = SETTINGS_PANE_SIZES + (bot_id or "")
    return "{}/{}".format(base, path) if path else base


def settings_focus_key(bot_id: str) -> str:
    """QSettings 里保存"上次聚焦的程序"的键。"""
    return SETTINGS_PANE_FOCUS + (bot_id or "")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _as_text(value: Any, default: str = "") -> str:
    """安全转字符串。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _as_int(value: Any, default: int = 0) -> int:
    """安全转整数。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except (OverflowError, ValueError):
            return default
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _clean_ids(values: Any, fallback: Optional[Iterable[str]] = None) -> List[str]:
    """把任意输入整理成去重、非空、保持顺序的程序 id 列表。"""
    result: List[str] = []
    if isinstance(values, (str, bytes)):
        candidates = [values]
    elif isinstance(values, (list, tuple, set)):
        candidates = list(values)
    else:
        candidates = []
    for item in candidates:
        text = _as_text(item, "").strip()
        if text and text not in result:
            result.append(text)
    if not result and fallback is not None:
        result = [text for text in fallback if text]
    return result


def _clean_ratio(values: Any, count: int) -> List[int]:
    """整理比例数组：长度适配、负数归零、总和不为 0 时保持相对大小。"""
    ratios: List[int] = []
    if isinstance(values, (list, tuple)):
        for item in values:
            ratios.append(max(0, _as_int(item, 0)))
    if len(ratios) < count:
        ratios.extend([DEFAULT_CHILD_RATIO] * (count - len(ratios)))
    elif len(ratios) > count:
        ratios = ratios[:count]
    if sum(ratios) <= 0:
        return [DEFAULT_CHILD_RATIO] * count
    return ratios


def normalize_orientation(value: Any, default: str = ORIENT_H) -> str:
    """把方向字段规范化为 h / v。"""
    text = _as_text(value, "").strip().lower()
    if text in ("h", "horizontal", "row", "left-right", "lr", "左右", "横"):
        return ORIENT_H
    if text in ("v", "vertical", "column", "top-bottom", "tb", "上下", "纵"):
        return ORIENT_V
    return default


# ---------------------------------------------------------------------------
# 布局节点
# ---------------------------------------------------------------------------

class PaneNode:
    """布局树节点。

    - ``type == "split"``：``orientation`` + ``children``（2 个或更多）+ ``ratio``
    - ``type == "pane"`` ：``programs``（1 个或多个）+ ``current``（子标签选中项）

    ``programs`` 恒为**去重非空**的 id 列表；空窗格不允许存在（会被 sanitize 剔除）。
    """

    __slots__ = ("type", "orientation", "children", "ratio", "programs", "current")

    def __init__(
        self,
        node_type: str = TYPE_PANE,
        orientation: str = ORIENT_H,
        children: Optional[List["PaneNode"]] = None,
        ratio: Optional[List[int]] = None,
        programs: Optional[List[str]] = None,
        current: Optional[str] = None,
    ) -> None:
        self.type: str = TYPE_SPLIT if node_type == TYPE_SPLIT else TYPE_PANE
        self.orientation: str = normalize_orientation(orientation, ORIENT_H)
        self.children: List["PaneNode"] = list(children) if children else []
        self.programs: List[str] = _clean_ids(programs)
        self.ratio: List[int] = _clean_ratio(ratio, len(self.children))
        if current and current in self.programs:
            self.current = str(current)
        elif self.programs:
            self.current = self.programs[0]
        else:
            self.current = None

    # ---- 基本属性 ----

    @property
    def is_split(self) -> bool:
        return self.type == TYPE_SPLIT

    @property
    def is_pane(self) -> bool:
        return self.type == TYPE_PANE

    @property
    def program_count(self) -> int:
        if self.is_pane:
            return len(self.programs)
        return sum(child.program_count for child in self.children)

    @property
    def has_tabs(self) -> bool:
        """是否含"一个窗格放多个程序"的子标签。"""
        if self.is_pane:
            return len(self.programs) > 1
        return any(child.has_tabs for child in self.children)

    # ---- 构造快捷方式 ----

    @classmethod
    def pane(cls, *programs: Any, current: Optional[str] = None) -> "PaneNode":
        """建一个窗格（自动展开列表参数并去重）。"""
        flat: List[str] = []
        for item in programs:
            if isinstance(item, (list, tuple, set)):
                flat.extend(_clean_ids(item))
            else:
                flat.extend(_clean_ids([item]))
        return cls(TYPE_PANE, programs=flat, current=current)

    @classmethod
    def split(
        cls,
        orientation: str,
        children: Optional[Sequence["PaneNode"]] = None,
        ratio: Optional[List[int]] = None,
    ) -> "PaneNode":
        """建一个分割节点。"""
        items = [child for child in (children or []) if child is not None]
        return cls(TYPE_SPLIT, orientation=orientation, children=items, ratio=ratio)

    # ---- 序列化 ----

    def to_dict(self) -> Dict[str, Any]:
        """转成可写入 JSON / QSettings 的字典。"""
        if self.is_split:
            data: Dict[str, Any] = {
                "type": TYPE_SPLIT,
                "orientation": self.orientation,
                "children": [child.to_dict() for child in self.children],
            }
            if any(value > 0 for value in self.ratio):
                data["ratio"] = list(self.ratio)
            return data
        data = {"type": TYPE_PANE, "programs": list(self.programs)}
        if self.current and len(self.programs) > 1:
            data["current"] = self.current
        return data

    @classmethod
    def from_dict(cls, data: Any, depth: int = 0) -> Optional["PaneNode"]:
        """从字典还原；非法结构返回 None（由上层决定回落策略）。"""
        if not isinstance(data, dict) or depth > MAX_DEPTH:
            return None
        node_type = _as_text(data.get("type"), "").strip().lower()
        if node_type == TYPE_SPLIT:
            raw_children = data.get("children")
            if not isinstance(raw_children, (list, tuple)):
                return None
            children: List["PaneNode"] = []
            for item in raw_children:
                child = cls.from_dict(item, depth + 1)
                if child is not None:
                    children.append(child)
            if len(children) < 2:
                return None
            orientation = normalize_orientation(data.get("orientation"), ORIENT_H)
            return cls(
                TYPE_SPLIT,
                orientation=orientation,
                children=children,
                ratio=_clean_ratio(data.get("ratio"), len(children)),
            )
        if node_type == TYPE_PANE:
            programs = _clean_ids(data.get("programs"))
            if not programs:
                return None
            return cls(
                TYPE_PANE,
                programs=programs,
                current=_as_text(data.get("current"), "") or None,
            )
        return None

    def clone(self) -> "PaneNode":
        """深拷贝。"""
        copied = PaneNode.from_dict(copy.deepcopy(self.to_dict()))
        return copied if copied is not None else PaneNode.pane()

    # ---- 行走 ----

    def walk(self) -> Iterable["PaneNode"]:
        """按先序产出自身与所有后代。"""
        yield self
        for child in self.children:
            for item in child.walk():
                yield item

    def panes(self) -> List["PaneNode"]:
        """所有窗格（叶子）。"""
        return [node for node in self.walk() if node.is_pane]

    def splits(self) -> List["PaneNode"]:
        """所有分割节点。"""
        return [node for node in self.walk() if node.is_split]

    def program_ids(self) -> List[str]:
        """树里出现的程序 id（先序、去重）。"""
        result: List[str] = []
        for node in self.walk():
            if not node.is_pane:
                continue
            for program_id in node.programs:
                if program_id not in result:
                    result.append(program_id)
        return result

    def first_pane(self) -> Optional["PaneNode"]:
        """第一个窗格（通常就是主程序所在窗格）。"""
        for node in self.walk():
            if node.is_pane:
                return node
        return None

    def pane_of(self, program_id: str) -> Optional["PaneNode"]:
        """程序所在的窗格。"""
        for node in self.walk():
            if node.is_pane and program_id in node.programs:
                return node
        return None

    def describe(self) -> str:
        """一句话描述，例如 "左右[ 上[ ★A / B ]（2 标签） / C ]"。"""
        if self.is_pane:
            return "（{} 标签）".format(len(self.programs)) if len(self.programs) > 1 else ""
        return ""

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "PaneNode({})".format(self.to_dict())


# ---------------------------------------------------------------------------
# 建树
# ---------------------------------------------------------------------------

def _roles(program_ids: Sequence[str], primary_id: Optional[str]) -> Tuple[Optional[str], List[str]]:
    """返回 (主程序 id, 其余程序 id 列表)，顺序按传入顺序。

    说明：若 ``primary_id`` 为空或不在列表里，**隐式把第一个程序当作主程序**。
    这是刻意保留的既有行为——配置里没标 primary 时，第一个程序占上半窗格，
    不会出现"上半格空着"的画面。
    """
    ids = list(program_ids)
    if primary_id and primary_id in ids:
        return primary_id, [item for item in ids if item != primary_id]
    if ids:
        return ids[0], ids[1:]
    return None, []


def _single_pane(program_id: Optional[str]) -> PaneNode:
    """一个只放单个程序的窗格。"""
    return PaneNode.pane(program_id) if program_id else PaneNode.pane()


def _tabs_pane(program_ids: Sequence[str]) -> PaneNode:
    """把若干程序放进一个带子标签的窗格。"""
    return PaneNode.pane(*program_ids)


def build_default_tree(
    program_ids: Sequence[str],
    primary_id: Optional[str] = None,
) -> PaneNode:
    """默认布局（与改造前的行为一致）。

    - 1 个程序：独占一个窗格
    - 2 个程序：上下各一个窗格
    - ≥3 个程序：主程序独占上半，其余放进下半的标签窗格
    - 没有标 primary 时，**第一个程序被当作主程序**（见 _roles 的说明）

    只有"配置里确实一个程序都没有"时才会返回空窗格。
    """
    ids = _clean_ids(program_ids)
    if not ids:
        return PaneNode.pane()
    if len(ids) == 1:
        return _single_pane(ids[0])

    primary, rest = _roles(ids, primary_id)
    if primary is None or not rest:
        # 理论到不了这里（≥2 个程序时 _roles 一定给出非空 rest），留着兜底
        return _tabs_pane(ids)
    if len(rest) == 1:
        return PaneNode.split(ORIENT_V, [_single_pane(primary), _single_pane(rest[0])])
    return PaneNode.split(ORIENT_V, [_single_pane(primary), _tabs_pane(rest)])


def build_tree(
    kind: str,
    program_ids: Sequence[str],
    primary_id: Optional[str] = None,
) -> PaneNode:
    """按模板名生成布局树。

    kind 取值见 ``LAYOUT_KINDS``；无法识别的模板名按默认布局处理。
    """
    ids = _clean_ids(program_ids)
    if not ids:
        return PaneNode.pane()

    name = _as_text(kind, "").strip().lower()
    primary, rest = _roles(ids, primary_id)

    if name == KIND_SINGLE:
        return _single_pane(primary or ids[0])

    if name == KIND_TABS:
        return _tabs_pane(ids)

    if len(ids) == 1:
        return _single_pane(ids[0])

    if name == KIND_H:
        if primary is None:
            return _tabs_pane(ids)
        if not rest:
            return _single_pane(primary)
        if len(rest) == 1:
            return PaneNode.split(ORIENT_H, [_single_pane(primary), _single_pane(rest[0])])
        return PaneNode.split(ORIENT_H, [_single_pane(primary), _tabs_pane(rest)])

    if name == KIND_H_LEFT_V:
        tail = rest[1:] if len(rest) > 1 else []
        left = PaneNode.split(ORIENT_V, [_single_pane(primary), _single_pane(rest[0])]) \
            if rest else _single_pane(primary)
        if not tail:
            return left
        return PaneNode.split(ORIENT_H, [left, _tabs_pane(tail)])

    if name == KIND_H_RIGHT_V:
        if not rest:
            return _single_pane(primary)
        if len(rest) == 1:
            return PaneNode.split(ORIENT_H, [_single_pane(primary), _single_pane(rest[0])])
        right = PaneNode.split(ORIENT_V, [_single_pane(rest[0]), _single_pane(rest[1])])
        tail = rest[2:]
        if tail:
            right = PaneNode.split(ORIENT_V, [_single_pane(rest[0]), _single_pane(rest[1]),
                                              _tabs_pane(tail)])
        return PaneNode.split(ORIENT_H, [_single_pane(primary), right])

    if name == KIND_V:
        if primary is None:
            return _tabs_pane(ids)
        if not rest:
            return _single_pane(primary)
        if len(rest) == 1:
            return PaneNode.split(ORIENT_V, [_single_pane(primary), _single_pane(rest[0])])
        return PaneNode.split(ORIENT_V, [_single_pane(primary), _tabs_pane(rest)])

    # 未识别的模板名（含 custom）：用默认布局兜底
    return build_default_tree(ids, primary_id)


def sanitize_tree(node: Optional[PaneNode]) -> Optional[PaneNode]:
    """规整布局树：

    - 空窗格（没有程序）剔除
    - 只有一个孩子的分割节点被孩子取代
    - 窗格内程序去重、``current`` 必须属于该窗格
    - 比例数组与孩子数量对齐

    返回 None 表示这棵树已经不可用，调用方应回落默认布局。
    """
    if node is None:
        return None

    if node.is_pane:
        programs = _clean_ids(node.programs)
        if not programs:
            return None
        current = node.current if node.current in programs else programs[0]
        return PaneNode(TYPE_PANE, programs=programs, current=current)

    cleaned_children: List[PaneNode] = []
    for child in node.children:
        fixed = sanitize_tree(child)
        if fixed is not None:
            if fixed.is_split and fixed.orientation == node.orientation:
                # 同方向的分割可以合并到父节点，让树更扁（更少的嵌套 splitter）
                cleaned_children.extend(fixed.children)
            else:
                cleaned_children.append(fixed)

    if not cleaned_children:
        return None
    if len(cleaned_children) == 1:
        return cleaned_children[0]
    return PaneNode(
        TYPE_SPLIT,
        orientation=node.orientation,
        children=cleaned_children,
        ratio=_clean_ratio(node.ratio, len(cleaned_children)),
    )


# ---------------------------------------------------------------------------
# 对账：程序增删后修正布局
# ---------------------------------------------------------------------------

def reconcile(
    node: Optional[PaneNode],
    program_ids: Sequence[str],
    primary_id: Optional[str] = None,
) -> Tuple[PaneNode, Dict[str, List[str]]]:
    """把布局树与"当前配置里的程序"对齐。

    规则（保证任何程序都不会没有日志可看）：

    1. 树里引用了已不存在的程序 -> 剔除该 id，空窗格一并消失；
    2. 配置里有程序不在树里 -> 追加到"标签窗格"（含多个程序的窗格）；
       没有标签窗格时追加到第一个窗格；树里一个窗格都没有则重建默认布局；
    3. 程序数量与树不匹配且无法修正 -> 回落默认布局；
    4. 结果始终经过 sanitize_tree()。

    返回 ``(修正后的布局树, {"missing": [...], "added": [...], "reset": [...]})``，
    其中 missing 是"布局里有、配置里没有"的程序 id，added 是"配置里有、被新加进
    布局"的程序 id，reset 非空表示发生了回落重建（值为原因说明）。
    """
    ids = _clean_ids(program_ids)
    report: Dict[str, List[str]] = {"missing": [], "added": [], "reset": []}

    if not ids:
        report["reset"].append("配置里没有任何程序，布局已重置为空窗格")
        return PaneNode.pane(), report

    fixed = sanitize_tree(node) if node is not None else None
    if fixed is None:
        report["reset"].append("布局数据不可用，已按默认布局重建")
        return build_default_tree(ids, primary_id), report

    missing = [item for item in fixed.program_ids() if item not in ids]
    if missing:
        report["missing"].extend(missing)
        _drop_programs(fixed, set(missing))
        fixed = sanitize_tree(fixed)
        if fixed is None:
            report["reset"].append("剔除失效程序后布局为空，已按默认布局重建")
            return build_default_tree(ids, primary_id), report

    placed = fixed.program_ids()
    extra = [item for item in ids if item not in placed]
    if extra:
        target = _pick_append_pane(fixed)
        if target is None:
            report["reset"].append("布局里没有可用窗格，已按默认布局重建")
            return build_default_tree(ids, primary_id), report
        for program_id in extra:
            if program_id not in target.programs:
                target.programs.append(program_id)
        if target.current is None:
            target.current = target.programs[0]
        report["added"].extend(extra)

    return fixed, report


def _drop_programs(node: PaneNode, remove: set) -> None:
    """从树里摘掉指定程序（原地修改）；空出来的窗格交给 sanitize_tree 处理。"""
    if node.is_pane:
        node.programs = [item for item in node.programs if item not in remove]
        if node.current not in node.programs:
            node.current = node.programs[0] if node.programs else None
        return
    for child in node.children:
        _drop_programs(child, remove)


def _pick_append_pane(node: PaneNode) -> Optional[PaneNode]:
    """挑选"新程序追加到哪个窗格"：优先标签窗格，其次第一个窗格。"""
    panes = node.panes()
    if not panes:
        return None
    for pane in panes:
        if len(pane.programs) > 1 and len(pane.programs) < MAX_TABS_PER_PANE:
            return pane
    for pane in panes:
        if pane.programs:
            return pane
    return None


# ---------------------------------------------------------------------------
# 推断与描述
# ---------------------------------------------------------------------------

def layout_kind_of(node: Optional[PaneNode]) -> str:
    """按形状推断布局模板名（不保证唯一，用于菜单里高亮当前那一项）。

    推断不出来时返回 ``"custom"``。
    """
    if node is None:
        return KIND_CUSTOM
    panes = node.panes()
    if not panes:
        return KIND_CUSTOM
    if len(panes) == 1:
        return KIND_TABS if len(panes[0].programs) > 1 else KIND_SINGLE
    if not node.is_split or len(node.children) != 2:
        return KIND_CUSTOM

    left, right = node.children
    if node.orientation == ORIENT_V:
        if left.is_pane and right.is_pane:
            return KIND_V
        return KIND_CUSTOM
    if node.orientation != ORIENT_H:
        return KIND_CUSTOM
    if left.is_pane and right.is_pane:
        return KIND_H
    if left.is_split and left.orientation == ORIENT_V and right.is_pane:
        return KIND_H_LEFT_V
    if left.is_pane and right.is_split and right.orientation == ORIENT_V:
        return KIND_H_RIGHT_V
    return KIND_CUSTOM


def describe_tree(node: Optional[PaneNode], labels: Optional[Dict[str, str]] = None) -> str:
    """生成可读的布局描述，例如 ``左右[ 上[ ★主程序 / ollama ] / GPT-SoVITS ]``。

    labels 是 ``程序 id -> 显示名`` 的映射；缺省时直接用 id。
    """
    names = labels or {}

    def name_of(program_id: str) -> str:
        return names.get(program_id, program_id)

    def render(item: Optional[PaneNode]) -> str:
        if item is None:
            return "（空）"
        if item.is_pane:
            if not item.programs:
                return "（空窗格）"
            if len(item.programs) == 1:
                return name_of(item.programs[0])
            return "标签[{}]".format(
                " | ".join(
                    "{}{}".format("★" if index == 0 else "", name_of(pid))
                    for index, pid in enumerate(item.programs)
                )
            )
        direction = "左右" if item.orientation == ORIENT_H else "上下"
        return "{}[ {} ]".format(direction, " / ".join(render(child) for child in item.children))

    return render(node)


def programs_in_tree(node: Optional[PaneNode]) -> List[str]:
    """树里出现的所有程序 id（按先序、去重）。"""
    return node.program_ids() if node is not None else []


def pane_count(node: Optional[PaneNode]) -> int:
    """窗格数量（= 右侧最多同时可见的日志窗口数）。"""
    return len(node.panes()) if node is not None else 0


def pane_for_program(node: Optional[PaneNode], program_id: str) -> Optional[PaneNode]:
    """程序所在的窗格。"""
    return node.pane_of(program_id) if node is not None else None


def first_pane(node: Optional[PaneNode]) -> Optional[PaneNode]:
    """第一个窗格（通常是主程序窗格）。"""
    return node.first_pane() if node is not None else None


def collect_panes(node: Optional[PaneNode]) -> List[PaneNode]:
    """所有窗格（保持先序）。"""
    return node.panes() if node is not None else []


def clone_tree(node: Optional[PaneNode]) -> Optional[PaneNode]:
    """深拷贝；None 原样返回。"""
    return node.clone() if node is not None else None


# ---------------------------------------------------------------------------
# 持久化（QSettings 以字符串形式保存）
# ---------------------------------------------------------------------------

def tree_to_dict(node: Optional[PaneNode]) -> Dict[str, Any]:
    """树 -> 字典（None 返回空 dict）。"""
    return node.to_dict() if node is not None else {}


def tree_from_dict(data: Any) -> Optional[PaneNode]:
    """字典 -> 树；失败返回 None。"""
    return PaneNode.from_dict(data)


def tree_to_json(node: Optional[PaneNode]) -> str:
    """树 -> JSON 字符串（写 QSettings 用，紧凑格式）。"""
    if node is None:
        return ""
    try:
        return json.dumps(node.to_dict(), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return ""


def tree_from_json(text: Any) -> Optional[PaneNode]:
    """JSON 字符串 -> 树；失败返回 None（调用方回落默认布局）。"""
    if isinstance(text, (bytes, bytearray)):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return PaneNode.from_dict(data)
