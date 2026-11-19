# Status and data sources

## Remote Codex and Claude

One SSH stream samples existing tmux panes. Process state, current terminal
UI and precisely associated root logs provide the evidence. Errors and
approvals take precedence over input readiness. Claude completion also
checks the foreground lifecycle and explicit child/workflow activity.

After observed work, an input-ready page must remain stable for two seconds;
new prose resets confirmation. The completion notifier adds a one-second
debounce. An active spinner, pending main request or verified executing child
does not complete merely because output pauses. Initial idle states are quiet.

Open terminal handles, persistent shells, and online child-agent totals are
resource counts. They do not by themselves mean the main task is working.
Codex's `7 background terminals running` and Claude's `done · 1 shell still
running` are compatible with waiting for the next user input.

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

The floating ring's waveform uses recent output or log changes. Its style,
palette, height and density change only the presentation; they do not change
sampling, completion detection or error alerts. A quiet waveform is not proof
that a task has completed, and its height is not CPU usage, token throughput
or reasoning efficiency. Turning it off leaves session monitoring active.

Names, status text, arcs and markers share one resolved visual state and color.
The default skin palette defines separate running, waiting, attention, error,
unknown and offline roles. The classic palette uses orange, green, yellow, red,
purple and gray respectively, with shades adapted to light and dark surfaces.
Waveform presets may decorate running and waiting; attention, error, unknown
and offline states always retain their semantic color.

The existing `orbit_label_color` setting now selects the global status palette:
`theme` means skin status colors and `status` means classic status colors.
It no longer makes every session name the same accent color. See
[color analysis and the shared API](THEMING.md) for resolver precedence,
component roles and exact RGB values.
