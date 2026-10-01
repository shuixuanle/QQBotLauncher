# -*- coding: utf-8 -*-
"""N2.1 验证：主题"花屏"修复。

· current_scheme 判据改为调色板优先
· _PALETTE_OVERRIDDEN 如实反映结果（含 last_apply 说明）
· 日志区跟随主题（不再"永远深色"）
只读（不改注册表）。用法：
    python _theme_check.py
"""

import sys

from PyQt6.QtWidgets import QApplication

sys.path.insert(0, r"C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器")

from app.ui import theme as theme_tokens  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


def qss_bg_lightness(qss: str) -> int:
    head = qss.split("background-color: ", 1)[1]
    color = head.split(";", 1)[0].strip()
    from PyQt6.QtGui import QColor

    return QColor(color).lightness()


def main() -> int:
    app = QApplication(sys.argv[:1])

    print("[1] 深浅切换：调色板 / 模式 / 判据必须始终自洽")
    for mode, expect_dark in ((theme_tokens.MODE_DARK, True),
                              (theme_tokens.MODE_LIGHT, False),
                              (theme_tokens.MODE_DARK, True),
                              (theme_tokens.MODE_LIGHT, False)):
        theme_tokens.apply_theme(app, mode)
        lum = theme_tokens.palette_window_lightness(app)
        info = theme_tokens.theme_debug_info()
        ok = (lum < 128) is expect_dark
        same_as_mode = (theme_tokens.current_scheme() == "dark") is expect_dark
        print("    mode={:<6} Window亮度={:<4} scheme={:<6} overridden={!r} last_apply={}".format(
            mode, lum, info["scheme"], info["overridden"], info["last_apply"]))
        check("{}: 亮度方向正确".format(mode), ok, str(lum))
        check("{}: current_scheme 与调色板一致".format(mode), same_as_mode)

    print("\n[2] 日志区跟随主题（不再是永远深色）")
    theme_tokens.apply_theme(app, theme_tokens.MODE_LIGHT)
    light_qss = theme_tokens.log_editor_qss()
    theme_tokens.apply_theme(app, theme_tokens.MODE_DARK)
    dark_qss = theme_tokens.log_editor_qss()
    light_lum = qss_bg_lightness(light_qss)
    dark_lum = qss_bg_lightness(dark_qss)
    print("    日志区底色亮度：浅色={} 深色={}".format(light_lum, dark_lum))
    check("浅色下日志区偏亮（>=128）", light_lum >= 128, str(light_lum))
    check("深色下日志区偏暗（<128）", dark_lum < 128, str(dark_lum))
    check("两套样式不同", light_qss != dark_qss)

    print("\n[3] 对比度兜底（防止白底白字这类不可读组合）")
    check("_contrast_ok 对相近色判 False",
          theme_tokens._contrast_ok("#ffffff", "#f0f0f0") is False)
    check("_contrast_ok 对差异色判 True",
          theme_tokens._contrast_ok("#ffffff", "#1a1a1a") is True)

    print("\n[4] 跟随系统：撤掉自建调色板；判据必须与实际调色板一致（不轻信 hint）")
    theme_tokens.apply_theme(app, theme_tokens.MODE_SYSTEM)
    info = theme_tokens.theme_debug_info()
    lum = info["window_lightness"]
    palette_dark = lum < 128
    print("    mode={} overridden={!r} hint={} scheme={} 亮度={}".format(
        info["mode"], info["overridden"], info["hint"], info["scheme"], lum))
    check("跟随系统不声称自建调色板", info["overridden"] is False)
    check("跟随系统 mode 正确", info["mode"] == theme_tokens.MODE_SYSTEM)
    # 核心：无论 hint 怎么说，scheme 必须与"肉眼所见"（调色板亮度）一致
    check("scheme 与调色板亮度一致（hint={} 亮度={}）".format(info["hint"], lum),
          (info["scheme"] == "dark") is palette_dark)

    print("\n[5] 系统模式下切换系统明暗（模拟 hint 撒谎）")
    # 强制 hint=dark 但把调色板设成浅色 —— 真机上就是这个组合导致"白字"
    try:
        app.styleHints().setColorScheme(theme_tokens.Qt.ColorScheme.Dark)
        app.setPalette(app.style().standardPalette())
    except (AttributeError, TypeError, RuntimeError):
        pass
    info = theme_tokens.theme_debug_info()
    print("    hint={} 亮度={} scheme={}".format(
        info["hint"], info["window_lightness"], info["scheme"]))
    check("hint 与调色板矛盾时以调色板为准（不返回 dark）",
          not (info["hint"] == "dark" and info["window_lightness"] >= 128
               and info["scheme"] == "dark"),
          str(info))

    print("\n[6] 反复切换 10 轮不漂移")
    ok = True
    for _ in range(10):
        theme_tokens.apply_theme(app, theme_tokens.MODE_DARK)
        if theme_tokens.palette_window_lightness(app) >= 128:
            ok = False
        theme_tokens.apply_theme(app, theme_tokens.MODE_LIGHT)
        if theme_tokens.palette_window_lightness(app) < 128:
            ok = False
    check("10 轮往返亮度始终正确", ok)

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
