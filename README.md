<div align="center">

<img src="docs/media/icon.png" width="88" alt="Agent Orbit icon">

# Agent Orbit

**Know when your agents need you.**

本机与远端 CLI Agent 的桌面会话环 · A native macOS monitor for Codex CLI & Claude Code CLI

[![macOS 13+](https://img.shields.io/badge/macOS-13%2B-101a2c?logo=apple&logoColor=white)](https://github.com/Arkaida/agent-orbit/releases)
[![MIT](https://img.shields.io/badge/license-MIT-38d9ff)](LICENSE)
[![Checks](https://github.com/Arkaida/agent-orbit/actions/workflows/ci.yml/badge.svg)](https://github.com/Arkaida/agent-orbit/actions/workflows/ci.yml)

[**下载应用 / Download**](https://github.com/Arkaida/agent-orbit/releases/latest) · [中文使用指南](docs/USAGE.zh-CN.md) · [状态如何判断](docs/STATUS.md)

<img src="docs/media/hero.png" width="100%" alt="Agent Orbit: compact native monitor and floating session ring, shown with fictional demo data">

</div>

不必反复切回终端，看看 CLI Agent 做完了没有。**Agent Orbit 专注监控 Codex CLI 与 Claude Code CLI 会话**，常驻桌面，在任务等待输入、需要确认或遇到异常时，让你及时看到。

Monitor **Codex CLI and Claude Code CLI sessions** from a compact always-on-top window or floating ring. Supports local Codex CLI and remote Codex CLI / Claude Code CLI over SSH and tmux. See running, waiting and error states at a glance, with optional sound alerts.

> **独立应用。** 下载后双击 `.app` 即可运行，无需安装 Python、Node.js 或 Codex 插件，也不需要在 Codex 中启动。它读取你已有的会话，不启动或接管 Agent。

## 看看它如何工作

[![Watch the privacy-safe demo](docs/media/demo.gif)](docs/media/agent-orbit-demo.mp4)

[观看 / 下载介绍视频 MP4](docs/media/agent-orbit-demo.mp4) · 视频使用应用原生界面与虚构会话数据，未录入真实桌面、主机或对话。

| 在桌面上看见 | 它代表什么 |
| --- | --- |
| 🟢 等待输入 | 主任务已确认结束，可以继续下一轮 |
| 🟠 运行中 | 正在思考、执行工具或有实际执行的子任务 |
| 🔴 异常 | 服务错误、中断、会话消失等，支持一次提示音 |
| 会话环外的活动轨迹 | 最近输出或日志变化，**不是 CPU 或推理效率** |

## 你可以做什么

- **本机 + 远端 CLI 监控**：自动发现本机 Codex CLI；通过 SSH 读取远端 tmux 中的 Codex CLI / Claude Code CLI。连接状态彼此独立。
- **关注重要会话**：分别配置本机和远端关注列表；关闭设置后继续监控。
- **收起成会话环**：点击展开，拖动移动；会话名沿边缘排列，多会话自动双圈。
- **完成与异常提醒**：分别控制声音，恢复后自动移除旧异常，不展示执行历史。
- **换一种外观**：四套皮肤、置顶、透明度、悬浮球尺寸，以及摘要、子任务数量的显示开关。
- **保留原工作方式**：远端卡片进入原 tmux 会话，本机卡片唤起 Codex 客户端。

<img src="docs/media/themes.png" width="100%" alt="Neon, Aurora, Graphite and Daylight themes">

## 安装

1. 在 [Releases](https://github.com/Arkaida/agent-orbit/releases/latest) 下载应用 ZIP。当前提供 Apple Silicon 的 `arm64` 安装包；Intel Mac 可按下方开发说明从源码构建。
2. 解压，将 **Agent Orbit.app** 放到“应用程序”或任意常用目录，双击启动。
3. 点右上角齿轮打开设置。默认监控本机；远端模式填写已有 SSH 别名与 tmux Socket，选择关注会话。

应用支持 **macOS 13+**，运行环境已内置。远端模式需要可无交互登录的 SSH，以及远端已有的 Python 3、tmux 和 Agent 会话。

当前社区构建使用本地签名，尚未进行 Apple 公证。macOS 可能提示无法验证开发者；请仅在确认下载来源和校验值后，按系统提供的“隐私与安全性 → 仍要打开”流程操作。不要关闭系统安全保护。

## 可选：终端命令

源码目录中提供 `agent-orbit` 入口，会优先使用应用自带的运行环境。将下面一行加入 `~/.zshrc`，并替换为自己的项目位置：

```zsh
source '/path/to/agent-orbit/shortcuts.zsh'
```

```bash
ao p                # 打开桌面应用
ao                  # 持续监控远端；未启用远端时监控本机
ao local            # 持续监控本机 Codex
ao + atlas nova     # 关注远端会话
ao atlas            # 进入原远端 tmux 会话
```

旧的 `cr`、`codex-remote` 命令继续兼容。退出 tmux 连接按 **Ctrl+B**，松开后按 **D**，任务继续运行。

## 隐私与判断边界

- 只读采集，无账户注册、遥测、云端中转或对外 HTTP 服务。远端通过已有 SSH 连接读取。
- 设置与关注列表留在本机用户目录，源码和发布包不含个人配置。日志仅记录连接诊断，不保存对话正文。
- 保留终端的数量不会直接阻止主对话显示等待；无法可靠归属的本机后台终端数量会省略。
- Codex / Claude 的日志格式会变化；无法确认的状态明确显示待核实，不会把长时间静默直接判为完成。

详见 [状态规则](docs/STATUS.md)、[使用指南](docs/USAGE.zh-CN.md) 和 [第三方许可](THIRD_PARTY_NOTICES.md)。

## 开发与贡献

使用 macOS 的 Xcode Command Line Tools 与 Python 3.9+ 构建：

```bash
python3 -m unittest
sh build_desktop.sh
python3 scripts/package_release.py
```

首次构建下载固定版本、经过 SHA-256 校验的可搬移 Python 运行环境；安装后的应用不需要外部 Python。`dist/` 生成源码包、当前架构应用包与校验文件。CI 在 Linux / macOS 验证采集逻辑，在 macOS 构建应用。

不连接真实会话的演示模式：

```bash
open 'Agent Orbit.app' --args --demo
```

欢迎提交复现信息、状态识别样例、无个人内容的截图或改进建议。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

**MIT License** · Independently built; not affiliated with OpenAI or Anthropic.
