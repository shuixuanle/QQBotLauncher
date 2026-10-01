# -*- coding: utf-8 -*-
"""静态检查：折叠左栏后的"浏览器式标签栏"（切换 bot 的鼠标入口）。

需求原话：
    "关闭左侧栏时，切换 bot 实例似乎只能使用 ctrl+tab，
     建议在取消左侧栏时还原添加左侧栏前上方的类似于浏览器的标签页"

要点：
  · 标签栏放在实例区顶部（QTabBar#botTabBar），浏览器式：可点、可关、可拖动排序；
  · **左栏一折叠就自动出现**；左栏展开时按"是否手动勾选"决定；
  · 一个窗口都没打开时不显示（空栏没意义）；
  · 点标签 = 切页面 + 左栏高亮落到该机器人的程序行（与左栏单击一致）。

用法：
    python tools\\check_bot_tab_bar.py
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "app" / "ui" / "main_window.py"
THEME = ROOT / "app" / "ui" / "theme.py"

failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def main() -> int:
    src = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow"),
        None,
    )
    check("找到 MainWindow", cls is not None)
    methods = {
        c.name: c for c in cls.body if isinstance(c, ast.FunctionDef)
    } if cls else {}

    print("[1] 构建与结构")
    build = methods.get("_build_bot_tab_bar")
    check("存在 _build_bot_tab_bar", build is not None)
    b_src = ast.get_source_segment(src, build) or "" if build else ""
    check("  用 QTabBar", "QTabBar(" in b_src)
    check("  objectName = botTabBar（供样式选择器使用）",
          "botTabBar" in b_src)
    check("  默认隐藏（左栏折叠时才出现）", "setVisible(False)" in b_src)
    check("  可关闭标签", "setTabsClosable(True)" in b_src)
    check("  可拖动排序（浏览器习惯）", "setMovable(True)" in b_src)
    check("  过长文字省略号", "ElideRight" in b_src)
    check("  接上 currentChanged / tabCloseRequested",
          "currentChanged" in b_src and "tabCloseRequested" in b_src)

    central = methods.get("_build_central")
    c_src = ast.get_source_segment(src, central) or "" if central else ""
    check("_build_central 里把标签栏放进实例区容器",
          "views_container" in c_src and "_build_bot_tab_bar()" in c_src)
    if "addWidget(self.bot_tab_bar)" in c_src and "addWidget(self.stack" in c_src:
        check(
            "  标签栏在页面栈上方（先加标签栏再加栈）",
            c_src.index("addWidget(self.bot_tab_bar)") < c_src.index("addWidget(self.stack"),
        )
    else:
        check("  标签栏在页面栈上方（先加标签栏再加栈）", False, "找不到 addWidget 调用")

    print("\n[1.5] 标签栏代码不能用错 @property（真机事故）")
    # 真实事故：bar.addTab(widget.bot_name()) —— bot_name 是 @property 返回 str，
    # 加括号 → TypeError: 'str' object is not callable → 管理器窗口直接打不开。
    check("  没有把 bot_name 当方法调用", "bot_name()" not in src)
    check("  同步调用统一走 _safe_sync_bot_tab_bar（异常不外泄）",
          "_safe_sync_bot_tab_bar" in src)
    safe = methods.get("_safe_sync_bot_tab_bar")
    check("  存在 _safe_sync_bot_tab_bar", safe is not None)
    safe_src = ast.get_source_segment(src, safe) or "" if safe else ""
    check("  它内部有 try/except 兜底", "except" in safe_src)

    print("\n[2] 同步与切换")
    sync = methods.get("_sync_bot_tab_bar")
    check("存在 _sync_bot_tab_bar", sync is not None)
    s_src = ast.get_source_segment(src, sync) or "" if sync else ""
    check("  以 stack 的顺序为准（不另立状态）", "stack.widget(index)" in s_src)
    check("  标签数据带 bot_id", "setTabData" in s_src)
    check("  重建/同步时屏蔽信号（避免递归）", "blockSignals" in s_src)

    changed = methods.get("_on_bot_tab_bar_changed")
    check("存在 _on_bot_tab_bar_changed", changed is not None)
    ch_src = ast.get_source_segment(src, changed) or "" if changed else ""
    check("  切换页面用 setCurrentWidget", "setCurrentWidget" in ch_src)
    check("  高亮跟着走（_select_nav_for_bot）", "_select_nav_for_bot" in ch_src)
    check("  ▸ 标记同步（_apply_nav_focus_marker）",
          "_apply_nav_focus_marker" in ch_src)

    close = methods.get("_on_bot_tab_bar_close_requested")
    check("存在 _on_bot_tab_bar_close_requested", close is not None)
    cl_src = ast.get_source_segment(src, close) or "" if close else ""
    check("  关闭走同一条路径（close_bot_window + confirm）",
          "close_bot_window" in cl_src and "confirm=True" in cl_src)

    print("\n[3] 可见性规则")
    vis = methods.get("_update_bot_tab_bar_visible")
    check("存在 _update_bot_tab_bar_visible", vis is not None)
    v_src = ast.get_source_segment(src, vis) or "" if vis else ""
    check("  依赖 _nav_collapsed（左栏折叠）", "_nav_collapsed" in v_src)
    check("  依赖手动勾选标志", "_bot_tab_bar_forced" in v_src)
    check("  没有窗口时不显示", "_tabs" in v_src)
    check("  等左栏状态应用完再决定（避免启动闪动）", "_nav_state_applied" in v_src)

    toggle = methods.get("toggle_bot_tab_bar")
    check("存在 toggle_bot_tab_bar", toggle is not None)
    t_src = ast.get_source_segment(src, toggle) or "" if toggle else ""
    check("  写成注册表（SETTINGS_BOT_TAB_BAR）", "SETTINGS_BOT_TAB_BAR" in t_src)
    check("  同步菜单勾选态且屏蔽信号", "setChecked" in t_src and "blockSignals" in t_src)

    print("\n[4] 生命周期接线（开窗/关窗/翻页/折叠都要同步）")
    for name, needle in (
        ("open_bot_tab", "_sync_bot_tab_bar()"),
        ("_remove_tab", "_sync_bot_tab_bar()"),
        ("_on_current_page_changed", "_sync_bot_tab_bar()"),
    ):
        fn = methods.get(name)
        f_src = ast.get_source_segment(src, fn) or "" if fn else ""
        check("  {} 里调用了 {}".format(name, needle), needle in f_src)

    restore = methods.get("_restore_nav_state")
    r_src = ast.get_source_segment(src, restore) or "" if restore else ""
    check("  _restore_nav_state 里同步可见性",
          "_update_bot_tab_bar_visible()" in r_src and "_nav_state_applied" in r_src)
    nav = methods.get("toggle_nav")
    n_src = ast.get_source_segment(src, nav) or "" if nav else ""
    check("  toggle_nav 里同步可见性", "_update_bot_tab_bar_visible()" in n_src)

    pref = methods.get("_restore_bot_tab_bar_preference")
    check("存在 _restore_bot_tab_bar_preference（启动还原）", pref is not None)
    schedule = methods.get("_schedule_nav_restore")
    sc_src = ast.get_source_segment(src, schedule) or "" if schedule else ""
    check("  _schedule_nav_restore 里调用了它",
          "_restore_bot_tab_bar_preference()" in sc_src)

    print("\n[4.5] Bot 工具条（左栏折叠时替代左栏按钮）")
    bot_bar = methods.get("_build_bot_bar")
    check("存在 _build_bot_bar", bot_bar is not None)
    bb_src = ast.get_source_segment(src, bot_bar) or "" if bot_bar else ""
    check("  objectName = botBar", "botBar" in bb_src)
    check("  默认可见（唯一一条工具条，不随左栏显隐）",
          "setVisible(True)" in bb_src)
    check("  文字按钮（与主工具栏一致）", "ToolButtonTextOnly" in bb_src)
    for action in ("action_new_bot", "action_edit_bot", "action_start_bot",
                   "action_stop_bot", "action_restart_bot",
                   "action_open_all_windows", "action_bot_list",
                   "action_open_config"):
        check("  挂了 {}".format(action), action in bb_src)
    check("  复用的是同一批 QAction（不是新建按钮）",
          "QPushButton(" not in bb_src)
    check("  存在 action_open_all_windows 动作定义",
          "self.action_open_all_windows = QAction(" in src)
    check("  它接到 open_all_windows", "open_all_windows)" in src)
    check("  _build_toolbar 里调用了 _build_bot_bar", "_build_bot_bar()" in src)
    vis_fn = methods.get("_update_bot_tab_bar_visible")
    vis_src = ast.get_source_segment(src, vis_fn) or "" if vis_fn else ""
    check("  可见性里包含 bot_bar", "bot_bar" in vis_src)
    check("  工具条不要求「已打开窗口」（与标签栏区别）",
          "nav_collapsed or forced" in vis_src)
    check("  chrome_qss 覆盖 QToolBar#botBar",
          "QToolBar#botBar" in THEME.read_text(encoding="utf-8"))

    print("\n[4.6] 只保留一行工具栏（真机确认的布局）")
    main_bar = methods.get("_build_toolbar")
    mb_src = ast.get_source_segment(src, main_bar) or "" if main_bar else ""

    def actions_of(text):
        found = []
        for line in text.splitlines():
            s = line.strip()
            for prefix in ("toolbar.addAction(self.", "bar.addAction(self."):
                if s.startswith(prefix):
                    found.append(s[len(prefix):].rstrip(")"))
        return found

    main_actions = actions_of(mb_src)
    bot_actions = actions_of(bb_src)
    print("    主工具栏   = {}".format(main_actions))
    print("    Bot 工具条 = {}".format(bot_actions))
    # 现在只有**一条**工具栏（真机确认"只留一行"）：_build_toolbar 委托给
    # _build_bot_bar，不再自己挂动作。
    check("  _build_toolbar 不再自己挂动作（只委托）",
          not main_actions, "实际 {}".format(main_actions))
    check("  _build_toolbar 里调用 _build_bot_bar 并复用 self.toolbar",
          "_build_bot_bar()" in mb_src and "self.toolbar = self.bot_bar" in mb_src)
    check("  只保留一行：动作齐全（机器人 + 窗口 + 全局）",
          {"action_start_bot", "action_stop_bot", "action_restart_bot",
           "action_open_all_windows", "action_bot_list", "action_new_bot",
           "action_edit_bot", "action_open_config",
           "action_start_all", "action_stop_all"} <= set(bot_actions),
          "实际 {}".format(bot_actions))
    check("  没有重复动作（同一动作只挂一次）",
          len(bot_actions) == len(set(bot_actions)),
          "重复：{}".format(sorted(
              a for a in set(bot_actions) if bot_actions.count(a) > 1)))
    check("  全局动作排在最后（用得最少、影响面最大）",
          bot_actions[-2:] == ["action_start_all", "action_stop_all"],
          "尾部：{}".format(bot_actions[-3:]))
    vis_fn2 = methods.get("_update_bot_tab_bar_visible")
    vis_src2 = ast.get_source_segment(src, vis_fn2) or "" if vis_fn2 else ""
    check("  可见性逻辑不再隐藏 Bot 工具条（常显）",
          "bot_bar.setVisible(True)" in vis_src2)

    print("\n[5] 菜单与主题")
    check("视图菜单里有「显示标签栏」", "action_toggle_bot_tab_bar" in src)
    check("  菜单项是可选中的（setCheckable）",
          "action_toggle_bot_tab_bar.setCheckable(True)" in src)

    theme_src = THEME.read_text(encoding="utf-8")
    check("theme.py 提供 bot_tab_bar_qss", "def bot_tab_bar_qss" in theme_src)
    check("  样式选择器是 QTabBar#botTabBar",
          "QTabBar#botTabBar" in theme_src)
    check("  三种状态都有（默认/hover/selected）",
          all(k in theme_src for k in ("::tab {", "::tab:hover", "::tab:selected")))
    check("  _apply_theme 里有接线", "bot_tab_bar_qss" in src)

    print("\n[6] 自检项")
    check("自检含 [12.z]", "[12.z] 折叠左栏后要有可点的标签栏" in src)
    check("  自检断言「折叠后标签栏可见」", "折叠左栏后标签栏必须自动出现" in src)
    check("  自检断言「点标签能切页面」", "点标签应当切到那个机器人的窗口" in src)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
