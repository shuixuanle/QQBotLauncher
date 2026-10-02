# scripts/ 这个目录是干什么的

**放"需要特殊启动方式"的脚本，由管理器调用，而不是双击运行。**

典型用途只有一个：**需要管理员权限的程序**（例如消防栓 NewHydrant —— 它要占
`HttpListener` 的 URL 前缀，非管理员起不来）。

## 怎么用

1. 把你自己写好的启动脚本放进这个目录（`.bat` 最省事）；
2. 在管理器里给对应程序填两栏：

   | 栏位 | 填什么 |
   | --- | --- |
   | 命令 | `cmd.exe /c 你的脚本.bat`（**不要**带路径、不要加引号） |
   | 工作目录 | 本目录（`…\QQBot启动管理器\scripts`），脚本里就不用写自己的路径 |

   也可以只写脚本名并在「编辑程序」里勾上「通过命令解释器启动」，同样走 `cmd.exe /c`。

## 为什么提权要写在脚本里，而不是写在命令里

管理器把命令行的参数**原样**交给进程，引号要经过三层解释（管理器 → Windows →
PowerShell），实测很容易变成"命令被打印出来、程序没启动、退出码还是 0"。

写进 `.bat` 就只需要一条没有引号的命令：

```bat
rem 检查是否已经是管理员
net session >nul 2>&1
if %errorlevel%==0 goto run

rem 不是就用 UAC 重新拉起自己（-Wait 让父进程等到子进程结束，好把退出码带回来）
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath '%~f0' -Verb RunAs -Wait -PassThru; exit $p.ExitCode } catch { exit 1223 }"
endlocal & exit /b %errorlevel%

:run
rem 这里放真正要跑的命令
```

几个踩过的坑，抄的时候注意：

| 坑 | 说明 |
| --- | --- |
| 提权后**当前目录**会变成 `C:\Windows\System32` | 先 `cd /d "%~dp0"` 再启动自己 |
| `Stop-Process -Verb RunAs` 不带 `-Wait` | 父进程会立刻退出，管理器看到"已退出"，退出码也丢了 |
| 在 `( … )` 块里用 `%CD%` | cmd 在**解析整块时**就展开它，`pushd` 等于白做 —— 真机事故：路径被算成"脚本目录\…"，报 `Cannot enter folder` + 退出码 3。要派生路径就只用 `%~dp0` |
| `.bat` 里写中文 | cmd 按系统 ANSI 解析 `.bat`，`echo`/`if` 块里的多字节字符可能提前闭合括号 —— **保持纯 ASCII**，中文说明另存 md |
| 文件换行必须是 CRLF | 纯 LF 的 `.bat` 会让 `goto`/标签/`for` 块行为异常 |
| 别加 `pause` | 会占住控制台，管理器那边看不到结束 |

## 这个目录里的文件

- `README.md`（就是你正在看的这个）—— 进仓库；
- **其它文件都不进仓库**：作者本机的 `start_hydrant.bat` 是针对自己那台机器写的
  （路径、dotnet 版本都是本地的），对别人没有意义，所以 `.gitignore` 里忽略了它。
  你照着上面的模板写自己的脚本即可。

> 想验证自己的脚本没写歪：`python tools\check_hydrant_launcher.py`
> 会检查 ASCII / CRLF / `net session` 提权 / `-Wait` / 退出码透传，
> 以及"没有在 `( … )` 块里用 `%CD%`"这条真机事故的回归项；
> 本机没有这个脚本时它会直接跳过。
