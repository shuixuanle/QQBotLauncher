# 诊断脚本（排查界面/主题/导航问题时用）

这些脚本都是**只读**的（最多临时改一下 QSettings 再还原），用法：

    cd /d "C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器"
    python tools\diagnostics\_theme_dump.py

| 脚本 | 用途 |
| --- | --- |
| `_theme_dump.py` | 打印三种模式下**界面各处真正的颜色**（调色板角色 + 左栏/窗格/日志区实际底色） |
| `_theme_check.py` | 主题模式切换的 6 组自检（判据自洽 / 日志区跟随 / 对比度兜底 / 跟随系统 / hint 撒谎 / 10 轮不漂移） |
| `_chain_probe.py` | 主程序链路：切换主题时日志控件的样式串与调色板是否真的更新 |
| `_log_style_dump.py` | 日志控件自身 styleSheet 与祖先链 |
| `_logcolor_probe.py` | `log_colors()` 的输入调色板与输出颜色并排 |
| `_minimal_theme.py` / `_minimal_theme2.py` | 最小窗口实验：样式串 vs Qt 实际调色板 |
| `_selector_probe.py` | Qt 样式表选择器语义验证（#id 是"后代"不是"自身"） |
| `_nav_diag2.py` | 左侧栏 折叠/展开状态跨"重启"还原（4 个场景） |
| `_nav_trace.py` | 谁在什么时候把左栏折叠/展开（含可见性事件监听） |
