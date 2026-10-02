# -*- coding: utf-8 -*-
"""配置模块：读写 bots_config.json。

本模块只负责"配置数据"本身，不涉及任何 UI 与进程管理逻辑，
因此可以独立被 main.py、process_manager.py、ui/* 复用。

设计要点
--------
1. 数据结构：一个 Bot（机器人）可以包含多个 Program（程序）。
   Bot  ->  programs: List[Program]
2. 每个 Program 的字段固定为：
   id、name、role(primary/secondary)、cwd、command、env、delay、auto_latest_jar
   （另外额外支持可选的 args 列表字段，便于把参数与可执行文件分开书写）
3. 路径解析规则：cwd 与 command 中的相对路径，均相对于 bots_config.json
   所在目录解析，因此整个项目目录可以随意移动。
4. 环境变量展开：所有路径与命令字符串都会展开 %VAR%（Windows 风格）
   与 $VAR / ${VAR}（POSIX 风格），并展开用户目录 ~。
5. 容错：JSON 文件缺失、损坏、字段类型不对时，都会自动降级为默认值，
   并可通过 BotConfig.load_warnings 查看降级原因。
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 程序角色
ROLE_PRIMARY = "primary"
ROLE_SECONDARY = "secondary"
VALID_ROLES = (ROLE_PRIMARY, ROLE_SECONDARY)

#: 配置文件名
CONFIG_FILENAME = "bots_config.json"

#: 当前配置格式版本
CONFIG_VERSION = 1


def bundle_root() -> Path:
    """返回「可写的工作目录」。

    - 源码运行：项目根目录（<root>/app/config.py -> <root>）
    - PyInstaller 打包：EXE 所在目录。打包后 __file__ 位于临时解包目录
      （_MEIPASS），不能用来存放配置，否则程序退出后配置会随临时目录一起消失。
    """
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except (OSError, ValueError, AttributeError):
            return Path.cwd()
    return Path(__file__).resolve().parent.parent


#: 项目根目录 / EXE 所在目录
PROJECT_ROOT = bundle_root()

#: 默认配置文件路径
DEFAULT_CONFIG_PATH = PROJECT_ROOT / CONFIG_FILENAME

#: 默认命令解释器（Windows 优先 cmd.exe）
DEFAULT_SHELL = "cmd.exe" if os.name == "nt" else "/bin/sh"

#: 命令中代表"最新 jar 包"的占位符，配合 auto_latest_jar 使用
JAR_PLACEHOLDER = "[LATEST_JAR]"

#: 兼容其它写法（config 与 process_manager 都会替换）
LEGACY_JAR_PLACEHOLDERS = ("{jar}", "%LATEST_JAR%")

#: 命令行拆分用的正则（仅做简单拆分，完整引号规则交给 shell 处理）
_ARG_SPLIT_RE = re.compile(r"\s+")

#: Windows 环境变量：%VAR%
_WIN_ENV_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")
#: POSIX 环境变量：$VAR 或 ${VAR}
_POSIX_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


# ---------------------------------------------------------------------------
# 基础工具函数
# ---------------------------------------------------------------------------

def new_id(prefix: str = "prog") -> str:
    """生成一个稳定可读的唯一 id。"""
    return "{}_{}".format(prefix, uuid.uuid4().hex[:8])


def as_text(value: Any, default: str = "") -> str:
    """把任意 JSON 值安全地转换成字符串。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return default


def as_bool(value: Any, default: bool = False) -> bool:
    """把任意 JSON 值安全地转换成布尔值。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "y", "on", "是", "真"):
            return True
        if text in ("0", "false", "no", "n", "off", "否", "假"):
            return False
    return default


def as_float(value: Any, default: float = 0.0) -> float:
    """把任意 JSON 值安全地转换成浮点数。"""
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def as_text_list(value: Any) -> List[str]:
    """把任意 JSON 值安全地转换成字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        result: List[str] = []
        for item in value:
            text = as_text(item, "")
            if text:
                result.append(text)
        return result
    return []


def as_str_dict(value: Any) -> Dict[str, str]:
    """把任意 JSON 对象安全地转换成 Dict[str, str]。"""
    if not isinstance(value, dict):
        return {}
    result: Dict[str, str] = {}
    for key, item in value.items():
        key_text = as_text(key, "")
        if not key_text:
            continue
        result[key_text] = as_text(item, "")
    return result


def normalize_role(value: Any, default: str = ROLE_SECONDARY) -> str:
    """把角色字段规范化为 primary / secondary。"""
    text = as_text(value, "").strip().lower()
    aliases = {
        "primary": ROLE_PRIMARY,
        "main": ROLE_PRIMARY,
        "master": ROLE_PRIMARY,
        "front": ROLE_PRIMARY,
        "主": ROLE_PRIMARY,
        "主要": ROLE_PRIMARY,
        "主程序": ROLE_PRIMARY,
        "secondary": ROLE_SECONDARY,
        "second": ROLE_SECONDARY,
        "sub": ROLE_SECONDARY,
        "slave": ROLE_SECONDARY,
        "back": ROLE_SECONDARY,
        "副": ROLE_SECONDARY,
        "次要": ROLE_SECONDARY,
        "副程序": ROLE_SECONDARY,
    }
    if text in aliases:
        return aliases[text]
    return default if default in VALID_ROLES else ROLE_SECONDARY


def role_display(role: str) -> str:
    """角色的中文显示名。"""
    return "主程序" if role == ROLE_PRIMARY else "副程序"


def expand_env(text: str, env: Optional[Dict[str, str]] = None) -> str:
    """展开 %VAR%、$VAR、${VAR} 与 ~，未知变量原样保留。"""
    if not text:
        return ""

    def _lookup(name: str) -> Optional[str]:
        if env is not None and name in env:
            return env[name]
        if name in os.environ:
            return os.environ[name]
        # Windows 环境变量大小写不敏感
        if os.name == "nt":
            lowered = name.lower()
            for key, value in os.environ.items():
                if key.lower() == lowered:
                    return value
            if env is not None:
                for key, value in env.items():
                    if key.lower() == lowered:
                        return value
        return None

    def _replace_win(match: "re.Match[str]") -> str:
        found = _lookup(match.group(1))
        return found if found is not None else match.group(0)

    def _replace_posix(match: "re.Match[str]") -> str:
        name = match.group(1) or match.group(2) or ""
        found = _lookup(name)
        return found if found is not None else match.group(0)

    result = _WIN_ENV_RE.sub(_replace_win, text)
    result = _POSIX_ENV_RE.sub(_replace_posix, result)

    if result.startswith("~"):
        home = os.path.expanduser("~")
        if home and home != "~":
            result = home + result[1:]
    return result


def resolve_path(text: str, base_dir: Path, env: Optional[Dict[str, str]] = None) -> str:
    """把配置里的路径解析为绝对路径字符串。

    - 展开环境变量与 ~
    - 已经是绝对路径时原样规范化返回
    - 相对路径相对于 base_dir（通常是 bots_config.json 所在目录）
    - 纯命令名（如 cmd.exe、python）保持原样，避免被错误拼成绝对路径
    """
    expanded = expand_env(text, env).strip().strip('"')
    if not expanded:
        return ""
    path = Path(expanded)
    try:
        if path.is_absolute():
            return str(path)
    except (OSError, ValueError):
        return expanded
    if os.sep in expanded or "/" in expanded or expanded.startswith("."):
        try:
            return str((Path(base_dir) / path).resolve())
        except (OSError, ValueError):
            return str(Path(base_dir) / path)
    return expanded


def split_command_line(command: str) -> List[str]:
    """把命令行字符串拆成参数列表（尊重双引号，保留引号字符）。

    规则：
    - 引号外的空白是分隔符；引号内的空格属于同一个参数
    - 引号字符本身保留在参数里，交给 cmd.exe 或程序自己解释
      （cmd /c "a && b" 这种写法需要把引号一起传给 cmd）

    示例：
        'cmd.exe /c "call .venv\\Scripts\\activate.bat && nb run"'
            -> ['cmd.exe', '/c', '"call .venv\\Scripts\\activate.bat && nb run"']
        'java -jar "C:\\Program Files\\x\\app.jar"'
            -> ['java', '-jar', '"C:\\Program Files\\x\\app.jar"']
    """
    text = (command or "").strip()
    if not text:
        return []
    if '"' not in text:
        return [part for part in _ARG_SPLIT_RE.split(text) if part]

    tokens: List[str] = []
    buffer: List[str] = []
    in_quotes = False

    for char in text:
        if char == '"':
            in_quotes = not in_quotes
            buffer.append(char)
            continue
        if char.isspace() and not in_quotes:
            if buffer:
                tokens.append("".join(buffer))
                buffer = []
            continue
        buffer.append(char)

    if buffer:
        tokens.append("".join(buffer))

    tokens = [token for token in tokens if token]
    if not tokens:
        return [part for part in _ARG_SPLIT_RE.split(text) if part]
    return tokens


def strip_arg_quotes(argv: List[str]) -> List[str]:
    """去掉每个参数**最外层**那一对双引号（只用于"直接交给 QProcess"的路径）。

    为什么必须去掉（真机事故 2026-10-02）
    ------------------------------------
    `split_command_line` 是**故意**保留引号的（`cmd /c "a && b"` 必须把引号一起交给
    cmd）。但 via_shell=False 时，参数是直接交给 QProcess → CreateProcess 的 ——
    引号会**原样进到子进程的 argv 里**。真机表现（一条命令解释全部现象）：

        python -c "import time; print('probe up'); time.sleep(120)"

    子进程收到的是 `"import time; print('probe up'); time.sleep(120)"`（**带引号**），
    Python 会把它当成一个**字符串字面量**：语法合法、什么都不做、退出码 0、零输出 ——
    看起来就像"进程自己立刻正常退出了"。同理，含空格的路径
    （`java -jar "C:\\Program Files\\x.jar"`）会变成一个"带引号的文件名"。

    规则：只剥"首尾都是双引号"的那一对；`""` → 空串；
    内部引号按 Windows 规则（`""` 表示一个 `"`）还原。
    """
    cleaned: List[str] = []
    for raw in argv or []:
        text = "" if raw is None else str(raw)
        if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if '""' in text:
            text = text.replace('""', '"')
        cleaned.append(text)
    return cleaned


def format_argv(argv: List[str]) -> List[str]:
    """把 argv 拼成便于阅读、也便于复制的命令行片段。

    - 含空格或制表符的参数加双引号
    - 内部双引号按 Windows/cmd 的规则用两个双引号表示（绝不用 \\" ，
      因为 cmd 不认识反斜杠转义，会把 \\" 当成普通字符导致
      "'xxx' is not recognized as an internal or external command"）
    """
    parts: List[str] = []
    for token in argv:
        text = "" if token is None else str(token)
        if text == "":
            parts.append('""')
            continue
        if any(char in text for char in ' \t'):
            if text.startswith('"') and text.endswith('"') and len(text) >= 2:
                # 已经带引号的参数（例如 cmd /c "a && b"）保持原样
                parts.append(text)
            else:
                parts.append('"{}"'.format(text.replace('"', '""')))
        else:
            parts.append(text)
    return parts


def format_command_line(argv: List[str]) -> str:
    """把 argv 拼成一行字符串。"""
    return " ".join(format_argv(argv))


# ---------------------------------------------------------------------------
# 数据类：Program
# ---------------------------------------------------------------------------

@dataclass
class Program:
    """一个被启动的程序（例如主机器人 jar、副机器人 jar、代理脚本等）。"""

    id: str = field(default_factory=lambda: new_id("prog"))
    name: str = "新程序"
    role: str = ROLE_SECONDARY
    cwd: str = ""
    command: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    auto_latest_jar: bool = False
    # 扩展字段（不影响上述 8 个必备字段）
    args: List[str] = field(default_factory=list)
    enabled: bool = True
    description: str = ""
    #: True 时整条 command 交给命令解释器执行（支持 && 、| 、> 等 cmd 语法，
    #: 例如 chcp 65001 && yarn start）；False 时按 argv 直接启动可执行文件。
    via_shell: bool = False

    # ---- 序列化 ----

    def to_dict(self) -> Dict[str, Any]:
        """转换成可写入 JSON 的字典（字段顺序稳定，便于人工阅读）。"""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "cwd": self.cwd,
            "command": self.command,
            "env": dict(self.env),
            "delay": self.delay,
            "auto_latest_jar": bool(self.auto_latest_jar),
            "args": list(self.args),
            "enabled": bool(self.enabled),
            "description": self.description,
            "via_shell": bool(self.via_shell),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Program":
        """从 JSON 字典构建，字段缺失或类型异常时使用安全默认值。"""
        if not isinstance(data, dict):
            data = {}
        program_id = as_text(data.get("id"), "").strip() or new_id("prog")
        name = as_text(data.get("name"), "").strip() or "未命名程序"
        delay = as_float(data.get("delay"), 0.0)
        if delay < 0:
            delay = 0.0
        return cls(
            id=program_id,
            name=name,
            role=normalize_role(data.get("role"), ROLE_SECONDARY),
            cwd=as_text(data.get("cwd"), ""),
            command=as_text(data.get("command"), ""),
            env=as_str_dict(data.get("env")),
            delay=delay,
            auto_latest_jar=as_bool(data.get("auto_latest_jar"), False),
            args=as_text_list(data.get("args")),
            enabled=as_bool(data.get("enabled"), True),
            description=as_text(data.get("description"), ""),
            via_shell=as_bool(data.get("via_shell"), False),
        )

    def copy(self) -> "Program":
        """深拷贝，供编辑对话框做"取消后不生效"的编辑。"""
        return Program.from_dict(copy.deepcopy(self.to_dict()))

    # ---- 便捷属性 ----

    @property
    def is_primary(self) -> bool:
        return self.role == ROLE_PRIMARY

    @property
    def role_text(self) -> str:
        return role_display(self.role)

    @property
    def display_name(self) -> str:
        """用于界面展示的名字，例如 "主程序 · Mirai" 。"""
        return "{} · {}".format(self.role_text, self.name or "未命名程序")

    @property
    def summary(self) -> str:
        """一行摘要，用于列表/提示。"""
        parts = [self.role_text, self.name or "未命名程序"]
        if not self.enabled:
            parts.append("已禁用")
        return " | ".join(parts)

    # ---- 路径与命令 ----

    def working_dir(self, base_dir: Path, env: Optional[Dict[str, str]] = None) -> str:
        """解析后的工作目录；未配置时回退到 base_dir。

        注意：base_dir 必须是"相对路径的基准目录"（即 BotConfig.base_dir，
        也就是 bots_config.json 所在目录）。不要把已经解析过的机器人目录再传进来，
        否则 cwd 中的相对路径会被拼接两次。
        """
        resolved = resolve_path(self.cwd, base_dir, env)
        return resolved or str(base_dir)

    def working_dir_path(self, base_dir: Path, env: Optional[Dict[str, str]] = None) -> Path:
        """解析后的工作目录 Path 对象。"""
        return Path(self.working_dir(base_dir, env))

    def merged_env(self, base_dir: Path) -> Dict[str, str]:
        """合并后的环境变量：进程环境 + 程序自定义 env（已展开变量）。"""
        merged: Dict[str, str] = {str(k): str(v) for k, v in os.environ.items()}
        for key, value in self.env.items():
            merged[str(key)] = expand_env(str(value))
        return merged

    def resolved_command(self, base_dir: Path) -> str:
        """展开环境变量与相对路径后的命令字符串。"""
        merged = self.merged_env(base_dir)
        return expand_env(self.command, merged).strip()

    def shell_argv(self, base_dir: Path) -> List[str]:
        """把整条命令交给命令解释器执行，支持 && / | / > 等 cmd 语法。

        Windows：  ["cmd.exe", "/c", "<command>"]
        POSIX  ：  ["/bin/sh", "-c", "<command>"]
        """
        command = self.resolved_command(base_dir)
        if not command:
            return []
        if os.name == "nt":
            shell = os.environ.get("COMSPEC") or DEFAULT_SHELL
            return [shell, "/c", command]
        return ["/bin/sh", "-c", command]

    def command_argv(self, base_dir: Path) -> List[str]:
        """最终要传给 QProcess 的参数列表。

        规则：
        - command 为空 -> 返回空列表（调用方应视为配置错误）
        - via_shell 为真 -> 整条命令交给 cmd.exe /c（或 sh -c）执行，
          此时不再拆分引号，&& 、| 、> 等语法原样生效
        - auto_latest_jar 为真且命令含 [LATEST_JAR]/{jar} -> 用工作目录下最新的 jar 替换
        - command 自身可能是简单命令名（python、cmd.exe），也可能是完整路径
        - 额外 args 会追加在 command 拆分结果之后
        """
        command = self.resolved_command(base_dir)
        if not command:
            return []

        if self.via_shell:
            argv = self.shell_argv(base_dir)
            argv = self._apply_latest_jar(argv, base_dir)
            for extra in self.args:
                argv.append(expand_env(str(extra), self.env))
            return argv

        argv = split_command_line(command)
        argv = self._apply_latest_jar(argv, base_dir)

        if self.args:
            for extra in self.args:
                argv.extend(split_command_line(expand_env(extra, self.env)))

        # 非 shell 路径最后一步：把"用于分组的引号"去掉再交给 QProcess，
        # 否则引号会原样进到子进程的 argv（真机事故：`python -c "…"` 的代码
        # 被当成字符串字面量，进程立刻退出码 0、零输出）。详见 strip_arg_quotes。
        return strip_arg_quotes(argv)

    def display_command(self, base_dir: Path) -> str:
        """给人看的启动命令（用于日志与对话框预览）。"""
        if self.via_shell:
            command = self.resolved_command(base_dir)
            if not command:
                return ""
            if os.name == "nt":
                return "{} /c {}".format(os.environ.get("COMSPEC") or DEFAULT_SHELL, command)
            return "/bin/sh -c {}".format(command)
        return format_command_line(self.command_argv(base_dir))

    def _apply_latest_jar(self, argv: List[str], base_dir: Path) -> List[str]:
        """把 [LATEST_JAR] / {jar} 替换为工作目录下最新的 jar。"""
        if not argv:
            return argv

        need_jar = bool(self.auto_latest_jar) or any(
            any(placeholder in token for placeholder in (JAR_PLACEHOLDER,) + LEGACY_JAR_PLACEHOLDERS)
            for token in argv
        )
        if not need_jar:
            return argv

        jar_path = find_latest_jar(self.working_dir_path(base_dir))
        if jar_path is None:
            return argv

        jar_text = str(jar_path)
        result: List[str] = []
        replaced = False
        for token in argv:
            new_token = token
            for placeholder in (JAR_PLACEHOLDER,) + LEGACY_JAR_PLACEHOLDERS:
                if placeholder in new_token:
                    new_token = new_token.replace(placeholder, jar_text)
                    replaced = True
            result.append(new_token)
        if not replaced and self.auto_latest_jar:
            # 命令中没写占位符，但明确要求自动选 jar：追加到末尾
            result.append(jar_text)
        return result

    def validate(self, base_dir: Path) -> List[str]:
        """返回配置问题列表，空列表表示配置可用。

        说明：auto_latest_jar 为真但命令中不含 {jar} 时不算错误，
        process_manager 会把最新的 jar 自动追加到命令末尾。
        """
        problems: List[str] = []
        if not self.name.strip():
            problems.append("程序名称为空")
        if not self.command.strip():
            problems.append("启动命令为空")
        if self.delay < 0:
            problems.append("启动延迟不能为负数")
        cwd = self.working_dir_path(base_dir)
        if self.cwd.strip() and not cwd.exists():
            problems.append("工作目录不存在：{}".format(cwd))
        return problems


def find_latest_jar(directory) -> Optional[Path]:
    """在目录中查找修改时间最新的 .jar 文件（递归一层子目录）。

    directory 允许传 str 或 Path（调用方常拿到 str 形式的目录）。
    找不到、目录不存在或没有权限时返回 None，不抛异常。
    """
    try:
        if isinstance(directory, Path):
            target = directory
        elif isinstance(directory, (str, bytes, os.PathLike)):
            text = directory.decode() if isinstance(directory, bytes) else str(directory)
            if not text.strip():
                return None
            target = Path(text)
        else:
            return None
    except (TypeError, ValueError, OSError):
        return None

    try:
        if not target.is_dir():
            return None
        candidates: List[Path] = []
        for entry in target.iterdir():
            try:
                if entry.is_file() and entry.suffix.lower() == ".jar":
                    candidates.append(entry)
                elif entry.is_dir():
                    for inner in entry.iterdir():
                        if inner.is_file() and inner.suffix.lower() == ".jar":
                            candidates.append(inner)
            except (OSError, PermissionError):
                continue
        if not candidates:
            return None

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        candidates.sort(key=_mtime, reverse=True)
        return candidates[0]
    except (OSError, PermissionError):
        return None


# ---------------------------------------------------------------------------
# 数据类：Bot
# ---------------------------------------------------------------------------

@dataclass
class Bot:
    """一个机器人，内部包含一个或多个程序。"""

    id: str = field(default_factory=lambda: new_id("bot"))
    name: str = "新机器人"
    qq: str = ""
    working_dir: str = ""
    enabled: bool = True
    auto_start: bool = False
    description: str = ""
    programs: List[Program] = field(default_factory=list)

    # ---- 序列化 ----

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "qq": self.qq,
            "working_dir": self.working_dir,
            "enabled": bool(self.enabled),
            "auto_start": bool(self.auto_start),
            "description": self.description,
            "programs": [program.to_dict() for program in self.programs],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Bot":
        if not isinstance(data, dict):
            data = {}
        bot_id = as_text(data.get("id"), "").strip() or new_id("bot")
        name = as_text(data.get("name"), "").strip() or "未命名机器人"
        raw_programs = data.get("programs")
        programs: List[Program] = []
        if isinstance(raw_programs, list):
            for item in raw_programs:
                programs.append(Program.from_dict(item))
        elif isinstance(raw_programs, dict):
            # 兼容写法：programs 写成 {"主程序": {...}} 的映射
            for key, item in raw_programs.items():
                if isinstance(item, dict):
                    payload = dict(item)
                    payload.setdefault("name", as_text(key, ""))
                    programs.append(Program.from_dict(payload))
        return cls(
            id=bot_id,
            name=name,
            qq=as_text(data.get("qq"), ""),
            working_dir=as_text(data.get("working_dir"), ""),
            enabled=as_bool(data.get("enabled"), True),
            auto_start=as_bool(data.get("auto_start"), False),
            description=as_text(data.get("description"), ""),
            programs=programs,
        )

    def copy(self) -> "Bot":
        return Bot.from_dict(copy.deepcopy(self.to_dict()))

    # ---- 便捷属性 ----

    @property
    def program_count(self) -> int:
        return len(self.programs)

    @property
    def enabled_programs(self) -> List[Program]:
        return [program for program in self.programs if program.enabled]

    @property
    def primary_program(self) -> Optional[Program]:
        for program in self.programs:
            if program.role == ROLE_PRIMARY:
                return program
        return None

    @property
    def summary(self) -> str:
        primary = self.primary_program
        role_text = role_display(primary.role) if primary else "无主程序"
        suffix = "" if self.enabled else "（已禁用）"
        return "{} 个程序 · {}{}".format(self.program_count, role_text, suffix)

    # ---- 操作 ----

    def get_program(self, program_id: str) -> Optional[Program]:
        for program in self.programs:
            if program.id == program_id:
                return program
        return None

    def add_program(self, program: Optional[Program] = None) -> Program:
        item = program if program is not None else Program()
        if any(existing.id == item.id for existing in self.programs):
            item.id = new_id("prog")
        self.programs.append(item)
        return item

    def remove_program(self, program_id: str) -> bool:
        for index, program in enumerate(self.programs):
            if program.id == program_id:
                del self.programs[index]
                return True
        return False

    def sort_programs(self) -> None:
        """主程序排前面，其次按原有顺序，保证启动顺序可预期。"""
        self.programs.sort(key=lambda item: 0 if item.role == ROLE_PRIMARY else 1)

    def base_dir(self, fallback: Optional[Path] = None) -> Path:
        """机器人级基准目录：优先 working_dir，其次 fallback。"""
        anchor = fallback if fallback is not None else PROJECT_ROOT
        resolved = resolve_path(self.working_dir, anchor)
        if resolved:
            return Path(resolved)
        return Path(anchor)

    def validate(self, base_dir: Path) -> List[str]:
        """校验机器人及其所有程序；base_dir 为配置文件所在目录（相对路径基准）。

        这里刻意不把 cwd 二次解析到机器人 working_dir，避免相对路径被拼接两次。
        """
        problems: List[str] = []
        if not self.name.strip():
            problems.append("机器人名称为空")
        if not self.programs:
            problems.append("没有任何程序，请至少添加一个程序")
        seen_ids: set = set()
        primary_count = 0
        for program in self.programs:
            if program.id in seen_ids:
                problems.append("程序 id 重复：{}".format(program.id))
            seen_ids.add(program.id)
            if program.role == ROLE_PRIMARY:
                primary_count += 1
            for item in program.validate(Path(base_dir)):
                problems.append("[{}] {}".format(program.name or program.id, item))
        if primary_count > 1:
            problems.append("存在 {} 个主程序，建议只保留一个".format(primary_count))
        return problems


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

def default_program_primary() -> Program:
    """默认示例：主程序（例如 Mirai / OneBot 主体）。"""
    return Program(
        id=new_id("prog"),
        name="主程序",
        role=ROLE_PRIMARY,
        cwd="bots/example",
        command="java -jar {jar}",
        env={"JAVA_TOOL_OPTIONS": "-Dfile.encoding=UTF-8"},
        delay=0.0,
        auto_latest_jar=True,
        args=[],
        enabled=True,
        description="示例主程序：自动使用工作目录中修改时间最新的 jar 包启动。",
    )


def default_program_secondary() -> Program:
    """默认示例：副程序（例如签名服务 / 外部插件 / 代理）。"""
    return Program(
        id=new_id("prog"),
        name="副程序",
        role=ROLE_SECONDARY,
        cwd="bots/example",
        command="python plugin_host.py --port 8080",
        env={"PYTHONIOENCODING": "utf-8"},
        delay=5.0,
        auto_latest_jar=False,
        args=[],
        enabled=True,
        description="示例副程序：在主程序启动 5 秒后启动。",
    )


def default_bot() -> Bot:
    """默认示例机器人：包含一个主程序与一个副程序。"""
    bot = Bot(
        id=new_id("bot"),
        name="示例机器人",
        qq="123456789",
        working_dir="bots/example",
        enabled=True,
        auto_start=False,
        description="默认示例配置，可直接在界面上修改或删除。",
        programs=[default_program_primary(), default_program_secondary()],
    )
    bot.sort_programs()
    return bot


def default_config_dict() -> Dict[str, Any]:
    """默认配置字典（也是首次运行写入 bots_config.json 的内容）。"""
    bot = default_bot()
    return {
        "version": CONFIG_VERSION,
        "shell": DEFAULT_SHELL,
        "start_interval": 1.0,
        "stop_timeout": 10.0,
        "log_max_lines": 2000,
        "bots": [bot.to_dict()],
    }


# ---------------------------------------------------------------------------
# 配置容器：BotConfig
# ---------------------------------------------------------------------------

class BotConfig:
    """bots_config.json 的内存模型与读写入口。

    典型用法：
        config = BotConfig.load()          # 缺失时自动生成默认文件
        for bot in config.bots: ...
        config.add_bot(bot); config.save()
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        version: int = CONFIG_VERSION,
        shell: str = DEFAULT_SHELL,
        bots: Optional[Iterable[Bot]] = None,
        start_interval: float = 1.0,
        stop_timeout: float = 10.0,
        log_max_lines: int = 2000,
    ) -> None:
        self.path: Path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        self.version: int = int(version)
        self.shell: str = shell or DEFAULT_SHELL
        self.start_interval: float = max(0.0, float(start_interval))
        self.stop_timeout: float = max(1.0, float(stop_timeout))
        self.log_max_lines: int = max(100, int(log_max_lines))
        self.bots: List[Bot] = list(bots) if bots is not None else []
        #: load() 过程中记录的非致命问题（文件缺失、字段降级等）
        self.load_warnings: List[str] = []
        #: 上次保存时的错误信息
        self.last_error: str = ""

    # ---- 目录 ----

    @property
    def base_dir(self) -> Path:
        """相对路径解析基准：配置文件所在目录。"""
        try:
            return self.path.parent
        except (OSError, ValueError):
            return PROJECT_ROOT

    # ---- 序列化 ----

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "shell": self.shell,
            "start_interval": self.start_interval,
            "stop_timeout": self.stop_timeout,
            "log_max_lines": self.log_max_lines,
            "bots": [bot.to_dict() for bot in self.bots],
        }

    @classmethod
    def from_dict(cls, data: Any, path: Optional[Path] = None) -> "BotConfig":
        if not isinstance(data, dict):
            data = {}
        raw_bots = data.get("bots")
        bots: List[Bot] = []
        if isinstance(raw_bots, list):
            for item in raw_bots:
                bots.append(Bot.from_dict(item))
        return cls(
            path=path,
            version=int(as_float(data.get("version"), CONFIG_VERSION)),
            shell=as_text(data.get("shell"), DEFAULT_SHELL) or DEFAULT_SHELL,
            start_interval=as_float(data.get("start_interval"), 1.0),
            stop_timeout=as_float(data.get("stop_timeout"), 10.0),
            log_max_lines=int(as_float(data.get("log_max_lines"), 2000)),
            bots=bots,
        )

    def copy(self) -> "BotConfig":
        return BotConfig.from_dict(copy.deepcopy(self.to_dict()), path=self.path)

    # ---- 读写 ----

    @classmethod
    def load(cls, path: Optional[Path] = None, create_if_missing: bool = True) -> "BotConfig":
        """读取配置文件。

        - 文件不存在：使用默认配置，并在 create_if_missing 为真时写盘
        - 文件损坏：备份为 *.broken-<时间戳>，改用默认配置（不覆盖原文件内容以外的数据）
        """
        config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        config = cls(path=config_path)

        if not config_path.exists():
            defaults = cls.from_dict(default_config_dict(), path=config_path)
            defaults.load_warnings.append("配置文件不存在，已生成默认配置：{}".format(config_path))
            if create_if_missing:
                ok, message = defaults.save()
                if not ok:
                    defaults.load_warnings.append(message)
            return defaults

        try:
            raw_text = config_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            fallback = cls.from_dict(default_config_dict(), path=config_path)
            fallback.load_warnings.append("读取配置失败（{}），已使用默认配置。".format(exc))
            return fallback

        try:
            data = json.loads(raw_text) if raw_text.strip() else {}
        except json.JSONDecodeError as exc:
            backup = config_path.with_name(
                "{}.broken-{}".format(config_path.name, _timestamp())
            )
            try:
                config_path.replace(backup)
                note = "配置 JSON 解析失败（{}），原文件已备份为 {}，并使用默认配置。".format(exc, backup.name)
            except OSError:
                note = "配置 JSON 解析失败（{}），已使用默认配置。".format(exc)
            fallback = cls.from_dict(default_config_dict(), path=config_path)
            fallback.load_warnings.append(note)
            fallback.save()
            return fallback

        config = cls.from_dict(data, path=config_path)
        if not config.bots:
            config.load_warnings.append("配置中没有机器人，已补充一个示例机器人。")
            config.bots.append(default_bot())
        return config

    def save(self, path: Optional[Path] = None) -> tuple:
        """原子方式写盘：先写临时文件，再替换目标文件。

        返回 (是否成功, 错误信息)。
        """
        target = Path(path) if path is not None else self.path
        self.path = target
        self.last_error = ""

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
            payload += "\n"
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                delete=False,
                dir=str(target.parent),
                prefix=target.name + ".",
                suffix=".tmp",
            )
            try:
                with handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, str(target))
            except BaseException:
                try:
                    if os.path.exists(handle.name):
                        os.unlink(handle.name)
                except OSError:
                    pass
                raise
            return True, ""
        except OSError as exc:
            self.last_error = "保存配置失败：{}".format(exc)
            return False, self.last_error
        except (TypeError, ValueError) as exc:
            self.last_error = "配置内容无法序列化：{}".format(exc)
            return False, self.last_error

    # ---- Bot 操作 ----

    def get_bot(self, bot_id: str) -> Optional[Bot]:
        for bot in self.bots:
            if bot.id == bot_id:
                return bot
        return None

    def get_bot_by_name(self, name: str) -> Optional[Bot]:
        for bot in self.bots:
            if bot.name == name:
                return bot
        return None

    def add_bot(self, bot: Optional[Bot] = None) -> Bot:
        item = bot if bot is not None else default_bot()
        existing_names = {existing.name for existing in self.bots}
        if item.name in existing_names:
            base_name = item.name
            index = 2
            while "{} ({})".format(base_name, index) in existing_names:
                index += 1
            item.name = "{} ({})".format(base_name, index)
        if any(existing.id == item.id for existing in self.bots):
            item.id = new_id("bot")
        self.bots.append(item)
        return item

    def remove_bot(self, bot_id: str) -> bool:
        for index, bot in enumerate(self.bots):
            if bot.id == bot_id:
                del self.bots[index]
                return True
        return False

    def index_of(self, bot_id: str) -> int:
        for index, bot in enumerate(self.bots):
            if bot.id == bot_id:
                return index
        return -1

    def move_bot(self, bot_id: str, offset: int) -> bool:
        """按 offset 调整机器人顺序（offset 为 -1 上移、+1 下移）。"""
        index = self.index_of(bot_id)
        if index < 0:
            return False
        new_index = index + offset
        if new_index < 0 or new_index >= len(self.bots):
            return False
        bot = self.bots.pop(index)
        self.bots.insert(new_index, bot)
        return True

    @property
    def auto_start_bots(self) -> List[Bot]:
        return [bot for bot in self.bots if bot.enabled and bot.auto_start]

    @property
    def total_programs(self) -> int:
        return sum(len(bot.programs) for bot in self.bots)

    # ---- 校验 ----

    def validate(self) -> List[str]:
        """整体校验，返回问题列表（用于启动前提示或"检查配置"菜单）。"""
        problems: List[str] = []
        if not self.bots:
            problems.append("配置中没有任何机器人")
        seen_ids: set = set()
        for bot in self.bots:
            if bot.id in seen_ids:
                problems.append("机器人 id 重复：{}".format(bot.id))
            seen_ids.add(bot.id)
            for item in bot.validate(self.base_dir):
                problems.append("[{}] {}".format(bot.name or bot.id, item))
        if self.start_interval < 0:
            problems.append("启动间隔不能为负数")
        return problems

    def describe(self) -> str:
        """一句话描述当前配置，便于日志与状态栏显示。"""
        return "共 {} 个机器人 / {} 个程序（{}）".format(
            len(self.bots), self.total_programs, self.path
        )


def _timestamp() -> str:
    """生成用于备份文件名的时间戳。"""
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# 模块级便捷函数
# ---------------------------------------------------------------------------

def load_config(path: Optional[Path] = None) -> BotConfig:
    """读取配置的快捷函数。"""
    return BotConfig.load(path)


def save_config(config: BotConfig, path: Optional[Path] = None) -> bool:
    """保存配置的快捷函数，返回是否成功。"""
    ok, _message = config.save(path)
    return ok


def ensure_config_file(path: Optional[Path] = None) -> Path:
    """确保配置文件存在（不存在则写入默认示例），返回其路径。"""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        BotConfig.load(target, create_if_missing=True)
    return target


# ---------------------------------------------------------------------------
# 直接运行时：自检 / 生成默认配置
# ---------------------------------------------------------------------------

def _selftest() -> int:
    """python -m app.config 时执行的自检，便于确认环境与编码正常。"""
    config = BotConfig.from_dict(default_config_dict(), path=DEFAULT_CONFIG_PATH)
    print("配置基准目录：", config.base_dir)
    print(config.describe())
    for bot in config.bots:
        print("-", bot.name, "|", bot.summary)
        for program in bot.programs:
            print("    *", program.display_name, "| delay=%.1fs" % program.delay,
                  "| auto_latest_jar=", program.auto_latest_jar)
            print("      cwd  =", program.working_dir(config.base_dir))
            print("      argv =", program.command_argv(config.base_dir))
    problems = config.validate()
    if problems:
        print("配置提示：")
        for item in problems:
            print("  !", item)
    else:
        print("配置校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
