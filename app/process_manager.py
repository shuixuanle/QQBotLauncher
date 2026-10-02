# -*- coding: utf-8 -*-
"""进程管理模块：基于 QProcess 启动 / 监控 / 停止机器人程序。

职责
----
1. 用 QProcess 启动程序，正确设置工作目录与环境变量。
2. 实时捕获输出（stdout + stderr），通过 Qt 信号逐行发给 UI。
3. 输出编码优先按 UTF-8 解码，失败时回退 GBK，并且"一个进程只选一次编码"，
   避免日志出现乱码抖动（cmd / java 在中文 Windows 上默认是 GBK）。
4. 停止进程树：Windows 下调用 taskkill /T /PID（必要时再加 /F），
   确保 java 启动的子进程不会变成孤儿进程。
5. 自动选择最新 jar：扫描工作目录下所有 .jar，按修改时间取最新，
   替换命令中的 [LATEST_JAR] 占位符。

线程模型
--------
全部逻辑运行在 Qt 主线程，QProcess 的信号在事件循环中派发，
因此 UI 可以直接连接本模块的信号，无需额外加锁。
"""

from __future__ import annotations

import codecs
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    QObject,
    QProcess,
    QProcessEnvironment,
    QTimer,
    pyqtSignal,
)

# 允许 "python app/process_manager.py" 直接运行自检
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import (  # noqa: E402
    LEGACY_JAR_PLACEHOLDERS,
    BotConfig,
    Program,
    ROLE_PRIMARY,
    find_latest_jar as config_find_latest_jar,
    format_command_line,
    split_command_line,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 命令中代表"最新 jar 包"的占位符
JAR_TOKEN = "[LATEST_JAR]"

#: 兼容写法（与 app.config.LEGACY_JAR_PLACEHOLDERS 保持一致）
LEGACY_JAR_TOKENS = LEGACY_JAR_PLACEHOLDERS

#: 输出编码探测顺序：先 UTF-8，失败回退 GBK
ENCODING_PREFERENCE = ("utf-8", "gbk")

#: 单行日志最大长度，超出则截断，避免刷屏卡死 UI
MAX_LINE_LENGTH = 8192

#: 默认优雅停止等待时间（毫秒），超时后强制结束
DEFAULT_STOP_TIMEOUT_MS = 10000

#: taskkill 的 PID 提取正则（此处只用于解析自身输出的失败信息）
TASKKILL_EXE = "taskkill"

#: 需要交给命令解释器执行的扩展名
_SHELL_EXTENSIONS = (".bat", ".cmd")

#: Windows 上创建窗口的进程（例如图形化启动器）
_USE_SHELL_ON_WINDOWS = True


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------

@dataclass
class RunningProcess:
    """一个正在被管理的进程及其运行时状态。"""

    #: 管理器内部唯一键（同一程序重复启动时会自动加序号）
    key: str
    #: 程序配置 id
    program_id: str
    #: 展示名（"机器人名 / 程序名"）
    display_name: str
    #: QProcess 实例（自己持有所有权，销毁时随 Python 对象回收）
    process: QProcess
    #: 启动时使用的 argv（用于日志与重启）
    argv: List[str] = field(default_factory=list)
    #: 启动时使用的工作目录
    cwd: str = ""
    #: 启动时使用的环境变量
    env: Dict[str, str] = field(default_factory=dict)
    #: 输出编码：None 表示尚未判定，判定后固定不再更换
    encoding: Optional[str] = None
    #: 输出解码器（懒创建，逐进程独立）
    decoder: Optional["OutputDecoder"] = None
    #: 行缓冲（\r 与 \n 都当作换行）
    line_buffer: str = ""
    #: 是否为本次启动错误（用于以"失败"而非"已退出"呈现）
    launch_failed: bool = False
    #: 是否正在请求停止（区分自然崩溃与手动停止）
    stopping: bool = False
    #: 是否在启动阶段（用于区分 FailedToStart 与运行中崩溃）
    starting: bool = True
    #: 是否等待重启
    restart_pending: bool = False
    #: 启动时的配置快照（无外部配置回调时用于重启）
    program_snapshot: Optional[Program] = None
    #: 启动时间戳
    started_at: float = field(default_factory=time.time)
    #: 上一次已知的进程 ID
    last_pid: int = 0

    @property
    def pid(self) -> int:
        """当前进程 ID，未运行时返回 0。"""
        try:
            if self.process.state() != QProcess.ProcessState.NotRunning:
                return int(self.process.processId() or 0)
        except (RuntimeError, AttributeError):
            return 0
        return int(self.last_pid or 0)

    @property
    def is_running(self) -> bool:
        try:
            return self.process.state() != QProcess.ProcessState.NotRunning
        except (RuntimeError, AttributeError):
            return False


# ---------------------------------------------------------------------------
# 输出解码器：UTF-8 优先，GBK 回退，逐进程粘性选择
# ---------------------------------------------------------------------------

def _looks_like_utf8(data: bytes) -> bool:
    """判断字节串是否是完整的合法 UTF-8（不含被截断的多字节序列）。"""
    if not data:
        return True
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        # 只有"该序列尚未接收完整"才允许等待更多数据，其余情况判为非法
        try:
            data[: exc.start].decode("utf-8")
        except UnicodeDecodeError:
            return False
        reason = str(exc.reason).lower()
        return "unexpected end of data" in reason


def preferred_encoding(data: bytes, current: Optional[str]) -> Optional[str]:
    """根据已收到的字节判定编码，返回 "utf-8" / "gbk" / None（不确定）。"""
    if current:
        return current
    if not data:
        return None
    if _looks_like_utf8(data):
        return "utf-8"
    try:
        data.decode("gbk")
        return "gbk"
    except (UnicodeDecodeError, LookupError):
        # 两者都失败，交给默认选择（UTF-8 + replace）
        return "utf-8"


class OutputDecoder:
    """把 QProcess 的文本块安全地还原成字节并解码为字符串。

    QProcess 在 Windows 上会按系统本地编码（中文系统为 GBK）解码子进程输出，
    再以 QString 形式给出。为了做"UTF-8 优先、GBK 回退"的判断，
    这里先按 latin-1 逐字符取回原始字节，再用 codecs 增量解码器处理
    跨块截断的多字节序列，最后统一按 CPU 编码还原文本。
    """

    def __init__(self, encoding: Optional[str] = None) -> None:
        self.encoding: Optional[str] = encoding
        self._decoder: Optional[codecs.IncrementalDecoder] = None
        self._pending: bytes = b""

    def set_encoding(self, encoding: str) -> None:
        """锁定编码并重建增量解码器。"""
        if encoding == self.encoding and self._decoder is not None:
            return
        self.encoding = encoding
        try:
            self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        except LookupError:
            self.encoding = "utf-8"
            self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        if self._pending:
            # 把等待中的字节交给新解码器
            buffered, self._pending = self._pending, b""
            self._decoder.decode(buffered, False)

    def feed(self, text: str) -> str:
        """喂入一段 QProcess 输出，返回可显示的文本（可能为空）。"""
        if not text:
            return ""
        # 1) 取回原始字节
        try:
            raw = text.encode("latin-1")
        except UnicodeEncodeError:
            # QProcess 已经把内容解成了非 latin-1 字符，说明它就是 Unicode，
            # 此时直接用 UTF-8 编码，保证不丢字符。
            raw = text.encode("utf-8", errors="replace")

        # 2) 判定编码
        if self.encoding is None:
            decided = preferred_encoding(raw, None)
            if decided is None:
                # 只有被截断的多字节前缀，等待下一块
                self._pending = raw
                return ""
            self.set_encoding(decided)
        elif self._decoder is None:
            self.set_encoding(self.encoding)

        raw = self._pending + raw
        self._pending = b""

        # 3) 解码；若粘性编码失败，则用另一种编码重试一次
        assert self._decoder is not None
        raw = self._hold_incomplete_tail(raw)
        try:
            return self._decoder.decode(raw, False)
        except (UnicodeDecodeError, LookupError):
            fallback = "gbk" if self.encoding != "gbk" else "utf-8"
            self.set_encoding(fallback)
            assert self._decoder is not None
            try:
                return self._decoder.decode(raw, False)
            except (UnicodeDecodeError, LookupError):
                return raw.decode("utf-8", errors="replace")

    def _hold_incomplete_tail(self, raw: bytes) -> bytes:
        """把末尾可能被截断的多字节序列留到下一次解码。"""
        if not raw:
            return raw
        limit = min(3, len(raw))
        for back in range(1, limit + 1):
            tail = raw[-back:]
            if len(tail) == 1:
                lead = tail[0]
                if lead < 0x80:
                    return raw
                if 0xC2 <= lead <= 0xDF:
                    need = 2
                elif 0xE0 <= lead <= 0xEF:
                    need = 3
                elif 0xF0 <= lead <= 0xF4:
                    need = 4
                else:
                    return raw
                if need > back:
                    self._pending = tail
                    return raw[:-back]
                return raw
            # 尾部若干个连续的多字节前缀
            if all(0x80 <= byte <= 0xBF for byte in tail[1:]) and tail[0] >= 0xC2:
                if tail[0] <= 0xDF:
                    need = 2
                elif tail[0] <= 0xEF:
                    need = 3
                else:
                    need = 4
                if need > back:
                    self._pending = tail
                    return raw[:-back]
                return raw
        return raw

    def flush(self) -> str:
        """进程结束时取出残留内容。"""
        if self._decoder is None and self._pending:
            self.set_encoding(self.encoding or "utf-8")
        result = ""
        if self._pending and self._decoder is not None:
            try:
                result += self._decoder.decode(self._pending, True)
            except (UnicodeDecodeError, LookupError):
                result += self._pending.decode("utf-8", errors="replace")
            self._pending = b""
        return result


def split_lines(buffer: str, chunk: str) -> Tuple[List[str], str]:
    """把 chunk 追加到 buffer，按 \\r / \\n / \\r\\n 切分出完整行。"""
    text = buffer + chunk
    lines: List[str] = []
    current: List[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\r":
            lines.append("".join(current))
            current = []
            if index + 1 < length and text[index + 1] == "\n":
                index += 2
                continue
        elif char == "\n":
            lines.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    return lines, "".join(current)


# ---------------------------------------------------------------------------
# 进程管理器
# ---------------------------------------------------------------------------

class ProcessManager(QObject):
    """管理多个程序进程的启动、日志转发与停止。

    信号约定（key 为管理器内部唯一键，通常等于程序 id）：
        state_changed(key, state, message)  状态机变化
        output_line(key, line, channel)     一行输出，channel 为 stdout/stderr
        output_text(key, text, channel)     UI 可直接追加的文本（带换行）
        log_message(key, message)           管理器自身日志（启动命令、错误等）
        process_started(key, pid)
        process_finished(key, exit_code, exit_status, expected)
        process_error(key, message)
    """

    state_changed = pyqtSignal(str, str, str)
    output_line = pyqtSignal(str, str, str)
    output_text = pyqtSignal(str, str, str)
    log_message = pyqtSignal(str, str)
    process_started = pyqtSignal(str, int)
    process_finished = pyqtSignal(str, int, str, bool)
    process_error = pyqtSignal(str, str)

    # 状态取值
    STATE_IDLE = "idle"
    STATE_STARTING = "starting"
    STATE_RUNNING = "running"
    STATE_STOPPING = "stopping"
    STATE_STOPPED = "stopped"
    STATE_FAILED = "failed"

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._entries: "Dict[str, RunningProcess]" = {}
        self._kill_timers: Dict[str, QTimer] = {}
        self._shutting_down = False
        #: 最近一次任务的基准目录（重启时复用）
        self._base_dir_cache: Dict[str, str] = {}
        self._config_provider = None  # 可选：回调，返回最新的 Program 配置

    # ------------------------------------------------------------------
    # 配置来源
    # ------------------------------------------------------------------

    def set_config_provider(self, provider) -> None:
        """注册"按 key 取最新 Program 配置"的回调，用于重启时读取最新配置。

        provider 签名：provider(program_id) -> Optional[Program]
        """
        self._config_provider = provider

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def has(self, key: str) -> bool:
        """是否管理着该 key 的进程（含已退出的记录）。"""
        return key in self._entries

    def get(self, key: str) -> Optional[RunningProcess]:
        """取运行时记录。"""
        return self._entries.get(key)

    def is_running(self, key: str) -> bool:
        """该 key 对应的进程是否仍在运行。"""
        entry = self._entries.get(key)
        return bool(entry and entry.is_running)

    def pid(self, key: str) -> int:
        """该 key 对应进程的 PID，未运行时为 0。"""
        entry = self._entries.get(key)
        return entry.pid if entry else 0

    def state(self, key: str) -> str:
        """粗略状态：running / starting / stopping / not_running。"""
        entry = self._entries.get(key)
        if entry is None:
            return self.STATE_IDLE
        if entry.stopping:
            return self.STATE_STOPPING
        if entry.starting:
            return self.STATE_STARTING
        if entry.is_running:
            return self.STATE_RUNNING
        return self.STATE_STOPPED

    def keys(self) -> List[str]:
        """所有受管理的 key。"""
        return list(self._entries.keys())

    def running_keys(self) -> List[str]:
        """仍在运行的 key。"""
        return [key for key, entry in self._entries.items() if entry.is_running]

    @property
    def running_count(self) -> int:
        """运行中的进程数量。"""
        return len(self.running_keys())

    def describe(self) -> str:
        """状态摘要，便于状态栏显示。"""
        total = len(self._entries)
        running = self.running_count
        return "运行中 {} / 共 {} 个进程".format(running, total)

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------

    def start(
        self,
        manager_key: Optional[str],
        program: Program,
        base_dir,
        display_name: str = "",
        argv: Optional[List[str]] = None,
    ) -> str:
        """启动一个程序。

        参数
        ----
        manager_key : 唯一键，通常传 program.id；为 None 时自动由程序 id 生成
        program     : app.config.Program 配置对象
        base_dir    : 相对路径基准目录（通常是 BotConfig.base_dir）
        display_name: 用于日志的名字，例如 "示例机器人 / 主程序"
        argv        : 可选，直接指定命令行参数（否则由 program.command_argv 生成）

        返回实际使用的 key；启动失败时也会返回 key，并通过信号报告错误。
        """
        name = display_name or program.display_name
        key = self._make_key(manager_key or program.id or name)

        existing = self._entries.get(key)
        if existing is not None and existing.is_running:
            message = "{} 已在运行（PID {}），忽略本次启动。".format(name, existing.pid)
            self._log(key, message)
            self._emit("state_changed", key, self.STATE_RUNNING, message)
            return key

        # 1) 解析命令与工作目录
        resolved_argv = list(argv) if argv else self._resolve_argv(program, base_dir)
        if not resolved_argv:
            message = "{} 未配置启动命令，已跳过。".format(name)
            self._log(key, message)
            self._emit("state_changed", key, self.STATE_FAILED, message)
            self._emit("process_error", key, message)
            return key

        cwd = program.working_dir(base_dir)
        env = program.merged_env(base_dir)

        # 2) 清理旧记录并创建进程对象
        old = self._entries.pop(key, None)
        if old is not None:
            self._dispose(old)

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setWorkingDirectory(cwd)
        process.setProcessEnvironment(self._build_environment(env))

        self._base_dir_cache[key] = str(base_dir)
        entry = RunningProcess(
            key=key,
            program_id=program.id or key,
            display_name=name,
            process=process,
            argv=list(resolved_argv),
            cwd=cwd,
            env=env,
            program_snapshot=program.copy(),
        )
        entry.started_at = time.time()
        self._entries[key] = entry

        program_argv = self._wrap_for_shell(resolved_argv)
        program_exe = program_argv[0]
        program_args = program_argv[1:]

        # 3) 决定最终命令行
        #    via_shell=True 时整条命令交给 cmd.exe /c 执行（&& 、| 、> 等语法原样生效），
        #    且不能再做 shell 包装，否则会变成 cmd /c cmd /c "..."。
        if getattr(program, "via_shell", False):
            program_argv = list(resolved_argv)
        else:
            program_argv = self._wrap_for_shell(resolved_argv)
        if not program_argv:
            message = "{} 生成的命令行为空，已跳过。".format(name)
            self._log(key, message)
            self._emit("state_changed", key, self.STATE_FAILED, message)
            self._emit("process_error", key, message)
            return key
        program_exe = program_argv[0]
        program_args = program_argv[1:]

        # 4) 连接信号（使用 key 做闭包参数，避免多实例串台）
        try:
            process.readyReadStandardOutput.connect(lambda k=key: self._on_ready_read(k))
            process.started.connect(lambda k=key: self._on_started(k))
            process.finished.connect(
                lambda code, status, k=key: self._on_finished(k, code, status)
            )
            process.errorOccurred.connect(
                lambda error, k=key: self._on_error_occurred(k, error)
            )
        except (RuntimeError, TypeError) as exc:
            message = "{} 无法连接进程信号：{}".format(name, exc)
            self._log(key, message)
            self._emit("state_changed", key, self.STATE_FAILED, message)
            self._emit("process_error", key, message)
            return key

        # 5) 记录并启动
        self._log(key, "启动命令：{}".format(format_command_line(program_argv)))
        self._log(key, "工作目录：{}".format(cwd))
        if getattr(program, "via_shell", False):
            self._log(key, "该程序通过命令解释器启动（支持 && 、| 、> 等语法）。")
        if program.auto_latest_jar and not argv:
            if JAR_TOKEN in (program.command or "") or any(
                JAR_TOKEN in token for token in resolved_argv
            ):
                self._log(key, "已启用自动选择最新 jar：{}".format(
                    resolved_argv[-1] if resolved_argv else "")
                )
        self._emit("state_changed", key, self.STATE_STARTING, "正在启动 {}".format(name))
        entry.starting = True
        process.start(program_exe, program_args)
        return key

    def _resolve_argv(self, program: Program, base_dir) -> List[str]:
        """生成 argv：先取配置命令，再替换最新 jar 占位符。"""
        # config.command_argv 已经处理过 {jar}，这里再统一处理 [LATEST_JAR]
        argv = program.command_argv(base_dir)
        if not argv:
            argv = split_command_line(program.resolved_command(base_dir))
        if not argv:
            return []

        cwd = program.working_dir(base_dir)
        need_jar = bool(program.auto_latest_jar) or self._contains_jar_token(argv)
        if not need_jar:
            return argv

        jar_path = self.find_latest_jar(cwd)
        if jar_path is None:
            self._log(program.id or program.name,
                      "未在 {} 中找到任何 .jar 文件，命令保持原样。".format(cwd))
            return argv

        result: List[str] = []
        replaced = False
        for token in argv:
            new_token = token
            for placeholder in (JAR_TOKEN,) + LEGACY_JAR_TOKENS:
                if placeholder in new_token:
                    new_token = new_token.replace(placeholder, jar_path)
                    replaced = True
            result.append(new_token)
        if not replaced:
            # 命令里没有占位符，但明确要求自动选 jar：追加到末尾
            result.append(jar_path)
        return result

    @staticmethod
    def _contains_jar_token(argv: List[str]) -> bool:
        for token in argv:
            for placeholder in (JAR_TOKEN,) + LEGACY_JAR_TOKENS:
                if placeholder in token:
                    return True
        return False

    @staticmethod
    def find_latest_jar(directory) -> Optional[str]:
        """扫描目录下所有 .jar（含一级子目录），按修改时间取最新，返回绝对路径。

        directory 允许传 str 或 Path（Program.working_dir() 返回 str），
        内部统一转成 Path 再交给 app.config.find_latest_jar。
        """
        if not directory:
            return None
        try:
            target = directory if isinstance(directory, Path) else Path(str(directory))
        except (TypeError, ValueError, OSError):
            return None
        found = config_find_latest_jar(target)
        if found is None:
            return None
        try:
            return str(found)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _build_environment(env: Dict[str, str]) -> QProcessEnvironment:
        """基于系统环境 + 程序自定义变量构造 QProcessEnvironment。"""
        qenv = QProcessEnvironment.systemEnvironment()
        for key, value in (env or {}).items():
            key_text = str(key)
            if not key_text:
                continue
            qenv.insert(key_text, "" if value is None else str(value))
        return qenv

    @staticmethod
    def _wrap_for_shell(argv: List[str]) -> List[str]:
        """必要时用命令解释器包裹（.bat/.cmd 无法被 CreateProcess 直接执行）。"""
        if not argv:
            return argv
        executable = argv[0]
        lowered = executable.lower()
        needs_shell = lowered.endswith(_SHELL_EXTENSIONS) or lowered in ("start", "call")
        if not needs_shell:
            return argv
        if os.name == "nt":
            shell = os.environ.get("COMSPEC") or "cmd.exe"
            return [shell, "/c"] + list(argv)
        return ["/bin/sh", "-c", " ".join(argv)]

    @staticmethod
    def _format_command(argv: List[str]) -> str:
        """把 argv 拼成便于阅读的命令行。

        使用 app.config.format_command_line：含空格的参数加双引号，
        内部引号按 Windows 规则写成两个双引号（不能用 \\" ，cmd 不认识反斜杠转义，
        否则会报 'xxx' is not recognized as an internal or external command）。
        """
        return format_command_line(argv)

    def _make_key(self, base_key: str) -> str:
        """生成不与运行中进程冲突的 key。"""
        key = base_key or "program"
        existing = self._entries.get(key)
        if existing is None or not existing.is_running:
            return key
        index = 2
        while True:
            candidate = "{}#{}".format(key, index)
            entry = self._entries.get(candidate)
            if entry is None or not entry.is_running:
                return candidate
            index += 1

    # ------------------------------------------------------------------
    # 输出处理
    # ------------------------------------------------------------------

    def _on_ready_read(self, key: str) -> None:
        """读取当前可用的所有输出并逐行派发。"""
        entry = self._entries.get(key)
        if entry is None:
            return
        try:
            raw_text = bytes(entry.process.readAllStandardOutput()).decode(
                "latin-1", errors="replace"
            )
        except (RuntimeError, AttributeError):
            return

        decoder = entry.decoder
        if decoder is None:
            decoder = OutputDecoder(entry.encoding)
            entry.decoder = decoder
        text = decoder.feed(raw_text)
        entry.encoding = decoder.encoding
        if not text:
            return

        lines, entry.line_buffer = split_lines(entry.line_buffer, text)
        for line in lines:
            self._emit_line(key, line)

        # 防止长时间无换行的输出撑爆内存
        if len(entry.line_buffer) > MAX_LINE_LENGTH:
            self._emit_line(key, entry.line_buffer)
            entry.line_buffer = ""

    def _emit_line(self, key: str, line: str) -> None:
        """派发一行输出。"""
        entry = self._entries.get(key)
        if entry is None:
            return
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + " …（已截断）"
        self._emit("output_line", key, line, "stdout")
        self._emit("output_text", key, line + "\n", "stdout")

    def _flush_buffer(self, key: str) -> None:
        """进程退出时把残留的缓冲内容与未解码字节输出掉。"""
        entry = self._entries.get(key)
        if entry is None:
            return
        decoder = entry.decoder
        if decoder is not None:
            tail = decoder.flush()
            if tail:
                lines, entry.line_buffer = split_lines(entry.line_buffer, tail)
                for line in lines:
                    self._emit_line(key, line)
        if entry.line_buffer:
            self._emit_line(key, entry.line_buffer)
            entry.line_buffer = ""

    # ------------------------------------------------------------------
    # 进程状态回调
    # ------------------------------------------------------------------

    def _on_started(self, key: str) -> None:
        entry = self._entries.get(key)
        if entry is None:
            return
        entry.starting = False
        entry.stopping = False
        try:
            entry.last_pid = int(entry.process.processId() or 0)
        except (RuntimeError, AttributeError):
            entry.last_pid = 0
        self._log(key, "{} 已启动，PID = {}".format(entry.display_name, entry.pid or "未知"))
        self._emit("state_changed", key, self.STATE_RUNNING, "运行中（PID {}）".format(entry.pid))
        self._emit("process_started", key, entry.pid)

    def _on_finished(self, key: str, exit_code: int, exit_status) -> None:
        entry = self._entries.get(key)
        if entry is None:
            return

        self._flush_buffer(key)
        try:
            status_text = {
                QProcess.ExitStatus.NormalExit: "正常退出",
                QProcess.ExitStatus.CrashExit: "异常终止",
            }.get(exit_status, str(exit_status))
        except (AttributeError, TypeError):
            status_text = str(exit_status)

        expected = bool(entry.stopping) or self._shutting_down
        restart_pending = bool(entry.restart_pending) and not self._shutting_down

        # 关键：Windows 上停止用的是 `taskkill` 子进程，"它结束了"不代表
        # "被它杀的程序结束了" —— 两个 QProcess 都会触发 finished。若在 taskkill
        # 结束时就把重启做掉，真正的进程可能还没退，或者紧接着的第二次 finished
        # 会把刚启动的新进程状态搞乱。所以只要真正的进程还在跑，就等它。
        if restart_pending and entry.is_running and entry.process is not None:
            self._log(key, "{} 的停止动作仍在进行，等进程真正退出后再重启。".format(
                entry.display_name))
            return

        entry.starting = False
        self._cancel_kill_timer(key)

        if restart_pending:
            self._log(key, "{} 已停止，准备重启。".format(entry.display_name))
            self._emit("state_changed", key, self.STATE_STOPPED, "已停止，准备重启")
        elif expected:
            self._log(key, "{} 已停止（{}，退出码 {}）。".format(
                entry.display_name, status_text, exit_code))
            self._emit("state_changed", key, self.STATE_STOPPED, "已停止")
        else:
            self._log(key, "{} 已退出（{}，退出码 {}）。".format(
                entry.display_name, status_text, exit_code))
            state = self.STATE_FAILED if exit_code not in (0, None) else self.STATE_STOPPED
            self._emit("state_changed", key, state, "{}，退出码 {}".format(status_text, exit_code))

        entry.stopping = False
        self._emit("process_finished", key, int(exit_code), status_text, expected)

        if restart_pending:
            program = self._lookup_program(entry)
            base_dir = self._base_dir_cache.get(key, entry.cwd or os.getcwd())
            display_name = entry.display_name
            entry.restart_pending = False
            if program is None:
                self._log(key, "缺少程序配置，已取消重启。")
                self._emit("process_error", key, "缺少程序配置，已取消重启")
            else:
                self._entries.pop(key, None)
                self._dispose(entry)
                self._log(key, "正在重启 {}…".format(display_name))
                self._emit("state_changed", key, self.STATE_STARTING, "正在重启…")
                QTimer.singleShot(
                    300,
                    lambda k=key, p=program, b=base_dir, n=display_name: self.start(k, p, b, n),
                )

    def _on_error_occurred(self, key: str, error) -> None:
        entry = self._entries.get(key)
        if entry is None:
            return
        try:
            name = {
                QProcess.ProcessError.FailedToStart: "无法启动（找不到程序或没有执行权限）",
                QProcess.ProcessError.Crashed: "进程崩溃",
                QProcess.ProcessError.Timedout: "启动超时",
                QProcess.ProcessError.WriteError: "写入进程失败",
                QProcess.ProcessError.ReadError: "读取进程输出失败",
                QProcess.ProcessError.UnknownError: "未知错误",
            }.get(error, str(error))
        except (AttributeError, TypeError):
            name = str(error)

        if error == QProcess.ProcessError.FailedToStart:
            entry.launch_failed = True
            entry.starting = False
            self._log(key, "{} 启动失败：{}（命令：{}）".format(
                entry.display_name, name, self._format_command(entry.argv)))
            self._emit("state_changed", key, self.STATE_FAILED, "启动失败：{}".format(name))
        elif not entry.stopping and not self._shutting_down:
            self._log(key, "{} 发生错误：{}".format(entry.display_name, name))
            self._emit("state_changed", key, self.STATE_FAILED, name)
        else:
            self._log(key, "{} 停止过程中报告：{}".format(entry.display_name, name))

        self._emit("process_error", key, name)

    # ------------------------------------------------------------------
    # 停止
    # ------------------------------------------------------------------

    def stop(
        self,
        key: str,
        timeout_ms: int = DEFAULT_STOP_TIMEOUT_MS,
        force: bool = False,
        keep_restart_pending: bool = False,
    ) -> bool:
        """停止进程（含整棵进程树）。

        返回是否已发出停止请求；进程可能仍在优雅退出的过程中，
        真正结束时会发出 process_finished 信号。

        keep_restart_pending
        -------------------
        "这次停止是重启的第一步"时传 True —— **不要**清掉 ``restart_pending``。

        真机事故："重启似乎在需要强制停止时，无法重新打开"。原因是
        :meth:`restart` 先设 ``restart_pending = True`` 再调用本方法，而本方法
        无条件把它清成 False，于是 ``_on_finished`` 看到的是"普通停止"，
        重启永远不会发生（只有当进程恰好已退出、走到 restart 里那条
        "未在运行，直接启动"的分支时才会碰巧成功）。
        """
        entry = self._entries.get(key)
        if entry is None or not entry.is_running:
            return False

        pid = entry.pid
        entry.stopping = True
        if not keep_restart_pending:
            entry.restart_pending = False
        self._emit("state_changed", key, self.STATE_STOPPING, "正在停止…")
        self._log(key, "正在停止 {}（PID {}）…".format(entry.display_name, pid or "未知"))

        if os.name == "nt" and pid:
            # 先不加 /F，给程序一次清理自身状态的机会
            self._taskkill_async(key, pid, force=force)
        else:
            try:
                entry.process.terminate()
            except (RuntimeError, AttributeError):
                pass

        if force:
            self._schedule_force_kill(key, pid, 1500)
        else:
            self._schedule_force_kill(key, pid, max(1000, int(timeout_ms)))
        return True

    def kill(self, key: str) -> bool:
        """强制结束进程树（taskkill /T /F）。"""
        return self.stop(key, timeout_ms=0, force=True)

    def _taskkill_async(self, key: str, pid: int, force: bool) -> None:
        """异步调用 taskkill /T /PID（可选 /F），不阻塞 UI。"""
        killer = QProcess(self)
        killer.setProgram(TASKKILL_EXE)
        args = ["/T"]
        if force:
            args.append("/F")
        args += ["/PID", str(pid)]
        killer.setArguments(args)

        def _done(*_args) -> None:
            try:
                output = bytes(killer.readAllStandardOutput()).decode("gbk", errors="replace")
                error = bytes(killer.readAllStandardError()).decode("gbk", errors="replace")
            except (RuntimeError, AttributeError):
                output, error = "", ""
            code = 0
            try:
                code = int(killer.exitCode() or 0)
            except (RuntimeError, AttributeError):
                code = 0
            if code != 0 and not self._shutting_down:
                entry = self._entries.get(key)
                name = entry.display_name if entry else key
                detail = (error or output).strip()
                # 退出码 128 是"优雅停止失败、需要强制"——对**进程树**来说是常态：
                # cmd.exe / powershell 这类中间宿主不响应 WM_CLOSE，taskkill /T
                # （不带 /F）必然拒绝。所以这里不再用"失败"这种吓人的说法，
                # 而是说明"下一拍会强制结束"，避免误以为出错了。
                if code == 128:
                    self._log(key, "{} 需要强制结束（退出码 128：进程树里有不响应"
                                   "关闭请求的子进程），即将结束整棵进程树。".format(name))
                else:
                    self._log(key, "taskkill 结束 {} 未成功（退出码 {}）：{}".format(
                        name, code, detail or "进程可能已经退出"))
            killer.deleteLater()

        killer.finished.connect(_done)
        killer.errorOccurred.connect(lambda _err: _done())
        killer.start()

    def _schedule_force_kill(self, key: str, pid: int, delay_ms: int) -> None:
        """超时后仍未退出，则强制结束整棵进程树。"""
        self._cancel_kill_timer(key)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(max(0, int(delay_ms)))

        def _on_timeout(k=key, p=pid) -> None:
            entry = self._entries.get(k)
            if entry is None or not entry.is_running:
                return
            self._log(k, "{} 在宽限期内没有自行退出，正在强制结束整棵进程树"
                         "（PID {}）——这是正常的两段式停止，不是错误。".format(
                             entry.display_name, p or entry.pid))
            if os.name == "nt" and (p or entry.pid):
                self._taskkill_async(k, p or entry.pid, force=True)
                # 再给 3 秒，仍不退出就直接 kill 句柄
                self._schedule_hard_kill(k, 3000)
            else:
                try:
                    entry.process.kill()
                except (RuntimeError, AttributeError):
                    pass

        timer.timeout.connect(_on_timeout)
        self._kill_timers[key] = timer
        timer.start()

    def _schedule_hard_kill(self, key: str, delay_ms: int) -> None:
        """最后一招：直接 kill QProcess 句柄。"""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(max(0, int(delay_ms)))

        def _on_timeout(k=key) -> None:
            entry = self._entries.get(k)
            if entry is None or not entry.is_running:
                return
            self._log(k, "taskkill 未能结束进程，直接终止句柄（PID {}）。".format(entry.pid))
            try:
                entry.process.kill()
            except (RuntimeError, AttributeError):
                pass

        timer.timeout.connect(_on_timeout)
        self._kill_timers[key] = timer
        timer.start()

    def _cancel_kill_timer(self, key: str) -> None:
        timer = self._kill_timers.pop(key, None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # 重启
    # ------------------------------------------------------------------

    def restart(
        self,
        key: str,
        timeout_ms: int = DEFAULT_STOP_TIMEOUT_MS,
    ) -> bool:
        """停止后自动重新启动（配置优先取 _config_provider，其次取启动时快照）。"""
        entry = self._entries.get(key)
        if entry is None:
            self._log(key, "没有可重启的进程记录。")
            return False

        program = self._lookup_program(entry)
        if program is None:
            self._log(key, "找不到 {} 的程序配置，无法重启。".format(entry.display_name))
            self._emit("process_error", key, "缺少程序配置，无法重启")
            return False

        base_dir = self._base_dir_cache.get(key, entry.cwd or os.getcwd())
        display_name = entry.display_name

        if not entry.is_running:
            self._entries.pop(key, None)
            self._dispose(entry)
            self._log(key, "{} 未在运行，直接启动。".format(display_name))
            self._emit("state_changed", key, self.STATE_STARTING, "正在启动…")
            self.start(key, program, base_dir, display_name)
            return True

        self._log(key, "{} 将重启：先停止再启动。".format(display_name))
        # 注意顺序与参数：restart_pending 必须在 stop() 之前设好，并且要告诉
        # stop() "别清它"（keep_restart_pending=True）—— 否则 _on_finished 会把
        # 这次停止当成普通停止，重启永远不会发生（真机事故）。
        entry.restart_pending = True
        if not entry.stopping:
            # 已经在停止中的进程直接沿用：此时重启意图已经记在 restart_pending 上
            self.stop(key, timeout_ms=timeout_ms, force=False,
                      keep_restart_pending=True)
        return True

    def _lookup_program(self, entry: RunningProcess) -> Optional[Program]:
        """取最新程序配置：优先外部回调，其次启动时保存的快照。"""
        provider = self._config_provider
        if provider is not None:
            try:
                program = provider(entry.program_id)
            except Exception:  # 回调来自外部，出错不应影响管理器
                program = None
            if isinstance(program, Program):
                return program
        return entry.program_snapshot

    def remember_program(self, key: str, program: Program, base_dir) -> None:
        """记录配置快照，便于在没有外部回调时也能重启。"""
        entry = self._entries.get(key)
        if entry is None:
            return
        entry.program_id = program.id or entry.program_id
        entry.program_snapshot = program.copy()
        self._base_dir_cache[key] = str(base_dir)

    # ------------------------------------------------------------------
    # 批量停止 / 清理
    # ------------------------------------------------------------------

    def stop_all(self, timeout_ms: int = 3000, wait: bool = True,
                 force: bool = False) -> int:
        """停止所有托管进程（用于关闭窗口与程序退出）。

        返回发出停止请求的数量。

        wait
            为真时阻塞等待（含强制结束），只在窗口关闭、程序退出时使用，
            避免残留孤儿进程。
        force
            为真时**跳过优雅停止**，直接 `taskkill /T /F` 并在 1.5 秒后就地兜底。
            用于"关闭管理器时立刻收摊"：有些程序（.NET / 控制台宿主）根本不响应
            优雅关闭请求，等它只会白等，还会让关窗卡住好几秒。
        """
        self._shutting_down = True
        targets = self.running_keys()
        for key in targets:
            try:
                self.stop(key, timeout_ms=timeout_ms, force=force)
            except Exception:  # 保证批量停止不会中途抛出
                pass

        if not wait or not targets:
            return len(targets)

        wait_ms = max(1000, int(timeout_ms))
        for key in targets:
            entry = self._entries.get(key)
            if entry is None or not entry.is_running:
                continue
            try:
                # 阻塞等待子进程自行退出，同时 QProcess 仍会派发输出信号
                if entry.process.waitForFinished(wait_ms):
                    continue
            except (RuntimeError, AttributeError):
                continue

            pid = entry.pid
            if os.name == "nt" and pid:
                self._taskkill_sync(pid)
            try:
                entry.process.kill()
                entry.process.waitForFinished(2000)
            except (RuntimeError, AttributeError):
                pass

        return len(targets)

    def cleanup(self) -> None:
        """释放资源：停止全部进程、清理定时器与记录。"""
        try:
            self.stop_all(timeout_ms=2000, wait=True)
        finally:
            for key in list(self._kill_timers.keys()):
                self._cancel_kill_timer(key)
            for key in list(self._entries.keys()):
                entry = self._entries.pop(key, None)
                if entry is not None:
                    self._dispose(entry)
            self._shutting_down = False

    @staticmethod
    def _taskkill_sync(pid: int) -> bool:
        """同步强制结束整棵进程树（仅用于退出清理，会阻塞片刻）。"""
        import subprocess

        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(
                [TASKKILL_EXE, "/T", "/F", "/PID", str(int(pid))],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                timeout=8,
                check=False,
            )
            return True
        except (OSError, ValueError):
            return False

    def _dispose(self, entry: RunningProcess) -> None:
        """断开信号并销毁 QProcess，避免野信号与句柄泄漏。

        注意：**不要**用无参数的 ``process.disconnect()``（通配断开）。
        真机控制台会因此在退出时刷一句
        ``QObject::disconnect: wildcard call disconnects from destroyed signal of QProcess``
        —— 它要遍历该对象的所有连接，其中一条的发送者可能已经销毁。
        这里只断开我们真正连过的那几个信号（见 `start()`）。
        """
        process = entry.process
        # 四个信号逐个断开（都是 `start()` 里连过的那几条）。
        # 写成四条显式调用而不是循环 + 局部变量：一眼能看出断的是哪几条，
        # 也方便 `tools/check_palette_studio.py` 静态拦住"对象级通配 disconnect"。
        try:
            process.readyReadStandardOutput.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            process.started.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            process.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            process.errorOccurred.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1000)
        except (RuntimeError, AttributeError):
            pass
        try:
            process.deleteLater()
        except (RuntimeError, AttributeError):
            pass
        self._cancel_kill_timer(entry.key)

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------

    def _alive(self) -> bool:
        """本对象的 C++ 部分是否还在（防"信号在析构之后才回调"）。

        真机/自检都出现过这个崩栈：窗口关闭 → `deleteLater()` 生效 → 之后
        `QProcess` 的 error/finished 信号才回调过来，此时 Python 包装对象还在，
        但底层 C++ 对象已被删除，任何信号 emit 都会抛
        ``RuntimeError: wrapped C/C++ object of type ProcessManager has been deleted``。
        这是**异步清理的正常竞态**，不是逻辑错误 —— 用 sip.isdeleted 静默跳过即可。
        """
        try:
            from PyQt6 import sip

            return not sip.isdeleted(self)
        except (ImportError, AttributeError, TypeError, RuntimeError):
            try:
                self.objectName()
                return True
            except RuntimeError:
                return False

    def _emit(self, signal_name: str, *args) -> bool:
        """安全发信号：对象已被销毁（窗口关闭的异步竞态）时静默跳过。

        为什么需要：`QProcess` 的 error/finished 信号可能在本对象 `deleteLater()`
        之后才回调过来，此时 emit 会抛
        ``RuntimeError: wrapped C/C++ object of type ProcessManager has been deleted``。
        这是正常的清理竞态；用 sip.isdeleted + try/except 双重保护即可。
        返回是否真的发出去了。
        """
        if not self._alive():
            return False
        try:
            getattr(self, signal_name).emit(*args)
            return True
        except (AttributeError, RuntimeError):
            return False

    def _log(self, key: str, message: str) -> None:
        """发送管理器自身的日志（前缀 [管理器]）。"""
        text = str(message)
        self._emit("log_message", key or "", text)
        self._emit("output_line", key or "", "[管理器] " + text, "manager")
        self._emit("output_text", key or "", "[管理器] " + text + "\n", "manager")


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def build_manager_key(bot_id: str, program_id: str) -> str:
    """生成跨机器人唯一的 key，避免不同机器人里同名程序互相顶掉。"""
    return "{}:{}".format(bot_id or "bot", program_id or "prog")


def start_bot(
    manager: ProcessManager,
    bot,
    config: Optional[BotConfig] = None,
    interval: float = 0.0,
) -> List[str]:
    """按顺序启动一个机器人下的所有启用程序（主程序优先）。

    - program.delay 通过 QTimer 延迟启动，不会阻塞 UI
    - interval 为机器人之间的启动间隔（秒），由调用方决定是否等待；
      这里只负责把间隔信息写进日志，实际等待由主窗口统一调度
    返回已发起启动请求的 key 列表。
    """
    if config is not None:
        base_dir = config.base_dir
    else:
        from pathlib import Path

        base_dir = Path.cwd()

    bot.sort_programs()
    started: List[str] = []
    for program in bot.programs:
        if not program.enabled:
            continue
        key = build_manager_key(bot.id, program.id)
        display_name = "{} / {}".format(bot.name, program.name)
        delay_ms = int(max(0.0, float(program.delay)) * 1000.0)
        if delay_ms <= 0:
            manager.start(key, program, base_dir, display_name)
        else:
            manager._emit(
                "log_message",
                key, "{} 将在 {:.1f} 秒后启动。".format(display_name, delay_ms / 1000.0),
            )
            QTimer.singleShot(
                delay_ms,
                lambda k=key, p=program, b=base_dir, n=display_name: manager.start(k, p, b, n),
            )
        started.append(key)

    if interval and interval > 0:
        manager._emit(
            "log_message", "", "机器人启动间隔：{:.1f} 秒。".format(float(interval))
        )
    return started


def stop_bot(
    manager: ProcessManager,
    bot,
    timeout_ms: int = DEFAULT_STOP_TIMEOUT_MS,
) -> int:
    """停止一个机器人下的所有进程，返回请求停止的数量。"""
    count = 0
    for program in reversed(list(bot.programs)):
        key = build_manager_key(bot.id, program.id)
        if manager.is_running(key) and manager.stop(key, timeout_ms=timeout_ms):
            count += 1
    return count


def role_label(program: Program) -> str:
    """角色标签（主程序 / 副程序），供 UI 显示。"""
    return "主程序" if program.role == ROLE_PRIMARY else "副程序"


# ---------------------------------------------------------------------------
# 自检：直接运行本文件时验证解码器与 jar 扫描逻辑（不启动真实进程）
# ---------------------------------------------------------------------------

def _selftest() -> int:
    print("Python:", sys.version.split()[0], "| 平台:", os.name)

    # 1) UTF-8 正常解码
    decoder = OutputDecoder()
    payload = "你好，世界 hello\n第二行\n".encode("utf-8")
    text = decoder.feed(payload.decode("latin-1"))
    print("[1] UTF-8 判定 =", decoder.encoding, "| 内容 =", repr(text))
    assert decoder.encoding == "utf-8"
    assert "你好，世界" in text

    # 2) GBK 回退（0xD6 0xD0 在 UTF-8 中非法）
    decoder2 = OutputDecoder()
    gbk_payload = "中文日志测试".encode("gbk")
    text2 = decoder2.feed(gbk_payload.decode("latin-1"))
    print("[2] GBK 判定 =", decoder2.encoding, "| 内容 =", repr(text2))
    assert decoder2.encoding == "gbk"
    assert "中文日志测试" in text2

    # 3) 跨块截断的 UTF-8 多字节序列
    decoder3 = OutputDecoder()
    whole = "机器人已启动".encode("utf-8")
    part1 = decoder3.feed(whole[:4].decode("latin-1"))
    part2 = decoder3.feed(whole[4:].decode("latin-1"))
    print("[3] 分块解码 =", repr(part1), "+", repr(part2))
    assert part1 + part2 == "机器人已启动"

    # 4) 行切分（\r\n 与单独的 \r 都算换行）
    lines, rest = split_lines("", "a\r\nb\rc\nd")
    print("[4] 行切分 =", lines, "| 残留 =", repr(rest))
    assert lines == ["a", "b", "c"] and rest == "d"

    # 5) jar 扫描
    here = os.path.dirname(os.path.abspath(__file__))
    latest = ProcessManager.find_latest_jar(os.path.dirname(here))
    print("[5] 项目目录最新 jar =", latest)

    # 6) 命令包装与格式化
    print("[6] 格式化 =", ProcessManager._format_command(
        ["java", "-jar", r"C:\Program Files\x\app.jar"]))
    assert ProcessManager._wrap_for_shell(["run.bat"])[1] == "/c"

    print("自检通过：解码优先级 UTF-8 -> GBK、分块拼接、行切分、jar 扫描均正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
