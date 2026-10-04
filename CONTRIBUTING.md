# 贡献指南

> 先说清楚：这个项目是**个人自用工具**，作者不懂编程（详见
> [README 的「问题反馈与贡献」](README.md#七问题反馈与贡献)）。
> 欢迎 Issue 与 PR，但请把期望放低一点、把说明写清楚一点 —— 这样最容易被理解与合并。

---

<a id="一报告问题issue"></a>
## 一、报告问题（Issue）

Issues 已开启：<https://github.com/shuixuanle/QQBotLauncher/issues/new>

### 请提供这五项

| # | 内容 | 说明 |
| --- | --- | --- |
| 1 | **现象** | 你做了什么 → 期望什么 → 实际什么 |
| 2 | **复现步骤** | 具体到"点哪个按钮 / 敲哪条命令 / 开关哪个设置" |
| 3 | **环境** | Windows 版本、Python 版本、PyQt6 版本 |
| 4 | **日志** | 管理器日志区右键 →「复制日志」；崩溃时附命令行输出 |
| 5 | **配置片段** | `bots_config.json` 里相关的那一段（先脱敏） |

环境信息一条命令拿全：

```bat
python -c "import sys, platform; print(sys.version); print(platform.platform())"
python -c "from PyQt6.QtCore import QT_VERSION_STR; print('PyQt6 Qt', QT_VERSION_STR)"
```

<a id="脱敏必做"></a>
### 脱敏（必做）

贴之前把这些替换掉 —— 它们对定位问题没用，但会泄露你的隐私：

| 内容 | 例子 | 替换成 |
| --- | --- | --- |
| 本机绝对路径 | `D:\BOTBENTI\yumubot` | `<bot目录>` |
| QQ 号 / 群号 | `123456789` | `<QQ号>` |
| token / 密码 / API key | `ghp_xxx` | `<token>` |
| 内网地址 | `192.168.1.10:8080` | `<内网地址>` |

<a id="提问前先自查能省一轮往返"></a>
### 提问前先自查（能省一轮往返）

```bat
python tools\run_all_checks.py     :: 28 个静态检查一次跑完
python main.py --doctor            :: 只做导入与名字体检，不需要图形界面
python main.py --theme-debug       :: 外观相关问题（打印主题诊断并写 theme_debug.log）
python main.py --nav-debug         :: 左侧列表相关问题
```

---

<a id="二提交改动pull-request"></a>
## 二、提交改动（Pull Request）

### 流程

```bat
:: ① Fork 本仓库到你自己账号，然后：
git clone https://github.com/<你的用户名>/QQBotLauncher.git
cd QQBotLauncher
pip install -r requirements.txt

:: ② 开分支、改代码
git checkout -b fix/某个简短的说明

:: ③ 改完必须跑自检（见下），全绿再提交
python tools\run_all_checks.py

:: ④ 提交并推到你自己的仓库
git add -A
git commit -m "修复：……"
git push -u origin fix/某个简短的说明
```

然后在 GitHub 上开 PR，写清楚**改了什么、为什么改、怎么验证的**。

### 提交信息格式

`类型：做了什么`，多行时第二段写"为什么"：

```
UI：合并重复的窗格标题行

真机反馈"这两栏功能上是有重复的"，日志视图不再重复显示程序名/状态，
行数并入窗格标题栏，屏幕上只剩一行。
```

常用类型：`功能` / `修复` / `UI` / `文档` / `整理` / `工具`。

### 代码约定

| 项目 | 约定 |
| --- | --- |
| 缩进 | 4 空格（不用 Tab） |
| 注释与文档串 | **中文**，且要写"**为什么**这么做"——踩过什么坑、不这么写会怎样；不要复述代码本身 |
| 类型标注 | 公开函数请标注参数与返回值 |
| `.bat` / `.cmd` | **必须纯 ASCII**。cmd 按系统 ANSI 解析，中文会破坏 `if (...)` 块（本项目真踩过） |
| `.vbs` | 若必须用，存成 **UTF-16 LE + BOM** 或 GBK（无 BOM 的 UTF-8 会报 `800A0409`） |
| 路径 | 不要在仓库文件里写死个人绝对路径；脚本用相对路径推导或环境变量覆盖 |
| 禁止提交 | `bots_config.json`、`*.log`、`build/work/`、`dist/`、`__pycache__/`（`.gitignore` 已排除） |

<a id="自检必须全绿"></a>
### 自检（必须全绿）

```bat
python tools\run_all_checks.py          :: 一次跑完 28 个检查器
python tools\run_all_checks.py -v       :: 需要看细节时
python main.py --selftest               :: GUI 自检（环境与配置摘要）
```

28 个检查器各自盯着一个真实踩过的坑，例如：

| 检查器 | 挡住的坑 |
| --- | --- |
| `check_names.py` | 用了没导入的名字（运行到才炸） |
| `check_property_calls.py` | 把 `@property` 当方法调用（`TypeError: 'str' object is not callable`） |
| `check_pane_scope.py` | 窗格按钮误作用于全部程序 |
| `check_restart_flow.py` | "重启"在需要强制停止时不生效 |
| `check_launchers.py` | 启动脚本编码/解释器选择错误 |
| `check_branch_qss.py` | QSS 花括号未转义导致样式静默失效 |
| `check_method_decorators.py` | 残留的 `@staticmethod` 导致方法签名错位 |
| `check_doc_links.py` | README 锚点/截图路径失效（GitHub 上点不动、破图标，没有任何报错） |
| `check_ansi_log.py` | 日志区把 ANSI 转义序列当普通文字显示；以及深浅色板上读不清的颜色 |
| `check_ansi_live.py` | 【需 PyQt6】**槽里的异常 = 整个程序退出**（同名函数被覆盖导致的解包错误，真机上就是"闪退"） |
| `check_duplicate_defs.py` | 同一个模块/类里重名定义，后一个静默覆盖前一个（静态检查全绿，跑起来才炸） |
| `check_palette_studio.py` | 配色链路断一环：菜单入口 / 立即生效 / 存设置 / 启动载入 / 配色记录（预览与真机不一致、存了不生效） |
| `check_definition_order.py` | 模块级**先用后定义**（`py_compile` 全绿，`python main.py` 一启动就 NameError） |
| `check_console_output.py` | 检查器崩在 print 上（中文 Windows 控制台是 cp936，编不出 ▸ ⇄ 这类符号） |
| `check_command_argv.py` | 给 QProcess 的 argv 里混进引号（`python -c "…"` 的代码被当成字符串字面量，进程秒退、退出码 0） |
| `check_repo_layout.py` | 仓库又乱回去：根目录冒出杂物、tools/ 里多出来历不明的脚本、生成物没进 .gitignore、README 结构图过期 |
| `check_hydrant_command.py` | 提权命令被写成三层引号（PowerShell 把命令打印出来、程序没启动，退出码还是 0） |
| `check_hydrant_launcher.py` | 提权启动脚本写歪：非 ASCII / 纯 LF 换行 / 少了 `-Wait` / **在 `( )` 块里用 `%CD%`**（真机事故：路径被算成脚本目录）；本机没有该脚本时自动跳过 |

<a id="仓库布局约定根目录要干净"></a>
### 仓库布局约定（根目录要干净）

> **中文标题的锚点**：标题里带中文标点（`、？：（）「」`）时，GitHub 自动生成的
> 锚点要看它的标点规则，不同实现可能不一致 —— 真机出现过"目录点不动"。
> 约定：**这类标题的上一行放一个显式锚点** `<a id="…"></a>`，目录指向它。
> `python tools\check_doc_links.py` 会检查这条（漏了会红），
> 目录本身用 `python tools\maintenance\patch_readme_toc.py --apply` 重新生成。

根目录**只放**白名单里的东西（入口脚本、文档、配置样例、`app/ tools/ docs/ scripts/ build/`）。
加新文件之前先想清楚该放哪：

| 想加的东西 | 放哪 |
| --- | --- |
| 新检查器 | `tools/check_xxx.py`（名字必须以 `check_` 开头，不然不会被 `run_all_checks` 跑到） |
| 检查器共用的夹具 / 工具函数 | `tools/_xxx.py`（下划线开头，不当检查器跑） |
| 开发期一次性脚本、临时探针 | 本地忽略区（`tools/maintenance/`、`tools/diagnostics/_*.py`），**不要**提交 |
| 只给自己看的文档 / 截图 | `_local/`（整个目录被忽略） |
| 使用者要双击的入口 | 根目录（`启动（…）.bat` 这些） |

顺手加的：`tools/check_repo_layout.py` 会盯着这几条 —— 根目录白名单、`tools/` 里每个文件的身份、
生成物是否被忽略、有没有跟踪 `__pycache__`、README 的「项目结构」是否过期。
往根目录加东西时，把它加进 `ALLOWED_ROOT` 并写清用途（那一步就是逼自己想一遍）。

### 修 bug 时建议顺手补一条断言

如果这个 bug **以后还可能再犯**（尤其是"语法对、名字对，但语义错"的那类），
欢迎在 `tools/check_*.py` 里补一条断言 —— 这是本项目最有效的防回归手段。
写检查器时注意：

- 用 **AST** 判断结构（别用字符串匹配，注释里的字样会误报）；
- 涉及的类名/方法名要**先核实**（`tools/check_tool_api_usage.py` 就是为这类错误写的）。

---

<a id="三作者会怎么处理你的-pr"></a>
## 三、作者会怎么处理你的 PR

老实说：

1. 用 `gh pr diff` 读你的改动，交给 **DSH（DeepSeek Harness）** 解释它在干什么；
2. 在本地跑 `python tools\run_all_checks.py`，必要时手工复现；
3. **看不懂就直接问**你（可能在 PR 里来回几轮）；
4. 小改动（错字、文档、一行修复、补断言）通常很快合并；
5. 大改动可能被请求**拆小**，或因为维护能力有限而婉拒 —— 请别介意。

如果你的改动是"顺手重写一遍架构"，大概率会被婉拒；
如果是"修好一个具体问题 + 附一条断言"，基本都会合。
