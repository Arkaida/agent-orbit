# Agent Orbit

A small native macOS monitor for local Codex and remote Codex / Claude sessions.

**5.0.0 — 本机 + 远端监控、统一设置与四套皮肤。**

用紧凑的置顶窗口或桌面会话环，查看 Agent 何时真正等待下一次输入、何时需要处理异常。绿色表示等待，橙色表示运行或待处理，红色表示异常。保留终端与在线 Agent 数量只提供上下文，不会把已结束的主对话误判成运行中。

## 功能

- 同时监控本机 Codex 和 SSH 远端 Codex / Claude；两种来源独立显示连接状态。
- 本机自动发现桌面端、CLI、VS Code 当前打开的 Codex 主会话，读取实际开始、结束与异常记录。
- 原生设置窗口：监控来源、SSH 主机、tmux Socket、两种关注列表、采样间隔、提示音、置顶、透明度、悬浮球大小及显示内容。
- 四套皮肤：霓虹深空、极光紫、石墨黑、晴空浅色，同时应用到面板和会话环。
- 完成与异常提示音可分别开关；已恢复的问题自动消失，不显示执行历史。
- 圆周会话名、双圈布局、真实输出活动轨迹，支持收起、拖动和恢复。
- 只读采集、Python 标准库，无遥测、账户注册、HTTP 服务或远端 hooks。

## 快速开始

直接使用发布版只需 **macOS 13+**，Python 运行环境已经内置；无需 Codex、Node.js 或任何插件来启动本应用。从源码构建时才需要 Python 3.9+ 和 Xcode Command Line Tools。远端模式另需可无交互登录的 SSH，以及远端 Python 3、tmux。本机采集使用系统 `ps` 和 `lsof`。

```bash
sh build_desktop.sh
sh ./agent-orbit panel
```

也可双击构建出的 `Agent Orbit.app`。首次安装默认只启用本机监控；已有安装会沿用迁移后的远端配置。点击面板右上角齿轮，或菜单栏 `AO → 设置…`，打开统一设置。

设置中的 SSH 主机填写你已有的 SSH 配置别名或 `user@host`。Socket 留空使用远端默认 tmux，使用自定义 Socket 时填写完整路径。远端关注列表每行一个会话名，留空表示全部。

本机关联当前打开的 Codex 根会话，可选择全部或逐个关注。未使用可识别运行记录的版本会显示待核实。点击本机会话会唤起已安装的 Codex 客户端；远端卡片进入原 tmux 会话。退出 tmux 连接使用 **Ctrl+B**，松开后按 **D**，远端任务继续运行。

## 简短命令

新命令为 `ao`，原有 `cr` 与 `codex-remote` 入口继续兼容。将下面这行加入 `~/.zshrc`，路径替换为你的项目目录：

```zsh
source '/path/to/agent-orbit/shortcuts.zsh'
```

```bash
ao p                # 桌面面板
ao                  # 终端持续监控远端（新安装没有远端配置时监控本机）
ao local            # 终端持续监控本机 Codex
ao l                # 单次列出
ao + work1 work2    # 添加远端关注
ao - work2          # 移除远端关注
ao f                # 查看远端关注
ao work1            # 进入远端会话
```

终端监控使用 ↑↓ 选择、Enter 进入远端、v 查看画面、q 退出。运行中的旧终端监控需要退出并重新启动才能加载新代码。

## 设置与数据

macOS 个人数据保存于 `~/Library/Application Support/Agent Orbit/`：

- `settings.json`：来源、外观、声音等设置。
- `follow.json`：远端关注列表，桌面与命令行共用。
- `.logs/connection.log`：有界连接诊断，不保存会话正文。

源码和应用包不携带个人设置。已有安装自动沿用原 `Codex Remote` 配置目录；新安装使用 `Agent Orbit`。旧项目中的 `follow.json` 在首次迁移时保留原件。可设置 `AGENT_ORBIT_DATA_DIR` 使用独立配置目录，也兼容旧的 `CODEX_REMOTE_DATA_DIR`；非 macOS 的终端模式使用 `$XDG_CONFIG_HOME/agent-orbit`。本机 Codex 数据目录可在设置中修改，默认使用 `CODEX_HOME` 或 `~/.codex`。

设置示例见 [settings.example.json](../settings.example.json)。保存后生效，变更连接参数时仅重新连接对应来源。皮肤和显示选项不会重启远端 Agent。

## JSONL 接口

```bash
sh ./agent-orbit watch --json                  # 原始远端采样
sh ./agent-orbit --local watch --json          # 原始本机采样
python3 -u desktop_bridge.py                # 按设置合并的处理后数据流
```

每行一个 JSON 对象，适合子进程逐行读取。桥接流包含 `rows`、`events`、`error_events`、`active_error_events`、`connections`、`local_sessions` 和全局连接概况。每行会话的 `source` 为 `local` / `remote`，`available` 表示该行来源是否新鲜；**不要用全局 `connected` 代替单行状态**。本地与远端的会话 ID 互不冲突。

`background_agents` 表示日志确认正在工作的子任务，`background_agents_total` 是可识别的在线总数，`background_terminals` 是保留终端数量。读取这些数值时检查相应的 `*_observable` / `background_checking`。本机共享 app-server 的终端无法准确归属单个对话时省略数量，不猜成 0。当前不提供 HTTP / REST 接口。

## 状态识别与边界

采样默认 0.5 秒，实际延迟包含采集和传输。输入页面与正文稳定后才确认结束；仍在思考、请求未结束或实际子任务工作时不会仅凭静默报完成。保留的 shell 或服务不会阻止主对话显示等待输入。首次打开和重连不补报历史完成。

本机状态来自当前打开的会话日志，远端还结合 tmux 当前页面。Codex / Claude 的内部日志与终端格式会变化，未识别状态会明确标注。终端活动图表示输出变化，不是 CPU 利用率或 Token 速度。详细规则见 [docs/STATUS.md](STATUS.md)。

## 开发与发布

```bash
python3 -m unittest -v
sh build_desktop.sh
python3 scripts/package_release.py
# 或 make package
```

`dist/` 生成源码 ZIP、当前架构的 macOS 应用 ZIP 与 SHA256 校验文件。发布脚本按明确文件名单打包，排除关注列表、个人设置、日志、备份与缓存。应用包自带采集脚本和固定版本的可搬移 Python 运行环境，移动到其他目录后可独立使用。当前使用本地临时签名，正式面向陌生用户分发时应由发布者完成 Developer ID 签名与公证；此项目不会自动上传 GitHub。

CI 在 Linux / macOS 运行测试，并在 macOS 构建应用。[贡献指南](CONTRIBUTING.md) · [开源参考](OPEN_SOURCE_RESEARCH.md)

MIT License，见 [LICENSE](../LICENSE)。
