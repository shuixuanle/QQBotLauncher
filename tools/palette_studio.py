# -*- coding: utf-8 -*-
"""配色工作台（命令行入口）：单独开一个窗口调色，和主界面里那个是**同一个对话框**。

平时更推荐直接从管理器里打开：**视图 → 外观 → 配色工作台…** ——
改完立刻生效（主界面、已打开的窗口、日志一起变），还能一键保存并留下配色记录。

这个入口的用处：
  · 管理器没开（或起不来）时也能调色；
  · 想对比深浅两套配色，单独开一个窗口更方便。

用法：
    python tools\\palette_studio.py            # 先看浅色
    python tools\\palette_studio.py dark       # 直接看深色

实现上这里**不重复写界面**：直接复用 `app/ui/palette_dialog.py` 里的
`PaletteDialog`（真控件、真上色路径、真设置读写），只有"保存后要重启"这一点
和主界面里不同 —— 主界面是立即生效，这里是给下次启动用。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import PyQt6  # noqa: F401
except ImportError:
    print("需要 PyQt6（pip install -r requirements.txt），本机没装 —— 无法启动工作台。")
    raise SystemExit(0)

from PyQt6.QtWidgets import QApplication                      # noqa: E402

from app.ui import theme as theme_tokens                      # noqa: E402
from app.ui.palette_dialog import PaletteDialog                # noqa: E402


# 控制台兜底：中文 Windows 的控制台默认是 cp936，编码不了 ▸ / ⇄ / ✓ 这类符号，
# 直接 print 会抛 UnicodeEncodeError，把检查器自己弄崩（真机踩过：run_all_checks
# 里两个检查器就是这么红的）。这里统一退化成 ?，绝不因为"输出"而中断检查。
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(theme_tokens.APP_NAME)
    app.setOrganizationName(theme_tokens.ORG_NAME)

    theme_tokens.load_custom_colors()
    dark = any(str(arg).lower() in ("dark", "--dark", "深色") for arg in sys.argv[1:])
    theme_tokens.apply_theme(app, "dark" if dark else "light")

    dialog = PaletteDialog(dark=dark)
    dialog.setWindowTitle("配色工作台（独立窗口）—— 保存后下次启动生效")
    dialog.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
