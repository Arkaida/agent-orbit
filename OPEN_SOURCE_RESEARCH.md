# 开源方案比较

核查日期：2026-11-15。依据 GitHub README、相关源代码与集成文档，未运行第三方安装脚本。

| 项目 | 适合参考的能力 | 与当前远端面板的差别 |
| --- | --- | --- |
| [Agent Manager X](https://github.com/maddada/agent-manager-x) | Swift 原生置顶 NSPanel、小窗、完成提示、终端跳转 | 现有实现主要读取本机进程与 Codex 记录，不能直接识别当前 Linux 远端 tmux 会话 |
| [agentoast](https://github.com/shuntaka9576/agentoast) | Codex `agent-turn-complete`、tmux 定位、持久完成提示 | 需要配置 Codex notify；已有远端任务的跨机传递仍需适配 |
| [Agent Notifications](https://github.com/777genius/agent-notifications) | Codex Stop / PermissionRequest hooks、多系统提醒 | hooks 需要配置及信任；主要提供通知，不是持续展示关注列表的小面板 |
| [ftop](https://github.com/Nongfsq/ftop) | 轻量原生桌面悬浮监控、菜单栏、置顶切换 | 数据源是本机 CPU/GPU/进程，不能直接监控 Codex 生命周期 |
| [Muxflow](https://github.com/gal064/muxflow) | SSH 远端 tmux、Codex 生命周期与桌面提醒 | 完整远端 IDE，要求远端 helper；README 提醒新 Codex 共享 daemon 会影响旧窗格状态归属，需要新建会话 |

## 采用的方案

在现有项目中增加独立的 AppKit NSPanel，保留持续 SSH 采样与原关注列表。这样可接入已经运行的会话，无需更换终端或重启 Codex。按确认后的 running→idle 变化提醒，启动时 idle、断连、审批、未知、历史画面不产生完成提醒。

状态依据终端画面，并非官方事件订阅；需要更精确的原生完成事件时，应对远端 Codex 配置正式 Stop/notify hooks。不能把 `item_completed` 当作整个回合完成，因为它也会在工具或消息项完成时出现。

## 实现依据

- [Agent Manager X MiniViewer.swift](https://github.com/maddada/agent-manager-x/blob/main/App/Resources/native-mini-viewer/MiniViewer.swift#L1090)：`.floating`、`hidesOnDeactivate = false`、跨桌面面板。
- [agentoast README](https://github.com/shuntaka9576/agentoast#integration)：Codex notify 和 `agent-turn-complete`。
- [Agent Notifications Codex 文档](https://github.com/777genius/agent-notifications/blob/main/docs/CODEX.md)：Stop、信任、权限事件及限制。
- [Muxflow README](https://github.com/gal064/muxflow#troubleshooting)：远端支持与 Codex 共享 daemon 的归属限制。

本项目未复制这些仓库的实现代码，未安装其运行时或修改远端配置。AppKit 和 SwiftUI 是 macOS 系统框架。
