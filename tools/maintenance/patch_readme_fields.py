# -*- coding: utf-8 -*-
"""收尾 README：
  1. §4.1 的 JSON 示例后补字段说明 + "这些字段全部由界面写入"；
  2. 项目结构里补 LICENSE / 上传到GitHub.md。

用法：
    python tools\\maintenance\\patch_readme_fields.py            # 预览
    python tools\\maintenance\\patch_readme_fields.py --apply    # 写入
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"

OLD_FIELDS = (
    "文件损坏时程序会自动把它备份成 `bots_config.json.broken-<时间戳>` "
    "并重建默认配置，不会直接丢数据。"
)

NEW_FIELDS = """字段含义：`role` 决定谁是"主程序"（日志占据大窗格、`[LATEST_JAR]` 一般只给主程序用）；
`delay` 是启动前等待秒数（后面的程序用来等前面的端口就绪）；`via_shell` 为 `true` 时整条命令
交给 `cmd.exe /c`（`&&`、`|`、`>` 等语法原样生效）；`auto_latest_jar` 为 `true` 时把
`[LATEST_JAR]` 换成工作目录下最新的 `.jar`。

> 上面这些字段**全部由界面写入**：你在「新建 Bot / 编辑当前 Bot」里填什么，程序就按同样的
> 顺序写进这个文件（`indent=2`、UTF-8、先写临时文件再原子替换）。手工编辑当然也可以，
> 只是注意别写坏 JSON —— 写坏了会走"备份 + 重建默认配置"那条路。

文件损坏时程序会自动把它备份成 `bots_config.json.broken-<时间戳>` 并重建默认配置，不会直接丢数据。"""

OLD_TREE = "├── .gitignore                  ← 忽略配置/日志/打包产物"
NEW_TREE = ("├── .gitignore                  ← 忽略配置/日志/打包产物\n"
            "├── LICENSE                     ← MIT 许可证\n"
            "├── 上传到GitHub.md             ← 把本项目传到自己仓库的步骤")


def main() -> int:
    apply = "--apply" in sys.argv
    src = README.read_text(encoding="utf-8")
    changed = []

    if "上面这些字段**全部由界面写入**" in src:
        print("  跳过（字段说明已存在）")
    else:
        if src.count(OLD_FIELDS) != 1:
            print("!! 字段锚点出现 {} 次".format(src.count(OLD_FIELDS)))
            return 1
        src = src.replace(OLD_FIELDS, NEW_FIELDS, 1)
        changed.append("§4.1 补字段说明 + 界面写入提示")

    if "上传到GitHub.md             ←" in src:
        print("  跳过（项目结构已含新文件）")
    elif src.count(OLD_TREE) == 1:
        src = src.replace(OLD_TREE, NEW_TREE, 1)
        changed.append("项目结构补 LICENSE / 上传指南")
    else:
        print("!! 结构锚点出现 {} 次".format(src.count(OLD_TREE)))

    for item in changed:
        print("  - {}".format(item))
    if not changed:
        print("无需修改。")
        return 0
    if not apply:
        print("（预览）加 --apply 才会写入。")
        return 0
    README.write_text(src, encoding="utf-8")
    print("已写入 {}".format(README.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
