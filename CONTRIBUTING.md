# 贡献指南

> 先说清楚：这个项目是**个人自用工具**，作者不懂编程（详见
> [README 的「问题反馈与贡献」](README.md#七问题反馈与贡献)）。
> 欢迎 Issue 与 PR，但请把期望放低一点、把说明写清楚一点 —— 这样最容易被理解与合并。

---

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

### 脱敏（必做）

贴之前把这些替换掉 —— 它们对定位问题没用，但会泄露你的隐私：

| 内容 | 例子 | 替换成 |
| --- | --- | --- |
| 本机绝对路径 | `D:\BOTBENTI\yumubot` | `<bot目录>` |
| QQ 号 / 群号 | `123456789` | `<QQ号>` |
| token / 密码 / API key | `ghp_xxx` | `<token>` |
| 内网地址 | `192.168.1.10:8080` | `<内网地址>` |

### 提问前先自查（能省一轮往返）

```bat
python tools\run_all_checks.py     :: 26 个静态检查一次跑完
python main.py --doctor            :: 只做导入与名字体检，不需要图形界面
python main.py --theme-debug       :: 外观相关问题（打印主题诊断并写 theme_debug.log）
python main.py --nav-debug         :: 左侧列表相关问题
```

---

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

### 自检（必须全绿）

```bat
python tools\run_all_checks.py          :: 一次跑完 26 个检查器
python tools\run_all_checks.py -v       :: 需要看细节时
python main.py --selftest               :: GUI 自检（环境与配置摘要）
```

26 个检查器各自盯着一个真实踩过的坑，例如：

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

### 修 bug 时建议顺手补一条断言

如果这个 bug **以后还可能再犯**（尤其是"语法对、名字对，但语义错"的那类），
欢迎在 `tools/check_*.py` 里补一条断言 —— 这是本项目最有效的防回归手段。
写检查器时注意：

- 用 **AST** 判断结构（别用字符串匹配，注释里的字样会误报）；
- 涉及的类名/方法名要**先核实**（`tools/check_tool_api_usage.py` 就是为这类错误写的）。

---

## 三、作者会怎么处理你的 PR

老实说：

1. 用 `gh pr diff` 读你的改动，交给 **DSH（DeepSeek Harness）** 解释它在干什么；
2. 在本地跑 `python tools\run_all_checks.py`，必要时手工复现；
3. **看不懂就直接问**你（可能在 PR 里来回几轮）；
4. 小改动（错字、文档、一行修复、补断言）通常很快合并；
5. 大改动可能被请求**拆小**，或因为维护能力有限而婉拒 —— 请别介意。

如果你的改动是"顺手重写一遍架构"，大概率会被婉拒；
如果是"修好一个具体问题 + 附一条断言"，基本都会合。
