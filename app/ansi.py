# -*- coding: utf-8 -*-
"""ANSI 转义序列解析：把 cmd / 终端里的颜色还原到日志区（纯 Python，不依赖 PyQt6）。

为什么需要
----------
机器人程序（Python 的 logging + colorlog/rich、Node 的 chalk、Java 的 Jansi…）
会往 stdout 写 ANSI 转义序列。**管道里没有终端，它们照写不误**，于是启动管理器的
日志区就显示成这种样子（真机截图）：

    ←[32;20m10-02 13:22:52 [INFO] atri-bot.PluginLoader | 插件已加载: poke_reaction←[0m
    ←[38;20m10-02 13:22:53 [DEBUG] atri-bot.whitelist | 群相关事件:{...}←[0m

同一个程序在 Windows Terminal 里是"INFO 绿色、DEBUG 默认色"。本模块负责把
这些序列解析成"文字 + 样式"，具体显示成什么颜色由 `app/ui/theme.py` 决定
（深浅两套色板，颜色只有一个出处）。

设计要点
--------
1. **有状态、按流解析**：一个转义序列可能被 QProcess 从中间切开
   （这一块结尾是 `\\x1b[3`，下一块开头是 `2m`），所以解析器跨块保留
   "半截序列"与"当前样式"。
2. **只认 SGR**（`ESC [ … m`，即颜色/加粗/下划线）。其余控制序列
   （清屏 `ESC[2J`、移动光标、`ESC[?25l` 隐藏光标、OSC 窗口标题…）
   在日志区没有意义，一律丢弃。
3. **认不出来的参数按终端习惯忽略**。真机例子：`ESC[38;20m` —— 38 后面不是
   5（256 色）也不是 2（真彩色），属于残缺的扩展色；Windows Terminal 直接
   不管它、这一行保持默认色，我们也照做。而 `ESC[32;20m` 里的 32 是绿色、
   20（fraktur，罕见）忽略。
4. 颜色统一表达成 :class:`AnsiColor`（0-15 索引 / 0-255 索引 / 真彩色 RGB），
   本模块**不碰**任何十六进制颜色值。

单独运行（文件末尾有自检）：
    python app\\ansi.py
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional, Sequence, Tuple

#: 转义字符（ESC）
ESC = "\x1b"

#: 一块日志结尾留下"半截序列"时最多保留多少字符 —— 防止畸形输出把内存吃光
MAX_PENDING = 4096


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnsiColor:
    """一个 ANSI 颜色值。

    kind = ``"index"``：0-15 是基础色 / 亮色，16-255 是 xterm 256 色；
    kind = ``"rgb"``：真彩色（``ESC[38;2;R;G;Bm``）。
    """

    kind: str = "index"
    index: int = -1
    rgb: Tuple[int, int, int] = (0, 0, 0)

    @staticmethod
    def basic(index: int) -> "AnsiColor":
        """基础 16 色 / 256 色索引。"""
        return AnsiColor(kind="index", index=max(0, min(255, int(index))))

    @staticmethod
    def truecolor(r: int, g: int, b: int) -> "AnsiColor":
        """真彩色。"""
        clamp = lambda v: max(0, min(255, int(v)))  # noqa: E731 - 局部小工具
        return AnsiColor(kind="rgb", rgb=(clamp(r), clamp(g), clamp(b)))


@dataclass(frozen=True)
class AnsiStyle:
    """当前生效的文字样式（由 SGR 序列累积而成）。"""

    fg: Optional[AnsiColor] = None
    bg: Optional[AnsiColor] = None
    bold: bool = False
    faint: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False

    def is_plain(self) -> bool:
        """是不是"什么都没设置"（默认样式）。"""
        return self == AnsiStyle()


#: 一段文字 + 它用的样式
AnsiRun = Tuple[str, AnsiStyle]


# ---------------------------------------------------------------------------
# SGR 解析
# ---------------------------------------------------------------------------

def _parse_params(body: str) -> Optional[List[int]]:
    """把 SGR 的参数部分（`ESC[` 与 `m` 之间）解析成整数列表。

    返回 None 表示这不是一个可用的 SGR：例如 `?25`（私有模式）、`>0`。
    空串按标准当作 0（`ESC[m` 等价于 `ESC[0m`，即重置）。
    """
    if body == "":
        return [0]
    if any(ch not in "0123456789;" for ch in body):
        return None
    params: List[int] = []
    for piece in body.split(";"):
        if piece == "":
            params.append(0)      # `ESC[;32m` 这种空参数按 0 处理
            continue
        try:
            params.append(int(piece))
        except ValueError:
            return None
    return params


def _extended_color(params: Sequence[int], start: int) -> Tuple[Optional[AnsiColor], int]:
    """解析 `38;5;n` / `38;2;r;g;b`（`start` 指向 38/48 后面的第一个参数）。

    返回 ``(颜色或 None, 下一个待处理下标)``。拿不到颜色时**不消耗**多余参数，
    与终端一致（后续参数照常单独生效）。
    """
    if start >= len(params):
        return None, start
    kind = params[start]
    if kind == 5 and start + 1 < len(params):
        return AnsiColor.basic(params[start + 1]), start + 2
    if kind == 2 and start + 3 < len(params):
        return AnsiColor.truecolor(params[start + 1], params[start + 2],
                                   params[start + 3]), start + 4
    return None, start


def apply_sgr(style: AnsiStyle, params: Sequence[int]) -> AnsiStyle:
    """把一条 SGR 的参数应用到样式上，返回新样式（原样式不变）。"""
    result = style
    index = 0
    total = len(params)
    while index < total:
        value = params[index]
        if value == 0:
            result = AnsiStyle()                       # 重置
        elif value == 1:
            result = replace(result, bold=True)
        elif value == 2:
            result = replace(result, faint=True)
        elif value == 3:
            result = replace(result, italic=True)
        elif value == 4:
            result = replace(result, underline=True)
        elif value == 7:
            result = replace(result, inverse=True)
        elif value in (21, 22):
            result = replace(result, bold=False, faint=False)
        elif value == 23:
            result = replace(result, italic=False)
        elif value == 24:
            result = replace(result, underline=False)
        elif value == 27:
            result = replace(result, inverse=False)
        elif 30 <= value <= 37:
            result = replace(result, fg=AnsiColor.basic(value - 30))
        elif value == 39:
            result = replace(result, fg=None)
        elif 40 <= value <= 47:
            result = replace(result, bg=AnsiColor.basic(value - 40))
        elif value == 49:
            result = replace(result, bg=None)
        elif 90 <= value <= 97:
            result = replace(result, fg=AnsiColor.basic(value - 90 + 8))
        elif 100 <= value <= 107:
            result = replace(result, bg=AnsiColor.basic(value - 100 + 8))
        elif value in (38, 48):
            color, next_index = _extended_color(params, index + 1)
            if color is None:
                index += 1          # 残缺的扩展色：忽略这个参数，继续往后看
                continue
            result = replace(result, fg=color) if value == 38 else replace(result, bg=color)
            index = next_index
            continue
        # 其它参数（20 = fraktur、5 = 闪烁…）：忽略，不影响文字内容
        index += 1
    return result


# ---------------------------------------------------------------------------
# 流式解析器
# ---------------------------------------------------------------------------

class AnsiParser:
    """把带 ANSI 序列的文本流切成"文字 + 样式"的片段。

    用法（界面层）：:

        parser = AnsiParser()
        for text, style in parser.feed(chunk):
            ...

    同一个解析器实例要**一直用下去**：它保存着跨块的半截序列与当前样式。
    换了一个程序 / 清空日志时调用 :meth:`reset`。
    """

    def __init__(self) -> None:
        self._pending = ""
        self._style = AnsiStyle()

    # -- 状态 ---------------------------------------------------------------

    @property
    def style(self) -> AnsiStyle:
        """当前样式（跨块保留）。"""
        return self._style

    @property
    def pending(self) -> str:
        """还没收全的半截序列（调试 / 测试用）。"""
        return self._pending

    def reset(self) -> None:
        """清空状态（清空日志、切换程序时调用）。"""
        self._pending = ""
        self._style = AnsiStyle()

    # -- 主入口 -------------------------------------------------------------

    def feed(self, text: object) -> List[AnsiRun]:
        """解析一块文本，返回 ``[(文字, 样式), …]``。

        纯文本（不含转义字符）会走快速路径：整块原样返回一个片段。
        """
        if text is None:
            return []
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return []

        if not self._pending and ESC not in text:
            return [(text, self._style)]        # 快速路径：绝大多数日志都是这种

        data = self._pending + text
        self._pending = ""
        runs: List[AnsiRun] = []
        plain: List[str] = []
        length = len(data)
        index = 0

        while index < length:
            char = data[index]
            if char != ESC:
                plain.append(char)
                index += 1
                continue

            end, new_style = self._scan(data, index)
            if end < 0:
                # 序列还没收全：留给下一块（长度设上限，畸形输出也不会吃内存）
                rest = data[index:]
                if len(rest) <= MAX_PENDING:
                    self._pending = rest
                else:
                    self._style = self._style     # 保持原样，直接丢弃
                index = length
                break

            if plain:
                self._append(runs, "".join(plain), self._style)
                plain.clear()
            if new_style is not None:
                self._style = new_style
            index = end

        if plain:
            self._append(runs, "".join(plain), self._style)
        return runs

    def finish(self) -> List[AnsiRun]:
        """收尾：把残留的半截序列丢掉（正常日志用不到）。"""
        self._pending = ""
        return []

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def _append(runs: List[AnsiRun], text: str, style: AnsiStyle) -> None:
        """追加一个片段；与上一个片段样式相同就合并（少建几个 QTextCharFormat）。"""
        if not text:
            return
        if runs and runs[-1][1] == style:
            runs[-1] = (runs[-1][0] + text, style)
            return
        runs.append((text, style))

    def _scan(self, data: str, start: int) -> Tuple[int, Optional[AnsiStyle]]:
        """从 ``data[start]``（一定是 ESC）开始识别一个转义序列。

        返回 ``(下一个待处理下标, 需要更新的样式或 None)``；
        ``(-1, None)`` 表示序列还没结束，需要等下一块数据。
        """
        length = len(data)
        cursor = start + 1
        if cursor >= length:
            return -1, None                     # 只有半个 ESC

        kind = data[cursor]
        if kind == "[":
            cursor += 1
            body_start = cursor
            while cursor < length and not ("@" <= data[cursor] <= "~"):
                cursor += 1
            if cursor >= length:
                return -1, None                 # CSI 还没结束
            body = data[body_start:cursor]
            final = data[cursor]
            style: Optional[AnsiStyle] = None
            if final == "m":
                params = _parse_params(body)
                if params is not None:
                    style = apply_sgr(self._style, params)
            return cursor + 1, style            # 非 SGR 的 CSI（清屏/移动光标…）直接丢掉

        if kind == "]":
            # OSC（例如设置窗口标题 ESC]0;xxx BEL）：一直吃到 BEL 或 ESC \
            cursor += 1
            while cursor < length:
                char = data[cursor]
                if char == "\x07":
                    return cursor + 1, None
                if char == ESC:
                    if cursor + 1 >= length:
                        return -1, None
                    if data[cursor + 1] == "\\":
                        return cursor + 2, None
                cursor += 1
            return -1, None

        if kind in "()*+-./#% ":
            # 两字符转义后面还有一个字符（如 ESC ( B 选择字符集）
            if cursor + 1 >= length:
                return -1, None
            return cursor + 2, None

        return cursor + 1, None                 # ESC = / ESC > / ESC c 之类


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def strip_ansi(text: object) -> str:
    """去掉全部 ANSI 序列，只留文字（等价于终端里"看到的字"）。

    新建一个解析器，不影响调用方自己的流；结尾残缺的序列会被丢掉。
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if ESC not in text:
        return text
    parser = AnsiParser()
    return "".join(part for part, _style in parser.feed(text))


def has_ansi(text: object) -> bool:
    """这段文本里有没有转义字符（界面层用它走快速路径）。"""
    return isinstance(text, str) and ESC in text


# ---------------------------------------------------------------------------
# 自检：python app\ansi.py
# ---------------------------------------------------------------------------

def _selftest() -> int:
    failures: List[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        print(("  [OK]   " if cond else "  [FAIL] ") + label + (("  " + detail) if detail else ""))
        if not cond:
            failures.append(label)

    def rendered(text: str) -> str:
        parser = AnsiParser()
        return "".join(part for part, _style in parser.feed(text))

    def styles(text: str) -> List[AnsiStyle]:
        parser = AnsiParser()
        return [style for _part, style in parser.feed(text)]

    print("[1] 真机日志里的两种序列")
    # ATRI 实际输出：INFO 绿色（32），DEBUG 是残缺的扩展色（38;20 → 忽略）
    info = "\x1b[32;20m10-02 13:22:52 [INFO] atri-bot.PluginLoader | 插件已加载\x1b[0m\n"
    debug = "\x1b[38;20m10-02 13:22:53 [DEBUG] atri-bot.whitelist | 群相关事件\x1b[0m\n"
    got = rendered(info + debug)
    check("转义序列不再出现在文字里", ESC not in got, repr(got[:40]))
    expect = (info + debug).replace("\x1b[32;20m", "").replace("\x1b[38;20m", "") \
                            .replace("\x1b[0m", "")
    check("文字内容原样保留", got == expect, repr(got[:40]))
    info_styles = styles(info)
    check("INFO 行是绿色（32）", info_styles[0].fg == AnsiColor.basic(2),
          str(info_styles[0].fg))
    check("行尾 ESC[0m 之后回到默认样式", info_styles[-1].is_plain(),
          str(info_styles[-1]))
    debug_styles = styles(debug)
    check("残缺的 38;20 被忽略（保持默认色，和 Windows Terminal 一致）",
          all(style.fg is None for style in debug_styles), str(debug_styles[0].fg))

    print("\n[2] 跨块切开也要认出来")
    parser = AnsiParser()
    first = parser.feed("\x1b[3")
    second = parser.feed("2mhello\x1b[0m world")
    check("半截序列不产生文字", first == [], str(first))
    check("跨块拼起来后文字正确", "".join(p for p, _s in second) == "hello world",
          repr("".join(p for p, _s in second)))
    check("第一段是绿色", second[0][1].fg == AnsiColor.basic(2), str(second[0][1].fg))
    check("ESC[0m 之后恢复默认", second[-1][1].is_plain(), str(second[-1][1]))

    print("\n[3] 颜色要能跨行、跨块延续（终端就是这样）")
    parser = AnsiParser()
    runs = parser.feed("\x1b[31m错误：")
    runs += parser.feed("第二行也是红的\n")
    check("第二块仍然是红色", runs[-1][1].fg == AnsiColor.basic(1), str(runs[-1][1].fg))

    print("\n[4] 256 色与真彩色")
    check("38;5;208 → 256 色索引 208",
          styles("\x1b[38;5;208mx")[0].fg == AnsiColor.basic(208),
          str(styles("\x1b[38;5;208mx")[0].fg))
    check("38;2;255;128;0 → 真彩色",
          styles("\x1b[38;2;255;128;0mx")[0].fg == AnsiColor.truecolor(255, 128, 0),
          str(styles("\x1b[38;2;255;128;0mx")[0].fg))
    check("48;5;236 → 背景色",
          styles("\x1b[48;5;236mx")[0].bg == AnsiColor.basic(236),
          str(styles("\x1b[48;5;236mx")[0].bg))
    check("残缺的 38;5（没有索引）不报错也不上色",
          styles("\x1b[38;5mx")[0].fg is None, str(styles("\x1b[38;5mx")[0].fg))

    print("\n[5] 加粗 / 下划线 / 反显 与复位")
    style = styles("\x1b[1;4;7mx")[0]
    check("加粗 + 下划线 + 反显", style.bold and style.underline and style.inverse, str(style))
    check("22/24/27 分别复位", styles("\x1b[1;4;7;22;24;27mx")[0].is_plain(),
          str(styles("\x1b[1;4;7;22;24;27mx")[0]))
    check("ESC[m 等价于 ESC[0m",
          styles("\x1b[32mA\x1b[mB")[-1].is_plain(), str(styles("\x1b[32mA\x1b[mB")[-1]))
    check("空参数按 0 处理（ESC[;32m = 重置 + 绿色）",
          styles("\x1b[;32mx")[0].fg == AnsiColor.basic(2), str(styles("\x1b[;32mx")[0].fg))

    print("\n[6] 其它控制序列一律丢掉、不留痕迹")
    noisy = "\x1b[2J\x1b[H\x1b[?25l\x1b]0;窗口标题\x07\x1b(B正常文字\x1b[?25h\n"
    check("清屏/光标/私有模式/OSC/字符集都被吃掉", rendered(noisy) == "正常文字\n",
          repr(rendered(noisy)))
    check("私有模式 ESC[?25l 不会当成 SGR", styles("\x1b[?25lx")[0].is_plain())

    print("\n[7] strip_ansi / 快速路径")
    check("strip_ansi 去掉颜色只留文字",
          strip_ansi("\x1b[31m红\x1b[0m字") == "红字", repr(strip_ansi("\x1b[31m红\x1b[0m字")))
    check("纯文本原样返回（快速路径）", strip_ansi("普通日志\n") == "普通日志\n")
    check("has_ansi 判定", has_ansi("\x1b[31m") and not has_ansi("普通日志"))

    print("\n[8] 畸形输入不能吃内存 / 不能抛异常")
    parser = AnsiParser()
    parser.feed("\x1b[" + "9" * (MAX_PENDING * 2))
    check("超长未结束序列被丢弃", len(parser.pending) <= MAX_PENDING,
          str(len(parser.pending)))
    render = rendered("\x1b") + rendered("[") + rendered("\x1b]no-terminator")
    check("孤立 ESC / 未结束 OSC 不抛异常", isinstance(render, str))

    print("\n结果：", "全部通过" if not failures else "失败项 = {}".format(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
