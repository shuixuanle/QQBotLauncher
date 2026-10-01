# 上传到 GitHub —— 操作指南

本项目已经是一个**本地 git 仓库**并完成了首次提交（`main` 分支）。剩下的是
"在 GitHub 上建仓库 → 推送上去"。下面两条路任选一条。

> 上传前已经做过的整理（不用你再操心）：
> - `bots_config.json` / `bots_config.json.bak` —— **不入库**（含本机绝对路径与个人配置），
>   仓库里只留 `bots_config.example.json` 供参考字段结构；
> - `build/work`（约 82 MB 的 PyInstaller 中间产物）、`dist/`、`__pycache__/`、
>   各种调试日志 —— 全部由 `.gitignore` 排除；
> - `scripts/start_hydrant.bat` 里硬编码的 `D:\BOTBENTI\...` 已改成**相对路径自动推导**，
>   也可以用环境变量 `QQBOT_HYDRANT_DIR` 覆盖；
> - 全项目扫描过一遍：**没有 API key / token / 密码**（`env` 里只有编码类变量）。
> - 已加入 `LICENSE`（MIT）。

---

## 路线 A：GitHub CLI（推荐，浏览器登录一次，长期省心）

### 1. 安装 gh

```bat
winget install --id GitHub.cli
```

装完**关掉并重新打开**命令行（PATH 才会生效），然后确认：

```bat
gh --version
```

### 2. 登录（走浏览器，不用手输 token）

```bat
gh auth login
```

依次选择：

| 提问 | 选 |
| --- | --- |
| What account do you want to log into? | **GitHub.com** |
| What is your preferred protocol for Git operations? | **HTTPS** |
| Authenticate Git with your GitHub credentials? | **Yes** |
| How would you like to authenticate? | **Login with a web browser** |

然后它会显示一个 **8 位验证码**（形如 `ABCD-1234`），按回车会自动打开浏览器，
粘贴验证码 → 授权即可。

### 3. 创建仓库并推送（一条命令搞定）

把 `<仓库名>` 换成你想要的名字（ASCII 更通用，例如 `QQBotLauncher`）：

```bat
cd /d "C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器"

:: 公开仓库
gh repo create <仓库名> --public --source=. --remote=origin --push

:: 或者私有仓库
gh repo create <仓库名> --private --source=. --remote=origin --push
```

`gh` 会自动：在 GitHub 上建仓库 → 添加 `origin` 远程 → 把 `main` 推上去。
命令结束后它会打印仓库地址。

以后改完代码只要：

```bat
git add -A
git commit -m "说明这次改了什么"
git push
```

---

## 路线 B：网页建仓库 + token 推送（不想装 gh 时）

### 1. 在网页上建**空**仓库

打开 <https://github.com/new>：

- **Repository name**：填仓库名（例如 `QQBotLauncher`）；
- **Public / Private**：按需选；
- ⚠️ **不要**勾选 "Add a README file"、".gitignore"、"license" ——
  否则远程会先有一个提交，推送时需要先合并（多一步，容易冲突）。

### 2. 生成 Personal Access Token

<https://github.com/settings/tokens> → **Generate new token (classic)**：

- Note 随便写，例如 `qqbot-launcher-push`；
- Expiration 选 30 天或自定义；
- 勾选 **`repo`**（细粒度 token 则给 **Contents: Read and write**）；
- 生成后**立刻复制**（页面关掉就再也看不到）。

### 3. 用项目里的脚本推送（token 不落盘）

```bat
cd /d "C:\Users\Strix\Desktop\conformity\dsh以及通过这个制作的程序\QQBot启动管理器"
python tools\git_push.py --user <你的GitHub用户名> --repo <仓库名>
```

脚本会：

1. 检查工作区是否干净、列出最近提交；
2. **逐项确认**敏感文件确实被忽略（`bots_config.json`、日志、`build/work`、`dist`）；
3. 用隐藏输入方式让你粘贴 token（不回显、**不写入任何文件**）；
4. 用一次性 URL 推送，成功后只登记**不含 token** 的 `origin`。

想先干跑一遍看它会做什么（不推送）：

```bat
python tools\git_push.py --user <用户名> --repo <仓库名> --dry-run
```

---

## 上传后建议补的几件事

1. **仓库描述与 Topics**（网页右上角 ⚙️）：
   `PyQt6` `qq-bot` `process-manager` `launcher` `windows`
2. **首次 push 之后的 .gitignore 校验**：在网页上确认**看不到** `bots_config.json`，
   否则说明它有历史提交，需要 `git rm --cached bots_config.json` 后再提交。
3. 如果你希望别人能直接跑起来，README 顶部的「环境要求 / 安装依赖 / 运行方法」
   已经齐全，无需改动。

---

## 常见问题

**推送时提示 `failed to push some refs` / `non-fast-forward`**
远程已有提交（多半是建仓库时勾了 README）。执行：

```bat
git pull --rebase origin main
git push -u origin main
```

**`gh auth login` 卡在 "Press Enter to open github.com in your browser"**
手动打开它给出的 <https://github.com/login/device>，输入那 8 位验证码即可。

**提交里出现了不该有的文件**
先从索引里移除（文件保留在本地）：

```bat
git rm --cached <路径>
git commit -m "从版本库移除 <路径>"
```

若该文件已在**历史提交**里，还需要改写历史（`git filter-repo`）——
但本项目的首次提交就已经把配置排除在外，通常不会遇到这种情况。
