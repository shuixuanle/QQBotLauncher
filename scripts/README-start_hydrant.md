# 消防栓（NewHydrant）启动脚本说明

本目录的 `start_hydrant.bat` 是**消防栓 bot 的受控启动入口**，由 QQBot启动管理器调用。

## 管理器里怎么配

| 字段 | 值 |
| --- | --- |
| 启动命令 | `cmd.exe /c start_hydrant.bat` |
| 工作目录 | `…\QQBot启动管理器\scripts`（即本文件所在目录） |
| 通过命令解释器启动 | 不需要勾（命令里已经写了 `cmd.exe /c`） |

**命令里一个引号都不需要** —— 这是刻意的，原因见下。

## 脚本做什么

1. `net session` 判断当前是否已是管理员；
2. 不是 → 用 UAC 提权重新运行自己（`-Wait -PassThru`，这样本进程会一直存活到子进程结束）；
3. 是 → 进入编译产物目录，**前台**运行 `dotnet Bleatingsheep.NewHydrant.Bot.dll`；
4. 把子进程退出码原样返回给管理器。

换机器时只改脚本里的 `BOT_DIR` 一行。

## 为什么提权逻辑写在脚本里，而不是写在"启动命令"里

管理器的 argv 是**原样**交给 `QProcess` 的；Qt 会把参数内部的双引号转义成
`\"`，而 PowerShell 又把 `\"` 当成自己的转义符 —— **三层引号嵌套必然出错**。

真机事故记录（2026-10）：

| 阶段 | 现象 |
| --- | --- |
| 第一版命令 | 命令里含 `\"\"…\"\"`（从别处复制带进来的多余转义）→ `UnexpectedToken`，退出码 1 |
| 第二版命令 | 改成 `powershell -Command "Start-Process …"` → PowerShell **把命令当字符串打印出来**，退出码 0 但 bot 没启动 |
| 定稿 | 提权写进 `.bat`，命令退化为 `cmd.exe /c start_hydrant.bat`（零引号）→ 正常 |

## 维护注意（这两条都踩过坑）

1. **本 `.bat` 必须保持纯 ASCII**。
   `cmd.exe` 按**系统 ANSI 代码页**解析 `.bat`；多字节字符出现在 `echo` / `if` 块里
   会被误解析 —— 全角右括号的尾字节可能被当成半角 `)`，把 `if (...)` 块提前闭合，
   于是后面的行全被当成命令执行，输出一堆
   `'xxx' is not recognized as an internal or external command`。
   中文说明就放在本文件里。

2. **不要加 `pause`**。
   它会占住控制台，导致管理器"停止"时窗口残留、退出码也不准。
   提权窗口本身就会保留输出，不需要 `pause`。

## 关于"停止"

提权后的 bot 跑在**独立的提权进程树**里：

- 管理器用 `taskkill /T` 能杀掉整棵树（父进程是提权后的 `cmd.exe`）；
- 但**不会**自动关闭那个管理员控制台窗口 —— 必要时手动关掉即可。

## 如果想免提权运行

删掉脚本里的第 (1)(2) 段（`net session` 判断与提权块），直接从 `:run` 开始即可：

```bat
cd /d "%BOT_DIR%"
dotnet Bleatingsheep.NewHydrant.Bot.dll
```

前提是 `dotnet` 与 bot 所需的端口/文件权限对普通用户可用 ——
你手动执行 `dotnet Bleatingsheep.NewHydrant.Bot.dll` 能成功，说明通常是够的。

## 关于管理员权限与 UAC 弹窗（2026-10 补充）

消防栓 bot 的 `HttpListener` 要绑定 http 前缀，**普通权限必然失败**：

```
System.Net.HttpListenerException (5): 拒绝访问。
   at System.Net.HttpListener.AddPrefixCore(String registeredPrefix)
```

所以它必须提权运行。两种用法：

| 管理器怎么启动 | 弹窗次数 | 说明 |
| --- | --- | --- |
| `python main.py`（普通权限） | **每次启动消防栓弹 1 次 UAC** | 脚本内部用 `Start-Process -Verb RunAs` 提权 |
| `python main.py --elevate`（管理员） | **一次都不弹** | 管理器本身是管理员，子进程继承令牌 |

`--elevate` 的做法：用 PowerShell 的 `Start-Process -Verb RunAs` 把管理器自己重新拉起
（弹一次 UAC），并给子进程加 `--no-elevate` 防止无限套娃。子进程继承管理员令牌后，
再启动消防栓就**不需要再提权**。

管理器的**状态栏右下角**常驻一个权限标记，一眼能看出当前是哪种模式：

- `🛡 管理员模式（提权程序不再弹 UAC）`
- `普通权限（提权程序启动时会弹 UAC）`

> **为什么不能"根本不弹窗"**：Windows 不允许普通权限进程静默提权
> （UAC 的"批准模式"就是干这个的），所以只有"起点就是管理员"才能零弹窗。
> 把 UAC 整个关掉（`EnableLUA=0`）虽然也能做到，但会显著降低全系统安全性，不建议。

## 停止消防栓时的两条日志是正常的

用「停止」关掉消防栓时，日志区可能出现：

```
[管理器] 消防栓 / NewHydrant 主程序 需要强制结束（退出码 128：进程树里有不响应关闭请求的子进程），即将结束整棵进程树。
[管理器] 消防栓 / NewHydrant 主程序 在宽限期内没有自行退出，正在强制结束整棵进程树（PID xxxx）——这是正常的两段式停止，不是错误。
```

这是**设计如此**：`taskkill /T`（不带 `/F`）只能"请求"进程退出，而 `cmd.exe` /
`dotnet` 这类控制台宿主**不响应**这个请求，所以第一步必然返回 128，
接着管理器用 `taskkill /T /F` 强制结束整棵树。结果是对的，日志已改成说明性措辞。

如果你希望它"优雅退出"，可以在脚本里加一个信号处理 —— 但 .NET 控制台程序通常
只能被强制结束，无需折腾。
