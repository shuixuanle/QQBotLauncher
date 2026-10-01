# 诊断脚本（排查界面 / 主题 / 日志颜色问题时用）

这几个脚本是**只读**的（最多临时改一下 QSettings 再还原），留着是因为它们
对"以后又出现类似症状"仍然有用。用法：

    cd /d "本项目目录"
    python tools\diagnostics\_theme_dump.py

| 脚本 | 用途 |
| --- | --- |
| `_theme_dump.py` | 打印三种模式下**界面各处真正的颜色**（调色板角色 + 左栏/窗格/日志区实际底色） |
| `_theme_check.py` | 主题模式切换的 6 组自检（判据自洽 / 日志区跟随 / 对比度兜底 / 跟随系统 / hint 撒谎 / 10 轮不漂移） |
| `_log_color_pixel.py` | **把日志区渲染成图片并采样像素** —— 读属性只能知道"我们设了什么"，读像素才知道"屏幕上是什么"；可验证深色下日志字色是否正确 |

> 早期为定位具体 bug 还写过一批一次性探针（`_nav_trace.py`、`_minimal_theme.py`、
> `_selector_probe.py` 等）。它们已**从仓库移除**（本地文件仍在），
> 需要时可以用 git 历史取回，例如：
>
>     git show a406ccb:tools/diagnostics/_nav_trace.py > _nav_trace.py
