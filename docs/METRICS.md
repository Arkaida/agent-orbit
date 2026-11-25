# 用量总览

面板将状态、用量和输出活动分开显示。用量来自 CLI 已写入的日志记录，不根据声浪、日志文件大小或终端字符数推算 Token。

默认逐会话显示近 30 秒新增 Token、精确累计、输出和工具速度，方便判断当前推进情况。悬浮球中心始终保留新增量，不会被等待提醒替换；空闲／运行累计以名称旁的细双色比例弧显示，具体时长放在悬停提示中。等待时长和久等提醒见 [及时回到会话](RESPONSE.md)。全局用量总览可以展开查看。

| 指标 | 单位与含义 |
| --- | --- |
| 总运行率 | 在线且状态已知的关注会话中，正在运行的比例。相同会话的多个窗格合并计数；离线、缺失和待核实会话不作为闲置加入分母。不是 CPU/GPU 利用率或账号额度。 |
| 实时消耗 | 最近 30 秒新报告的输入与输出 Token，除以 30，单位 `tok/s`。 |
| 近 30 秒新增 | 最近 30 秒实际采集到的输入与输出 Token 整数增量，单位 `tok`。随新用量记录刷新，不从速率四舍五入反推。 |
| 总消耗 | 当前关注主会话日志中已完整核实的累计输入与输出 Token，单位 `tok`。不是金额，也不是只统计本次打开应用以后的消耗。 |
| 上游速度 | 最近 30 秒新报告的输出 Token，除以 30，单位 `tok/s`。表示日志结算的输出速度。 |
| 工作速度 | 最近 60 秒完成的工具调用数量，单位 `次/min`。工具返回结果即计一次，包含错误结果；不代表任务成功数。 |

刚开始监控时，速度与新增量先建立基线，不把接入前的历史消耗计为新增；不足完整窗口时速度仍使用上述固定分母。响应的用量可能延迟落盘，尤其是 Claude，所以速度会呈现结算波动，不能据此测量逐 Token 的网络传输或严格的模型生成速度。未报告的用量不会被估算成 0。面板中的累计显示完整整数；悬浮球空间不足时才缩写累计，可悬停查看精确值。新增量仍为 30 秒内的整数和，窗口内没有新增时显示 0，断线或等待连续基准时显示 `—`。

## 累计来源与覆盖

- Codex：使用最新 `token_count.info.total_token_usage` 累计快照，不将多条累计快照相加。
- Claude：合并相同 `message.id` 的 `message.usage`，避免流式重复记录造成多次计费式的累加。输入包括普通输入、缓存读取和缓存创建；缓存创建的 5m/1h 明细不再重复加入。
- 统一输出满足 `总 Token = 输入 Token + 输出 Token`。缓存读取、缓存写入属于输入的子集，推理 Token 属于输出的子集，都不会再次叠加。
- 仅统计当前关注的主会话；不再叠加子智能体日志，避免把主记录已包含的用量重复计入。未写入主日志的后台用量不在覆盖范围内。
- 同一来源、同一日志对应的多个窗格只计一次用量。总消耗随关注列表变化，因此可能减少；它不是跨全部历史会话的永久账本。

首次读取会分批核对已有日志，避免大文件阻塞状态监控。Codex 可优先读取尾部最新的权威累计快照，工具次数仍需核对历史；Claude 需要合并主日志中的各次回复。未核实、字段缺失或无法读取时显示未覆盖；总消耗仅合计已核实的部分。查看覆盖数，不能把部分统计当成所有会话的完整消耗。日志删除、轮换或截断后会重新核对，不保证被删除历史仍可恢复。

## 断线和重连

断线来源保留上次累计值，实时速度停止显示。部分来源在线时，只显示在线且已覆盖部分的速度。新连接、关注列表变化、日志身份变化或计数重置后重新建立基线，不把离线期间补到的用量当成瞬时高峰。界面重复刷新不会重复记账。

## 活动声浪

**5.3.3 / build 39。** 对话输出与 CPU、磁盘、GPU、网络共五层。输出层独立于硬件，按新增正文字符观测绘制。[完整图解](WAVE_GUIDE.zh-CN.md)

| 通道 | 默认颜色 | 来源与单位 | 边界 |
| --- | --- | --- | --- |
| 对话输出 `output` | 薄荷 | 新增非空白助手正文字符；观测字符／秒 | 不是Token、日志字节或工具事件数；终端／日志均有覆盖边界 |
| CPU `cpu` | 青蓝 | 进程执行时间差；百分比 | `100%` 为一个逻辑核，可超过 `100%` |
| 磁盘 `disk` | 玫红 | 活跃线程 `read_bytes/write_bytes` 连续增量；`B/s` | 观测下界，缓存命中与管道不等同读盘 |
| GPU `gpu` | 琥珀 | NVIDIA 按进程提供的 SM 利用率；百分比 | 逐进程／设备采样点之和，可超过100%；不是去重后的设备总利用率或显存占比 |
| 网络 `network` | 紫色 | 可关联 TCP 收发增量；`B/s` | 不包含 UDP 或网卡总量，共享与缺失保留边界 |

五层使用同一种声浪皮肤，以颜色区分，叠在同一圆周和24秒时间轴，每个会话、每层独立缩放，悬停查看实际值。CPU区间采样按覆盖时间写入视觉桶，GPU按实际采样点绘制，不把查询周期当观测窗口；缓存样本不重复累计。高度不能比较不同通道的占比或任务效率。

仅工作成员贡献动态波形，等待、异常、待核实和离线立即清除历史；恢复运行先建立基线。未知不补零，低活动灵敏度不制造活动。RSS 只保留悬停信息。旧 `output_total`、工具事件数和 `io_read/write_*` 仍可用于兼容数据消费者，但不生成新对话输出层或硬件声浪。

资源在后台有界采样，CPU、磁盘和网络约一秒更新，GPU 单独约三秒批量查询，过期样本保持未知。macOS 当前支持 CPU/RSS；Linux 磁盘、网络和GPU视权限、驱动与字段覆盖提供。Token 仍来自 CLI 用量记录。

前台完成不能替后台实验完成：已记录任务的连续输出、与其输出文件精确关联的进程分支活动，或经过精确会话标识核验的脱离实验进程活动，可以补充后台工作状态。整个 Claude 宿主的心跳、在线终端数量或GPU显存不足以触发此判定。详情见 [状态识别](STATUS.md)。

采样、状态投递与绘图独立调度，短视觉过渡只平滑已观测值，不外推流量或延迟完成提醒。

## 接口

`desktop_bridge.py` 的逐行 JSON 流新增顶层 `metrics`。它提供上述汇总、各项覆盖数和窗口长度；原始会话行中的 `telemetry` 提供独立的主会话累计计数。无数据时数值为 `null`，不要将其转换为 0。`output_total`、`output_rate` 和 `activity_level` 保留兼容，不再驱动硬件声浪，也不能当作 Token。

| `metrics` 字段 | 说明 |
| --- | --- |
| `utilization_percent` | 0–100 的总运行率；无已知在线状态时为 `null` |
| `running_sessions` / `known_sessions` / `monitored_sessions` | 运行会话数、已确认在线状态数、关注会话总数，按会话分组 |
| `total_tokens` / `input_tokens` / `output_tokens` | 关注主日志中已核实的累计 Token |
| `cached_input_tokens` / `cache_write_input_tokens` | 输入中的缓存子项；来源不提供时为 `null` |
| `consumption_per_second` | 输入加输出 Token 的窗口结算速度 |
| `window_tokens` / `window_output_tokens` | 当前 Token 窗口内已确认的消耗／输出整数增量；可用部分与 `live_usage_covered` 一致，无连续基准时为 `null` |
| `upstream_tokens_per_second` | 输出 Token 的窗口结算速度 |
| `tools_per_minute` | 工具结果的窗口完成速度 |
| `usage_sessions` | 用量覆盖分母：已去重的主日志与尚无日志身份的关注会话槽位 |
| `usage_covered` / `tools_covered` | 分别已有完整核对累计值的槽位数，可含断线来源的上次确认值 |
| `live_usage_covered` / `live_tools_covered` | 分别已建立连续速率基线的在线槽位数 |
| `window_seconds` / `work_window_seconds` | Token 与工具速率的窗口长度 |
| `scope` | 固定为 `followed_root_sessions` |

覆盖分母不会省略无法统计的关注会话，状态会话数和主日志数也可能不同。消费者应同时展示覆盖数与速率单位。

每行 `rows[].usage_metrics` 使用相同的 Token、速率、窗口与覆盖字段，`scope` 为 `root_session`，只统计该行已确认的主日志。别把全部会话的汇总当作某一会话的工作速度。

每行 `rows[].dialogue_output` 独立提供 `epoch`、`sample_id`、`total_chars`、`available`、`complete`、`sample_age_seconds` 和 `source`。`total_chars` 是接入后连续观测确认的新增非空白助手正文字符累计，不是日志大小、Token计数或完整生命周期输出。首次、缺口、来源／身份变化与恢复运行先建立基线；同一观测流、同一采样不重复计入。`source` 说明来自终端正文或本机助手日志；未识别布局及覆盖不完整需保留边界。

`rows[].resources` 提供 `identity`、`sample_id`、`sample_age_seconds`、进程树 RSS、CPU、GPU、活跃线程磁盘 / 系统调用 I/O 与 TCP 字节和速率，以及各项 `*_available`、`*_complete`、`*_reason` 和共享范围。离线或过期数据不可当作当前活动。`usage_metrics` 另提供 `tool_completions`、`active_tools`、`tools_epoch`；这些字段继续描述工具用量，不生成硬件声浪。

进程树 CPU 使用 `cpu_percent`，单逻辑核为 `100%`，多核可超过 `100%`；`cpu_seconds` 只累计连续样本已确认的增量，不是进程全生命周期 CPU。`cpu_available` / `cpu_complete` / `cpu_covered` / `cpu_reason` 表示可用性和覆盖。`attribution` 区分 `exclusive`、`shared` 与 `unattributed`；`attribution_source` 说明来自 tmux 进程树或打开的会话日志。共享宿主的数值不自动分摊给其中某个对话。

I/O 接口字段名沿用，5.2.9 的 `io_scope=observed_process_tree_threads`、`io_kind=thread_syscall_bytes` 明确口径变化；`io_lower_bound=true` 表示已观测下界。旧版 root I/O 与新口径不能跨 epoch 拼成同一账本。详细限制见 [进程采集说明](PROCESS_COLLECTION.zh-CN.md)。

若其他目标树的 FD 读取不完整，当前 socket 的独占归属无法证实，则 `network_attribution_unknown=true`、`network_reason=ownership_unknown`，相应流量保持未知并重新建立基线；不能将预算不足当作独占流量的证据。

这套指标不需要 API 密钥，不查询供应商计费服务，不计算价格、剩余额度、机器性能或任务质量。

GPU 的 `gpu_utilization_percent`、`gpu_available`、`gpu_complete`、`gpu_reason` 与独立的 `gpu_sample_id` / `gpu_sample_age_seconds` 描述真实逐进程 SM 观测。`disk_read/write_per_second` 用于磁盘声浪，不能替换成 `io_read/write_per_second`。`process_activity` 提供已核验进程身份、父关系、分进程指标和输出文件的设备／inode 标识，用于精确后台任务关联，不包含命令正文或文件路径。

脱离原树的NVIDIA实验通过精确Claude会话标识、同一用户与进程身份核验后，资源范围改为 `session_processes`；逐进程 `association=claude_session_env` 标注关联来源。`background_active_session_processes` 计活动进程，不能解释为实验或智能体数量。`foreground_input_ready` 与后台运行状态分别报告。
