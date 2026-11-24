# 颜色分析与统一接口

Agent Orbit 的桌面颜色由 [Theme.swift](../desktop/Theme.swift) 统一定义。皮肤决定视觉风格，状态角色决定信息含义；面板、会话环和提醒使用同一解析结果。本文件描述当前实现，不改变 Codex CLI / Claude Code CLI 的采集或完成判断。

## 旧问题来自哪里

| 原来的分散规则 | 可见后果 | 统一后的规则 |
| --- | --- | --- |
| `ThemePalette`、`Neon`、`NSColor.neon*` 多层别名；声浪渐变又写在 `OrbitView` | 改皮肤要查多处，颜色容易遗漏 | RGB 只在 `Theme.swift` 定义，视图请求用途明确的角色 |
| 会话环名字在 `theme` 模式直接使用 accent，卡片名字固定 text | 运行、等待和异常的名字可能同色，两个窗口含义不同 | 名字、状态文字、圆弧和标记都从 `SessionVisualState` 取色 |
| running 和 attention 共用 amber；unknown 和 offline 共用 muted | 工作中、需要回答、待核实与离线不易区分 | 六种状态分别拥有角色与色值 |
| 卡片、节点各写状态条件；来源连接有时由全局值或 `available` 代替 | 部分来源离线时可能串色；`missing` 可能被可用性条件遮住 | 先确定该来源是否连接，再调用同一 resolver；连接仍有效时明确异常优先于 `available=false` |
| 连接点变色但阴影固定 accent；未读异常汇总固定 amber | 光晕与指示点冲突，严重错误仍显黄色 | 连接点与阴影共用连接解析结果；严重错误红色，普通提醒黄色 |
| 全局调色板变化不直接触发叶子视图重建 | 没有新采样时换肤可能滞后 | 设置保存统一 `configure`，SwiftUI 身份同时包含皮肤与状态配色 |

## 颜色来源的范围

原生桌面中所有自定义 RGB、状态色和声浪渐变均收敛到本接口。透明色、系统原生控件的背景及其深浅外观仍由 AppKit/SwiftUI 绘制，控件强调色由 `tint(.accent)` 接入。设置页的六色预览通过独立 `PaletteResolver` 读取草稿，不提前改变当前运行界面。

仓库还保留两类独立输出：`monitor.py` 的 curses 终端色与 `agent_orbit_cli.py` 的 ANSI 等待提示遵循终端自身调色板和 `NO_COLOR`；`scripts/render_media.py` 的背景、标题颜色用于合成介绍素材。它们不属于桌面皮肤的运行时状态色。不要从介绍素材取色或把终端的颜色编号当作皮肤 RGB。

## 三层职责

| 类型 | 负责 | 不负责 |
| --- | --- | --- |
| `SkinDefinition` | 皮肤 ID、标题、深浅模式、12 个界面／状态 RGB token、5 个资源通道 token、默认声浪样式和线宽 | 判断 Agent 是否结束 |
| `PaletteResolver` | 不可变的皮肤与状态配色组合；返回界面 token、会话色、资源通道色与单层声浪色，便于无窗口检查 | 保存设置、控制窗口 |
| `ThemePalette` | UI 唯一入口；把 resolver 的 token 转成 AppKit `NSColor` 或 SwiftUI `Color`，提供连接、资源与声浪接口 | 在视图中重新定义状态色 |

`SkinDefinition` 构造时分别检查界面和资源角色是否齐全、色值是否为 24 位 RGB。未知皮肤回退到 `neon`；`statusStyle` 除 `status` 外归一为 `theme`。

## 12 个用途角色

| 角色 | 用途 |
| --- | --- |
| `base` | 窗口和悬浮球底色 |
| `surface` | 卡片、面板表层与背景渐变 |
| `accent` | 图标、网格、边框装饰、输入光标和控件 tint |
| `secondaryAccent` | 次级渐变与 Claude 类型徽标等装饰 |
| `text` | 普通标题与正文 |
| `muted` | 说明、预览、计数标签和基线 |
| `running` | 任务正在运行 |
| `waiting` | 已确认等待下一次输入 |
| `attention` | 需要回答、批准或处理暂停 |
| `error` | 错误、中断、停止或会话消失 |
| `unknown` | 当前状态待核实 |
| `offline` | 来源未连接或会话数据不可用 |

`accent` 是装饰角色。需要表示任务状态时，请使用相应状态角色，不能因为当前皮肤恰好让两者同色就互相替代。

## 六状态解析顺序

`SessionVisualState.resolve(state:connected:available:attentionLevel:)` 按以下顺序返回，前一条命中后不再继续：

1. `connected == false` → `offline`，不把断连前的旧状态当作当前状态。
2. `attentionLevel == "error"`，或原始状态为 `error/interrupted/stopped/missing` → `error`。
3. `available == false` → `offline`。明确的错误在上一条已保留。
4. 原始状态为 `unknown` → `unknown`。
5. `attentionLevel == "warning"`，或状态为 `approval/input/paused` → `attention`。
6. `running` → `running`；`idle` → `waiting`；其他值 → `unknown`。

调用端使用 `pane.visualState(connected: model.isConnected(pane))`。`model.isConnected` 先检查整体连接，在存在对应来源记录时读取该来源的 `connected`，没有来源记录时才回退到整体连接；`available` 继续交给 resolver 判断。不要写 `pane.available ?? model.connected`。

连接指示另由 `ThemePalette.connection(connected:state:)` 解析，因为它的输入不是 Agent 状态：

| 连接状态 | 视觉状态 |
| --- | --- |
| `config_error/monitor_error/remote_error` | `error` |
| `partial/reconnecting/delayed` | `attention` |
| `connecting` | `unknown` |
| 其他值 | 已连接为 `running`，否则为 `offline` |

`SessionVisualState.role` 一对一映射六种语义角色。多窗格与总览通过 `mostImportant` 按异常 → 待处理 → 未知 → 运行 → 等待 → 离线聚合，未知不会被同组运行窗格遮住。部分断线在总览保留注意色与“部分离线”提示；离线来源不显示实时完成横幅。卡片显示、等待/运行摘要和同组会话环节点都以解析后的状态为依据；不要用原始 `state == "idle"` 绕过更高优先级的异常。

## 当前皮肤的准确色值

以下为 `Theme.swift` 中的 sRGB token，均为不含透明度的 `#RRGGBB`。透明度、渐变与阴影由组件叠加，因此截图像素不一定与 token 完全相同。

### 皮肤状态色：`statusStyle = "theme"`

| 角色 | 霓虹深空 `neon` | 极光紫 `aurora` | 石墨黑 `graphite` | 晴空浅色 `daylight` |
| --- | --- | --- | --- | --- |
| `base` | `#060B16` | `#100B20` | `#101215` | `#EDF2FA` |
| `surface` | `#0B1421` | `#20132F` | `#1C2025` | `#FFFFFF` |
| `accent` | `#3DD9FF` | `#BD9CFF` | `#88C5D6` | `#136FBA` |
| `secondaryAccent` | `#976EFF` | `#F38AC5` | `#C0A9DA` | `#7856C7` |
| `text` | `#E0F0FF` | `#F3E8FF` | `#E4E7EC` | `#15263E` |
| `muted` | `#82A1C2` | `#B5A1D0` | `#9DA8B5` | `#52677F` |
| `running` | `#3DD9FF` | `#BD9CFF` | `#88C5D6` | `#136FBA` |
| `waiting` | `#57FFB2` | `#83E7BE` | `#9CD4B2` | `#087A4F` |
| `attention` | `#F4CD73` | `#F7CF79` | `#E2CB8B` | `#8A6600` |
| `error` | `#FF708C` | `#FF8FAE` | `#E58D9C` | `#C52B45` |
| `unknown` | `#BD9AEF` | `#CFAC91` | `#B9A1CA` | `#79559A` |
| `offline` | `#7F8EA5` | `#A29AAF` | `#8C96A3` | `#59697D` |

### 经典状态色：`statusStyle = "status"`

经典模式只替换六种状态角色；`base/surface/accent/secondaryAccent/text/muted` 继续使用上表对应皮肤的值。三套深色皮肤共用深色经典状态表，晴空浅色使用浅色经典表。

| 状态角色 | 三套深色皮肤 | 晴空浅色 |
| --- | --- | --- |
| `running` | `#FFAE67` | `#9E4B08` |
| `waiting` | `#72E2AE` | `#126D48` |
| `attention` | `#F4D66E` | `#866300` |
| `error` | `#FF8398` | `#B02D4A` |
| `unknown` | `#C4B4F5` | `#70549B` |
| `offline` | `#93A1B6` | `#586A80` |

## 多层声浪：五种信号各有颜色角色

**main 预览 · 5.2.8 / build 34。** 当前稳定包为 5.2.6。多层模式将输出与系统调用 I/O 拆为独立信号；应用内“声浪图说明…”和网页[声浪图解](WAVE_GUIDE.zh-CN.md)使用同一含义。

`ResourceRole` 定义 `output/io/tools/network/memory`。颜色来自 `SkinDefinition.resourceTokens`，经 `PaletteResolver.resourceToken/resourceColor` 和 `ThemePalette.resourceColor` 读取，不能由 `OrbitView` 另写 RGB。资源通道描述观测来源，不选择会话状态；`statusStyle` 不会把所有通道染成运行色。

| 资源角色 | 固定图形 | 霓虹深空 | 极光紫 | 石墨黑 | 晴空浅色 |
| --- | --- | --- | --- | --- | --- |
| `output` | 半透明填充光带 | `#63D9FF` | `#86D6FF` | `#8FCFE5` | `#136FBA` |
| `io` | 分段柱 | `#FF91B5` | `#FF9DA6` | `#E8A0AB` | `#B44860` |
| `tools` | 完成针 / 实心菱形；活动工具静态空心菱形 | `#FFD187` | `#F3C57D` | `#DBC084` | `#946000` |
| `network` | 细曲线 / 峰帽 | `#B99CFF` | `#CE9EFF` | `#C9AEEC` | `#7951C2` |
| `memory` | 静态细弧 | `#7CE3C0` | `#84DBC7` | `#9ACDBA` | `#24775E` |

资源色的可选 `fraction` 只向正文色混合少量亮度，不能改变通道映射。四个动态通道叠在同一条圆周基线上，共用最近 24 秒时间桶，顺时针从旧到新；RSS 不进入动态时间轴，主机总内存可读时按占比显示，否则用短虚线标记，不臆测比例。每个会话、每个通道独立使用窗口标尺，结合近期峰值与固定单位参考缩放；短过渡结束后同值同高。通道之间的高度没有占比意义，也不用于跨会话比较效率。

只有运行节点显示动态活动。等待、待处理、异常、待核实和离线立即清除历史，恢复运行先建立基线；已知零不画动态层，未知不填零，不添加随机波动。RSS 与未返回工具仅提供静态上下文，不证明任务仍在工作。工具完成包含失败结果，输出活动也不是 Token 或字节吞吐。各数据源、单位和共享范围见 [指标口径](METRICS.md#资源声浪)。

多层模式开启时，“样式”和“配色”采用固定通道图形与资源色；高度、密度和低活动灵敏度继续生效。关闭多层模式后恢复以下原有输出样式与渐变。设置页图例使用当前草稿的 resolver，换肤时不提前修改主窗口。

几何平滑与颜色解析分开：`presentedHeight` 仅对已观测正值进行最长 180 毫秒的单调过渡，零值或不可用信号直接清除；减少动态效果时不插值。独立的展示时钟按可见性、活动和实际绘制耗时选择频率，只请求重绘，不生成采集样本或重新累计用量。状态变化仍从模型即时进入视图，不能为了动画平滑而保留等待或异常前的活动。

## 单层输出声浪：保留原有样式

本节仅适用于关闭“多层资源声浪”的模式。声浪幅度来自实际页面、运行记录或已确认后台进展的活动变化。颜色样式不修改采样、完成确认或异常检测，静默不能单独证明任务结束。

`PaletteResolver.waveColor(state:palette:fraction:)` 先处理语义优先级：`attention/error/unknown/offline` 始终返回当前全局状态色，忽略装饰预设。只有 `running/waiting` 可以使用以下渐变：

| 装饰预设 | 深色起点 → 终点 | 浅色起点 → 终点 |
| --- | --- | --- |
| `ice` | `#63E5FF` → `#7994FF` | `#126EA5` → `#3558B8` |
| `aurora` | `#BD9CFF` → `#F38AC5` | `#6949B8` → `#A4377D` |
| `ember` | `#FFD187` → `#FF8B7B` | `#A65B00` → `#C13B32` |

声浪配色 `theme` 和旧值 `status` 都使用当前全局状态色，终点只向 `text` 混合 16%，保留状态区别；`fraction` 被限制在 0…1，非有限值按 0 处理。

样式 `bars/curve/dots/plasma/aurora/radar/off` 可显式选择，`theme` 或未知值使用皮肤默认值：

| 皮肤 | `theme` 默认样式 | 默认线宽 |
| --- | --- | --- |
| 霓虹深空 | `plasma` | 1.5 |
| 极光紫 | `aurora` | 1.2 |
| 石墨黑 | `radar` | 0.9 |
| 晴空浅色 | `curve` | 1.2 |

显式 `curve/plasma/aurora/radar` 的线宽分别为 1.4/1.2/1.1/1.0，其他显式样式为 1.5；使用 `theme` 时读取皮肤默认线宽。高度 50%–150%、密度 60%–160% 仅控制几何呈现。

`plasma` 绘制多层相位波与粒子，`aurora` 绘制多层光带，`radar` 将真实输出事件呈现为向外扩展的涟漪。活动强度与事件来自采集记录；无活动时不伪造持续工作动画。减少动态效果时使用静态轮廓，小号球使用简化显示。新安装的球缩放默认 85%，声浪高度默认 130%。

`bars` 为全息分段声浪柱：近 24 秒的真实输出按时间分桶，柱身保留分段间隙，亮色细芯和峰值短帽突出变化，最新输出产生短暂光尾与反射。柱宽随环上间距调整，避免高密度时糊成一圈；内圈压低高度，给会话文字留出空间。减少动态效果会冻结峰帽与光尾的计时，只有新输出或状态变化才更新。

## 组件如何取色

| 组件 | 颜色入口与角色 |
| --- | --- |
| 面板背景、卡片背景 | `ui(.base/.surface)` |
| 图标、网格、装饰线、控件 tint | `ui(.accent/.secondaryAccent)` |
| 普通标题、预览、说明 | `ui(.text/.muted)` |
| 卡片会话名、状态点、状态文字、侧边标记 | `session(visualState)` 或 `ui(visualState.role)`；使用同一状态 |
| 等待/运行摘要 | `waiting/running`，计数同样按解析后的状态过滤 |
| 未读提醒卡片与汇总 | critical → `error`；未知 → `unknown`；普通 warning → `attention` |
| 连接点及其阴影 | `connection(...).role`，点与光晕同色 |
| 会话环名字、圆弧和活动标记 | 节点的 `session(visualState)` |
| 悬浮球总览与中心轨迹 | 当前聚合状态的语义色；文字标签使用 `muted` |
| 多层资源声浪、图例与说明窗口 | `resourceColor(.output/.io/.tools/.network/.memory)`；图形与颜色同时表示通道，显示由运行状态控制 |
| 原有单层输出声浪 | `waveColor(...)`，由统一入口保护异常与可用性语义 |
| 关注列表编辑器 | `ns(.text)` 与 `ns(.accent)` 输入光标 |

组件可以调整 alpha、阴影半径、几何和线宽；不能自行增加另一套 RGB、`Neon` 颜色别名或状态到颜色的判断链。

## 调用统一接口

设置读取或保存后，在 UI 所在线程统一配置一次：

```swift
ThemePalette.configure(theme: settings.theme,
                       statusStyle: settings.orbitLabelColor)
```

SwiftUI 与 AppKit 使用同一份定义：

```swift
let visual = pane.visualState(connected: model.isConnected(pane))
let semanticColor = ThemePalette.ui(visual.role)  // SwiftUI
let nameColor = ThemePalette.session(visual)     // AppKit NSColor
let labelColor = ThemePalette.ns(.muted)
let waveColor = ThemePalette.waveColor(state: visual,
                                       palette: settings.wavePalette,
                                       fraction: 0.5)
let ioColor = ThemePalette.resourceColor(.io)  // 多层模式与图例共用
```

不依赖窗口的色值检查使用不可变 resolver：

```swift
let palette = PaletteResolver(theme: "aurora", statusStyle: "theme")
let waiting = palette.token(.waiting)
let running = palette.session(.running)
```

叶子 SwiftUI 视图读取静态入口时不会自动观察 resolver。面板保留 `.id(theme + "|" + orbitLabelColor)`，外观保存后同步更新原生会话环，确保没有新采样时也立即刷新。

## 新增皮肤

在 `Theme.swift` 中给 `SkinDefinition.all` 增加一项。可先复制一个完整皮肤再修改需要的 token，避免漏掉六种状态：

```swift
var tokens = SkinDefinition.all.first { $0.id == "neon" }!.tokens
tokens[.accent] = 0x56C8D8        // 示例新皮肤的装饰色
tokens[.running] = 0x56C8D8       // 明确指定运行角色，不依赖隐式别名
let ocean = SkinDefinition(id: "ocean", title: "海潮", light: false,
                           tokens: tokens, defaultWaveStyle: "curve")
let preview = PaletteResolver(skin: ocean, statusStyle: "theme")
```

上例是新增定义的写法；正式皮肤还应通过 `resourceTokens` 显式定义五种资源角色，确保光带、分段柱、工具标志、网络曲线和 RSS 弧可分辨。将实际定义加入 `SkinDefinition.all` 后，设置中的 ID/标题列表自动派生；`configuration.py` 的主题 ID 白名单也需加入同一 ID。检查六种状态彼此可分辨、正文和状态文字可读，再运行主题检查与应用构建。新增皮肤无需修改 `PanelView` 或 `OrbitView` 的颜色分支。

## 设置兼容

JSON 仍使用旧键，已有配置无需迁移字段名：

```json
{
  "theme": "neon",
  "orbit_label_color": "theme"
}
```

| JSON 值 | 界面名称 | 当前含义 |
| --- | --- | --- |
| `orbit_label_color: "theme"` | 皮肤状态色 | 全局使用所选皮肤的六状态 token |
| `orbit_label_color: "status"` | 经典状态色 | 全局使用深色或浅色经典六状态 token |

Swift 设置属性继续叫 `orbitLabelColor`，传入 `ThemePalette.configure` 时对应 `statusStyle`。它已不是“只修改会话名”的开关，`theme` 也不再把所有名字强制染成 accent。`wave_resources: true` 开启多层通道；`wave_palette` 是关闭多层后单层输出的独立装饰选项，不能覆盖高优先级语义状态，也不改写资源通道色。

## 检查

在 macOS 项目根目录运行：

```bash
sh scripts/check_theme.sh
```

主题检查之外，仍需运行采集逻辑测试和应用构建：

```bash
python3 -m unittest
sh build_desktop.sh
```

颜色 token、状态优先级与组件调用应一起检查。不要以看起来相近的单张截图替代角色一致性检查；半透明图层和系统渲染本来就会改变最终像素。修改主题接口时，请同步更新本页中的色表和调用示例。
