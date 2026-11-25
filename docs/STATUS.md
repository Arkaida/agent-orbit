# Status and data sources

## Remote Codex and Claude

One SSH stream samples existing tmux panes. Process state, current terminal
UI and precisely associated root logs provide the evidence. Errors and
approvals take precedence over input readiness. Claude completion also
checks the foreground lifecycle and explicit child/workflow activity.

Codex reconnects recover the latest recognized task boundary from a bounded
tail of the precisely associated root log. Historical error records are not
replayed as new alerts. Log replacement, truncation and skipped large updates
discard the previous lifecycle before recovery; an unread boundary never
inherits an older start or completion. Live interrupt footers also recognize
additional shortcut hints and long input areas, while quoted prose and draft
input do not establish running state. A visible input box alone does not end
an active, logged task.
Timer footers also accept known status items outside the closing bracket,
including background terminal counts and `/ps` or `/stop` hints. Only the last
known item may be clipped; arbitrary prose does not become a status suffix.
Current UI evidence remains useful when a long-running turn's start precedes
the bounded log tail.

Claude foreground state is reduced from ordered root-log lifecycle records.
A bounded tail read sees recent records independently of the slower background
history reconstruction; older file offsets cannot overwrite a newer completion.
`turn_duration`, or a non-empty text answer with `stop_reason=end_turn`, closes
the foreground turn. A thinking-only `end_turn` fragment does not. A later
main request or real user input starts the next turn. Verified completion can
correct a stale terminal spinner; pending background reconstruction remains
unconfirmed, and a verified active child still keeps the session running.

Claude asynchronous Agent/Task results may declare `isAsync=true` or
`status=async_launched` even when the original tool input omits
`run_in_background`. These launches remain active after the foreground answer
and are cleared by their matching terminal notification or stop result. Online
agent totals and persistent shell handles still do not imply active work.
The current input area's `* Waiting for N background agent(s) to finish`
status also keeps unfinished children running, including while log history is
being checked. Quoted text, old prompts and scrollback do not count as this
live status. Matching UI and log counts use the maximum rather than being added.

After observed work, an input-ready page must remain stable for two seconds;
new prose resets confirmation. The completion notifier adds a one-second
debounce. An active spinner, pending main request or verified executing child
does not complete merely because output pauses. Initial idle states are quiet.

Open terminal handles, persistent shells, and online child-agent totals are
resource counts. They do not by themselves mean the main task is working.
Codex's `7 background terminals running` and Claude's `done · 1 shell still
running` are compatible with waiting for the next user input.

## Confirmed background activity

Foreground completion and background job completion are separate. Explicit
Claude background tasks can continue after the root answer. Continuous growth
of a bound task output file, or fresh CPU/GPU/disk activity of its precisely
associated process branch, keeps it working with a background status. File
identity and process start identities prevent old history or PID reuse from
becoming progress. Repeated cached samples do not renew activity evidence.
`foreground_input_ready` reports foreground readiness separately.

If previously confirmed process activity becomes unobservable, background
state stays unconfirmed rather than reporting completion or replaying old
waves. An initial missing resource sample and an ordinary retained terminal
do not by themselves create this state.

Task completion/stop notifications clear the matching job. Retained handles,
host CPU load, idle GPU memory and unassociated processes do not establish job
activity. Explicit Workflow/persistent task schemas are recognized separately
from Agent counts. NVIDIA-discovered detached processes can also associate through an exact
CLAUDE_CODE_SESSION_ID match to a confirmed root log, the same user and a
verified process start identity. Their presence or GPU memory alone is not
activity; fresh GPU or continuous CPU execution is required. Missing provenance,
permissions or cross-machine experiments remain coverage limitations.

## Local Codex

The local collector maps Codex processes to their open rollout files using
`ps` and `lsof`, then validates root session metadata under the configured
Codex home. Desktop, VS Code and CLI root sessions are supported when they
expose these records. Child and guardian sessions do not become extra root
cards; owned child lifecycle records provide child counts.

SQLite is opened with `mode=ro` only to resolve titles. Started/completed/
aborted/error events determine state; logs without a recognizable lifecycle
show an unconfirmed state. Neither modification time nor silence is treated
as completion. Local background-terminal ownership is not inferred from a
shared app-server process, so that count is omitted. Local entry opens the
installed Codex client; it does not inject input or automatically resume a chat.

## Availability and privacy

Local and remote sources have independent connections and row availability.
A failed SSH source cannot gray out a healthy local source. Delayed or
disconnected data cannot produce a new completion event. Sampling defaults to
0.5 seconds; actual latency includes collection and transport.

Collectors read local files / tmux output. There is no telemetry, HTTP listener,
cloud account, hook installation or third-party Python dependency. Local Codex
formats are not a stable public API; unsupported records remain unconfirmed.
The diagnostic log contains connection metadata, not conversation bodies.

## Activity visualization

In 5.3.3, the floating ring adds dialogue output to CPU, disk, GPU and TCP
network observations on one circular baseline and a 24-second timeline.
CPU uses measured intervals; GPU uses real per-process SM sample points,
without filling the time between queries. Disk uses live-thread read_bytes /
write_bytes, rather than terminal or syscall character counts. GPU memory
allocation and host totals never substitute for observed process activity.
Dialogue output counts observed new assistant text independently of hardware coverage. Input drafts,
spinners, timers, log metadata, tool event counts and Token estimates do not generate output
peaks. One selected wave skin defines the same geometry for all five channels; colors distinguish their sources. Changing the skin changes every channel together, without changing measurements. The legacy mixed output_total does not drive this layer; RSS remains in tooltips.

Only working members contribute. Waiting, attention, error, unknown and
offline nodes clear history; resuming establishes a baseline. Channels scale
independently, and missing coverage remains unknown. Drawing and collection
have separate clocks. Turning activity waves off leaves monitoring active.
While a source and session remain live and running, a temporary measurement
gap retains already observed history until its original 24-second expiry.
It adds no activity; current coverage remains unknown. Recovery establishes a
new counter baseline, and observed stream/epoch changes discard the old trace.
See the [waveform guide](WAVE_GUIDE.zh-CN.md) for units and platform coverage.

## Process sampling

Each collection uses one immutable process snapshot and parent/child index. Resource sampling remains bounded and asynchronous, with process-instance baselines and explicit shared or unconfirmed attribution. CPU is measured from process CPU time; Linux I/O uses live thread counters to avoid recounting waited-for children. Resource activity does not reopen a completed foreground turn; precisely bound background task activity is evaluated separately. Silence never proves completion. Short-lived work between samples may be missed; no eBPF component is installed. See [process collection](PROCESS_COLLECTION.zh-CN.md).
