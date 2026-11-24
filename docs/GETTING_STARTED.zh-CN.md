# 第一次使用 Agent Orbit

从下载应用到看到第一条会话。本指南适用于没有项目源码、没有 Orbit 个人配置的新用户；日常配置在图形界面完成，无需修改 JSON。

## 1. 下载并打开

1. 在 [稳定版 5.2.6 发布页](https://github.com/Arkaida/agent-orbit/releases/tag/v5.2.6) 的 **Assets** 中下载 **`Agent-Orbit-5.2.6-macos-arm64.zip`**，用于 Apple Silicon Mac。`Source code` 是开发源码，不能直接双击运行。
2. 解压，把 **Agent Orbit.app** 放进“应用程序”或自己的常用目录，再双击。要求 **macOS 13+**；Intel Mac 需[从源码构建](../README.md#从-main-体验新声浪)。
3. 如果 macOS 阻止打开，确认下载来自上述仓库后，在“系统设置 → 隐私与安全性”中使用“仍要打开”。社区构建尚未 Apple 公证。
4. 点击小窗右上角的齿轮，或屏幕菜单栏的 **AO → 设置…**。

应用自带 Python，启动 Orbit 无需安装 Node.js、Python 或 Codex 插件。**被监控的 Codex / Claude 则需要你自己安装、登录并启动**；Orbit 不会替你创建或执行 Agent 任务。

全新安装默认只开启本机 Codex 监控。尚未打开 Codex 会话时显示空列表属于正常情况。主分支的 5.2.8 预览新增四路声浪、自适应绘图和应用内声浪图解；5.2.6 的配置入口相同，尚无这些新显示功能。

## 2. 选择监控来源

| 你想看什么 | 使用哪条流程 | 必须先准备什么 |
| --- | --- | --- |
| 这台 Mac 上的 Codex | [A：本机](#a本机-codex) | 已经打开的 Codex 会话及可读的运行日志 |
| Linux 服务器上的 Codex 或 Claude Code | [B：远端](#blinux-远端-codex--claude-code) | 无交互 SSH、远端 Python 3、tmux、在 tmux 中运行的 Agent |
| 两边都看 | A、B 都配置 | 同时保留两种监控开关 |

**本机暂不采集 Claude Code。** 远端监控读取 Linux 上现有的 tmux 会话；直接运行在普通 SSH 终端、但不在 tmux 中的 CLI 不会自动出现在远端列表中。无需部署本项目作者的同步脚本或 VS Code 任务。

## A：本机 Codex

1. 先按你平时的方式，在 Codex CLI、Codex 桌面客户端或 VS Code 中打开一个实际会话，并发送一次任务。
2. 在 Orbit **设置 → 监控 → 本机 Codex**，开启 **监控本机 Codex（桌面端 / CLI / VS Code）**。
3. **Codex 数据目录**保留默认值。通常是 `~/.codex`；如果你的 Codex 使用自定义 `CODEX_HOME`，填写那个数据根目录的完整路径。这里填的是保存会话日志的目录，**不是工程代码目录**。从 Finder 启动的应用不一定继承终端中的环境变量，需要时手动填写。
4. 首次建议开启 **关注全部当前打开的本机会话**。出现会话后，取消全选并逐项勾选，就能只关注指定几个。默认最多显示 16 个，可按需调大。
5. 点击窗口底部 **保存设置**。取消或直接关掉设置窗口不会应用草稿。

成功时，小窗会显示本机来源在线和会话名称；任务运行时显示运行，CLI 结束并确认等待下一次输入后显示等待。可执行一个简短任务验证状态变化。首次打开时已在等待的会话不会补响一次“刚完成”的声音。

本机发现依据进程当前打开且可读取的会话日志，**不是导入所有历史聊天**。点进本机卡片会尝试唤起已安装的 Codex 客户端；不能自动跳到指定聊天或接回 CLI / VS Code 的原终端。继续输入时请回到原会话所在的应用或终端。

## B：Linux 远端 Codex / Claude Code

以下命令中的 `work-server`、`work1` 和 Socket 路径均为示例，换成你自己的值。标为“Mac 终端”的命令在本机执行，标为“服务器”的命令在 SSH 登录后执行。

### B1. 先确认 SSH 可用

在 **Mac 终端**中，先用你平时的连接方式登录一次：

```bash
ssh work-server
```

首次连接按正常 SSH 流程核对服务器指纹，再完成登录。Orbit 没有 SSH 密码或验证码输入框，后台监控要求密钥或已解锁的 SSH agent 能够无交互登录。退出 SSH 回到 Mac 后验证：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 work-server true
```

**没有输出且正常退出**表示这项验证通过。如果仍提示认证失败、需要密码或主机指纹未知，先把终端 SSH 登录配置好，再开远端监控。

如果目前没有 SSH 别名，可编辑 **Mac 的 `~/.ssh/config`**，把下面的示例值换成自己的服务器信息；端口、密钥、跳板等均在这个文件中配置：

```sshconfig
Host work-server
    HostName server.example.com
    User alice
    Port 22
    IdentityFile ~/.ssh/id_ed25519
```

`IdentityFile` 只在你使用该密钥时保留；这段配置不会自动生成密钥或配置服务器。已有可用 SSH 别名时直接沿用，无需新建。

### B2. 确认远端工具和会话

在 **Mac 终端**检查后台实际使用的非交互环境：

```bash
ssh -o BatchMode=yes work-server 'python3 --version'
ssh -o BatchMode=yes work-server 'tmux -V'
ssh -o BatchMode=yes work-server "tmux list-sessions -F '#{session_name}'"
```

前两条应输出版本，第三条应列出目标 tmux 会话名，例如：

```text
work1
review
```

如果使用自定义 tmux Socket，第三条默认找不到目标也可能是正常的，按下一节查实际路径。非交互环境提示 `command not found` 时，要确保该环境也能找到 Python 3 / tmux，不能只在交互式终端中可用。

如果尚无 tmux 会话，可在**服务器**新建一个示例会话，再运行你已经安装并登录的 Agent：

```bash
tmux new-session -s work1
codex
```

使用 Claude Code 时，把最后一行换成 `claude`。已有任务时沿用原 tmux 会话。要离开当前 tmux 连接并保留任务，按 **Ctrl+B**，松开后按 **D**；再用 `exit` 退出 SSH 回到 Mac。

### B3. 确定 tmux Socket

普通 `tmux` 创建的默认服务：在 Orbit 中把 Socket **留空**。

如果目标会话通过 `tmux -L ...`、`tmux -S ...` 或工具脚本创建，请在**目标 tmux 服务的 shell 窗格内**执行下面的命令，不要粘进 Codex / Claude 输入框。可以先按 **Ctrl+B**，松开后按 **C**，在同一 tmux 服务中新开一个 shell 窗口：

```bash
tmux display-message -p '#{socket_path}'
```

复制输出的完整路径，例如 `/tmp/tmux-1000/agents`，填进 Orbit。不要填写 `-L agents`、一整条 tmux 命令或会话名。可在 Mac 上用实际路径核对会话列表：

```bash
ssh work-server "tmux -S '/tmp/tmux-1000/agents' list-sessions -F '#{session_name}'"
```

登录的远端用户必须有权限读取这个 Socket、目标进程及日志。Orbit 一次远端配置连接一个 SSH 主机和一个 tmux Socket。

### B4. 在 Orbit 填写这些字段

打开 **设置 → 监控 → 远端会话**：

| 界面字段 | 怎么填 | 示例 |
| --- | --- | --- |
| 启用 SSH 远端监控 | 打开 | 开 |
| SSH 主机 | 刚验证通过的 SSH 别名，或 `user@host`；不要带 `ssh`、空格、端口选项或密码 | `work-server` |
| tmux Socket（留空使用默认） | 默认服务留空；自定义服务填 B3 查到的完整 Socket 文件路径 | 留空，或 `/tmp/tmux-1000/agents` |
| 关注会话 | 每行一个 B2 / B3 列出的 **tmux session 名**；留空显示该 Socket 下全部会话 | 见下方 |

关注列表可以填写：

```text
work1
review
```

它不是聊天标题、工程路径、tmux 窗口名、窗格 `%ID` 或 VS Code 任务名。只看远端时可以关掉本机监控，但至少保留一种来源。

点击底部 **保存设置**。监控会自动重新连接相应来源；无需重启远端 Agent，也不用重新打开 Orbit。成功时应看到**远端在线和目标会话名称**。若两种来源同时开启，要分别看本机、远端的状态，不能只凭某个来源在线就认为另一边也已连接。

### B5. 回到原远端会话

**稳定版 5.2.6 的独立应用包存在进入按钮依赖文件缺失问题**：监控可用，但卡片点击进入可能失败。主分支已补齐打包入口和内置 Python 的定位，下次从 main 构建即可使用。5.2.6 用户先在 **Mac 终端**进入原会话：

```bash
# 默认 Socket
ssh -t work-server 'tmux attach-session -t =work1'

# 自定义 Socket：路径换成 B3 的实际输出
ssh -t work-server "tmux -S '/tmp/tmux-1000/agents' attach-session -t =work1"
```

选择与你的配置相符的一条即可。新构建的主分支版本可在面板中**双击远端会话卡片，或点卡片右上角的 ↗**，打开终端进入；悬浮球模式先双击球展开面板。macOS 首次询问是否允许 Orbit 控制“终端”时，需要允许才能自动打开；之后也可在“系统设置 → 隐私与安全性 → 自动化”检查。离开连接仍按 **Ctrl+B**、再按 **D**，任务继续运行。

## 3. 用一分钟确认设置成功

1. **看到会话。** 面板显示你选择的来源在线，且出现目标名称。
2. **看到状态变化。** 在原 CLI 发一条简短任务，观察 Orbit 从运行变为等待输入。等待色为绿色，运行色随皮肤变化；红色是异常，黄色表示有事需要处理。
3. **确认声音。** 在 **设置 → 提醒** 开启提示音及“任务结束时提示”，可先点“试听完成提示”，然后保存。首次连接不补报历史完成。
4. **找到收起入口。** 小窗的减号收起为悬浮球；球单击切换大小，双击恢复小窗。找不到球时用菜单栏 **AO → 显示悬浮窗**。

确认基础监控正常后，再配置分组、皮肤和久等提醒。需要互斥任务成组时，在 **设置 → 监控 → 互斥会话组** 新建组并至少选两个成员，再保存；分组不会自动关注未关注的会话。

## 按症状排查

| 看到的情况 | 先检查什么 |
| --- | --- |
| 本机在线但没有会话 | Codex 是否已打开实际会话；数据目录是否与真实 `CODEX_HOME` 一致；是否关闭了全选并漏勾目标；是否超过“最多显示”的数量。运行进程未持有可读会话日志时也不会被发现 |
| 看不到本机 Claude | 当前仅支持本机 Codex；Claude Code 使用 Linux 远端 tmux 模式 |
| SSH 认证失败 / 正在重连 | B1 的 `BatchMode` 验证是否通过；网络、用户名、密钥 agent 和首次主机指纹是否已处理。改完设置后要点保存 |
| `python3` / `tmux` 找不到 | 用 B2 的非交互 SSH 命令检查服务器 PATH |
| 远端在线但没有目标 / 未找到会话 | SSH 用户、Socket、真实 session 名是否对应；自定义 Socket 用 B3 的命令列出。关注列表留空可临时排除名字填错 |
| 显示待核实 | 当前 CLI 版本的日志/终端格式未被识别，或暂时缺少可靠证据；回原会话核对。不能把它当成已完成 |
| 点击远端卡片进不去 | 稳定 5.2.6 使用 B5 的手动进入方法；main 新构建检查系统“自动化”中是否允许打开终端 |
| 没有完成提示音 | 检查系统音量、设置中的总提示音和完成提示开关；首次打开就已经等待的会话不补响 |
| 找不到窗口或悬浮球 | 点击菜单栏 AO → 显示悬浮窗；已退出应用时重新打开 `.app` |

需要检查个人设置文件时，可用 **设置 → 关于 → 打开个人设置目录**。新安装通常在 `~/Library/Application Support/Agent Orbit/`，旧安装可能沿用 `Codex Remote`；排查时以这个按钮打开的位置为准。

## 可选：终端命令与更多设置

只下载 `.app` 不会自动安装 `ao` 命令。想使用 `ao`，请先取得项目源码，并按[使用指南的简短命令](USAGE.zh-CN.md#简短命令)设置；只用图形监控可以跳过。

[完整使用指南](USAGE.zh-CN.md) · [声浪图解（main 预览）](WAVE_GUIDE.zh-CN.md) · [状态判断边界](STATUS.md) · [返回项目首页](../README.md)
