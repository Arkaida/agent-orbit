#!/usr/bin/env python3
"""Read-only tmux snapshots; --stream keeps one SSH process alive."""
import hashlib
import json
import os
import pwd
import re
import select
import subprocess
import sys
import time
from process_snapshot import ProcessSnapshot, as_snapshot
from resource_probe import AsyncProcessResources

PROMPT = re.compile(r'^\s*[›»]\s*(.*)$')
# Codex appends live terminal counts and shortcuts outside the timer brackets.
# Only known UI items (or the final item's clipped prefix) may follow them;
# arbitrary quoted prose after an interrupt example is not a live footer.
_CODEX_STATUS_HINTS = ('/ps to view', '/stop to close', 'ctrl+b to run in background')
_CODEX_BACKGROUND_HINTS = ('background terminal running', 'background terminals running')
_CODEX_STATUS_ITEM = r'(?:\d+\s+background terminals?(?: running)?|' + '|'.join(re.escape(x) for x in _CODEX_STATUS_HINTS) + r')'
_CODEX_STATUS_PREFIX = (r'(?:\d+\s+(?:' + '|'.join(sorted({re.escape(x[:n]) for x in _CODEX_BACKGROUND_HINTS for n in range(3, len(x) + 1)}, key=len, reverse=True)) + r')|'
                        + '|'.join(sorted({re.escape(x[:n]) for x in _CODEX_STATUS_HINTS for n in range(3, len(x) + 1)}, key=len, reverse=True)) + r')')
RUNNING = re.compile(r'^\s*(?:[•●◦]\s*)?(?:Working|Thinking|Running|Compacting(?: context)?)\s*'
                     + r'\(\s*(?:\d+(?:\.\d+)?(?:ms|[hms])\s*)+[·•]\s*esc to interrupt\b[^)\n]*\)'
                     + r'(?:\s*[·•]\s*' + _CODEX_STATUS_ITEM + r')*'
                     + r'(?:\s*[·•]\s*' + _CODEX_STATUS_PREFIX + r'(?:…|\.{3})?)?\s*$', re.I)
RECOVERING = re.compile(r'^\s*[•●◦]\s*(?:Reconnecting|Retrying|重新连接|重试)(?:\.{1,3})?(?:\s|$)', re.I)
FOOTER = re.compile(r'\bGPT[- ]|\bMain \[|\? for shortcuts|Goal (?:paused|stalled)|warnings? · f2', re.I)
CODEX_FOOTER = re.compile(r'^\s*(?:\? for shortcuts\s*|GPT[- ][^\n]*[·•][^\n]*|[^\n]*\bMain \[[^\]]+\][^\n]*)$', re.I)
EMPTY_ISSUE = dict(error_summary='', error_signature='', error_kind='', attention_level='')
CLAUDE_PROMPT = re.compile(r'^\s*❯\s*(.*)$')
CLAUDE_WORKING = re.compile(r'^\s*(?:[✢✳✶✻✽✱⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s+.*(?:…|\.\.\.|esc to interrupt|ctrl\+c to interrupt)|[^\w\s]\s+.*(?:…|\.\.\.).*\([^)]*(?:\d+(?:\.\d+)?(?:h|m|s)\b|\d+(?:\.\d+)?k?\s+tokens\b|(?:esc|ctrl\+c) to interrupt))', re.I)
SAFETY_BANNER = re.compile(r'^\s*(?:[ⓘℹ⒤i]\s+)?This content can[’\x27]t be shown\s*$', re.I)


def issue(summary, kind='fatal', level='error'):
    return dict(error_summary=summary, error_signature=hashlib.sha256((kind + ':' + summary).encode()).hexdigest(),
                error_kind=kind, attention_level=level)


def safe_summary(text):
    text = re.sub(r'https?://\S+|request[_ -]?id\s*[:=]\s*\S+', '', text, flags=re.I)
    text = re.sub(r'\b(?:sk-|Bearer\s+)\S+', '[已隐藏]', text, flags=re.I)
    return re.sub(r'\s+', ' ', text).strip(' ,;')[:200]


def detect_failure(lines, running, scrolled):
    empty = dict(EMPTY_ISSUE)
    if scrolled:
        return empty
    if running:
        body = [line for line in lines if line.strip()]
        active = max((i for i, line in enumerate(body) if RUNNING.match(line) or RECOVERING.match(line)), default=-1)
        tail = body[active + 1:] if active >= 0 else []
        retry = re.search(r'Reconnecting|Retrying|重试|重新连接', running, re.I)
        if not retry:
            return detect_failure(tail, '', False) if tail else empty
        else:
            detail = detect_failure(tail, '', False) if tail else empty
            code = re.search(r'\b([45]\d\d)\b', running)
            summary = '正在重连 / 重试'
            if detail['error_summary']:
                summary += ' · ' + detail['error_summary']
            elif code:
                summary += ' · HTTP ' + code.group(1)
            return issue(summary, 'retrying', 'error' if detail['error_summary'] or code else 'warning')
        return empty
    # A top-level failure block at the end of the transcript is distinct from
    # an exception in ordinary command output, or prose quoting an HTTP code.
    body = [line.rstrip() for line in lines if line.strip()]
    marker = next((i for i in range(len(body) - 1, max(-1, len(body) - 60), -1)
                   if SAFETY_BANNER.match(body[i]) or re.match(r'^(?:\s*■(?:\s|$)|(?:API\s+)?Error\s*:|Conversation interrupted\b|Turn (?:aborted|cancelled|canceled)\b|You.ve hit your usage limit\b)', body[i], re.I)), None)
    if marker is None:
        return empty
    # A subsequent user turn, assistant response or completed-work separator
    # makes an earlier failure historical, even if it is still on screen.
    if any(re.match(r'^\s*(?:[•●◦›»]\s|[─━]+\s*Worked for|Worked for\s)', line, re.I)
           for line in body[marker + 1:]):
        return empty
    block = ' '.join(line.strip() for line in body[marker:])
    if re.search(r'conversation interrupted|turn (?:aborted|cancelled|canceled)|interrupted by|cancelled by|用户中断|已取消|操作已中断', block, re.I):
        return issue('本轮已中断，需要重新输入或继续', 'interrupted')
    if SAFETY_BANNER.match(body[marker]) and re.search(r'cybersecurity requests|apply for Daybreak|extra care', block, re.I):
        return issue('内容被安全策略拦截（Cybersecurity / Daybreak）', 'safety')
    if re.search(r'cyber[\s_-]?(?:scure|secure|safety|security)|safety (?:reasons|policy|check)|blocked.{0,30}(?:safety|security)|potentially harmful', block, re.I):
        summary = '安全策略拦截（Cyber safety）'
    elif re.search(r'\b502\b|bad gateway', block, re.I):
        summary = '502 网关错误'
    elif re.search(r'\b503\b|service unavailable', block, re.I):
        summary = '503 服务暂不可用'
    elif re.search(r'\b504\b|gateway time.?out', block, re.I):
        summary = '504 网关超时'
    elif re.search(r'\b401\b|unauthorized|authentication', block, re.I):
        summary = '401 身份验证失败'
    elif re.search(r'\b403\b|forbidden|permission denied|access denied|Daybreak access', block, re.I):
        summary = '403 请求被拒绝或权限不足'
    elif re.search(r'\b429\b|rate.?limit|quota|usage limit|credits|budget', block, re.I):
        summary = ('429 ' if re.search(r'\b429\b', block) else '') + '请求频率、配额或预算受限'
    elif re.search(r'context|token limit', block, re.I):
        summary = '上下文或令牌数量超出限制'
    elif re.search(r'stream.{0,30}disconnect|connection.{0,30}(?:closed|reset)|timed? out|timeout', block, re.I):
        summary = '响应流中断、连接重置或请求超时'
    else:
        summary = safe_summary(re.sub(r'^■\s*', '', block)) or 'Codex 报错，详情请进入会话查看'
    return issue(summary)


def visible_dialogue(screen, agent, history=False):
    """Observe explicit assistant blocks, excluding input and terminal chrome.

    Tool-call headers and their ambiguous continuations are excluded. This is
    a bounded visible-text lower bound, never an estimate from log bytes or
    token counters. Unknown layouts remain unavailable.
    """
    prompt = CLAUDE_PROMPT if agent == 'claude' else PROMPT
    tool = re.compile(r'^(?:Ran\b|Running\b|Explored\b|Searched\b|Edited\b|Added\b|Deleted\b|Updated\b|'
                      r'(?:Bash|Read|Write|Edit|Grep|Glob|Task|Agent|Workflow|WebFetch|WebSearch)\s*\(|mcp__)')
    lines = screen.splitlines()
    end = next((index for index in range(len(lines) - 1, max(-1, len(lines) - 16), -1) if prompt.match(lines[index])), None)
    body, mode, known = [], '', False
    for line in lines[:end] if end is not None else lines:
        stripped = line.strip()
        if prompt.match(line):
            mode = 'user'
            continue
        if (RUNNING.match(line) or RECOVERING.match(line) or CLAUDE_WORKING.match(line)
                or re.match(r'^\s*[✢✳✶✻✽✱].*\bdone\b', line, re.I)
                or re.match(r'^\s*(?:Worked for\b|[─━]{3,}|\+\s*(?:Show details\b|\d+\s+(?:more\s+)?lines?\b)|'
                            r'(?:⎿\s*)?Tip:\s+Use /btw\b|⏵⏵|✔ Update installed\b|Back to bottom\b|'
                            r'Jump to bottom\b|Queued follow-up inputs\b|\?\s+\d+\s+questions?\b|shift\+.*to answer|'
                            r'\d+% until auto-compact\b|\* Waiting for \d+ background agents? to finish\b)', line, re.I)):
            continue
        marker = re.match(r'^[•●◦]\s+(.*)$', line)
        if marker:
            content = marker.group(1)
            mode = 'tool' if tool.match(content) else 'assistant'
            known = True
            if mode == 'assistant' and content.strip():
                body.append(content)
            continue
        if mode == 'assistant' and stripped:
            body.append(line)
    return dict(dialogue_text='\n'.join(body)[-16384:], dialogue_source='assistant_screen',
                dialogue_identity='terminal:' + agent, dialogue_available=bool(known and end is not None and not history),
                dialogue_complete=False, dialogue_sample_id=str(time.monotonic_ns()))


def analyse_screen(screen, command='codex', dead=False, codex_alive=None, process_state='', exit_status='', in_mode=False, process_check=True, dialogue_context=None):
    lines = screen.splitlines()
    # Input is below the transcript, directly above the model/footer. Never
    # interpret an arbitrary transcript mention of "approval" as an approval UI.
    prompt_index = None
    for i in range(len(lines) - 1, -1, -1):
        if PROMPT.match(lines[i]) and (any(CODEX_FOOTER.fullmatch(x) for x in lines[i + 1:]) or i >= len(lines) - 5):
            prompt_index = i
            break
    tail = lines[max(0, len(lines) - 16):]
    # Drafts may span most of the viewport and quote interrupt hints. A live
    # status belongs above the current input, never inside the draft itself.
    status_end = prompt_index if prompt_index is not None else len(lines)
    # Completed code fences are examples or tool output. An unfinished fence
    # during streaming must not hide the live footer rendered beneath it.
    fenced_lines, fence = set(), None
    for index, line in enumerate(lines[:status_end]):
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if not marker:
            continue
        token, rest = marker.groups()
        if fence is None:
            fence = (token[0], len(token), index)
        elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
            fenced_lines.update(range(fence[2], index + 1))
            fence = None
    status_lines = [line for index, line in enumerate(lines[max(0, status_end - 16):status_end], max(0, status_end - 16))
                    if index not in fenced_lines]
    running = next((x.strip() for x in reversed(status_lines) if RUNNING.match(x) or RECOVERING.match(x)), '')
    codex = codex_alive is True or command == 'codex' or any(re.search(r'GPT[- ]|Ask Codex to do anything', x, re.I) for x in tail)
    approval_lines = [(i, x) for i, x in enumerate(lines[-40:])
                      if re.search(r'^\s*(?:Would you like to (?:run|allow|make|apply|proceed)|Do you want to (?:run|allow|make|apply|proceed)|Allow .+\?|Approve .+\?|需要批准|等待批准)', x, re.I)]
    approval = any(not any(RUNNING.match(later) or re.match(r'^\s*[•●◦]\s+(?!Working\b)', later, re.I)
                           for later in lines[-40:][index + 1:]) for index, _ in approval_lines)
    scrolled = in_mode or any('Back to bottom' in x for x in lines)
    # Queued follow-up counters can remain after a reply has been submitted.
    # Only an active question UI is evidence that another answer is needed.
    queued = max((i for i, x in enumerate(lines) if re.search(r'Queued follow-up inputs', x, re.I)), default=len(lines))
    question_lines = [x for i, x in enumerate(lines) if i >= len(lines) - 16 and i < queued
                      and re.search(r'^\s*\?\s+\d+\s+questions?\b|^\s*Question \d+/\d+\b', x, re.I)]
    question = bool(question_lines)
    goal = next((x.strip() for x in lines[-7:] if re.match(r'^\s*Goal (?:paused|stalled|blocked|usage.limit|budget.limit)', x, re.I)), '')
    response_wait = any(re.search(r'No action is required\.\s*Codex will keep waiting|this menu will close when the response is ready', x, re.I) for x in lines[-20:])
    failure = dict(EMPTY_ISSUE)
    if dead:
        state, status = 'stopped', '进程已退出'
        failure = issue('窗格进程已退出' + ('，退出码 ' + exit_status if exit_status else ''), 'stopped')
    elif codex_alive is False and codex:
        state, status = 'stopped', 'Codex 已退出'
        failure = issue('Codex 进程已不存在，终端可能已返回 Shell', 'stopped')
    elif codex_alive and re.search(r'[Tt]', process_state):
        state, status = 'paused', '进程已挂起'
        failure = issue('Codex 进程被挂起，需要在终端恢复', 'paused')
    elif not codex:
        state, status = 'other', '其他程序 / Shell'
    elif scrolled:
        state, status = 'history', '正在查看历史'
    elif approval:
        state, status = 'approval', '等待确认'
        failure = issue('需要批准操作后才能继续', 'approval', 'warning')
    elif goal:
        state, status = 'paused', '目标已暂停' if 'paused' in goal.lower() else '目标受阻 / 已到限额'
        failure = issue(safe_summary(goal), 'goal', 'warning')
    elif response_wait:
        state, status = 'running', '等待响应（菜单已打开）'
        running = '远端仍在等待服务响应'
    elif running:
        state, status = 'running', '运行中 · 有问题待回答' if question else '运行中'
        failure = issue('有问题等待回答，Codex 仍在运行', 'question', 'warning') if question else detect_failure(lines[:prompt_index] if prompt_index is not None else lines, running, False)
        if failure['error_kind'] == 'retrying':
            status = '重连 / 重试中'
        elif failure['error_kind'] == 'safety':
            state, status = 'error', '安全策略拦截'
    elif question:
        state, status = 'input', '等待回答'
        failure = issue('需要回答问题后才能继续', 'question', 'warning')
    elif prompt_index is not None:
        draft = PROMPT.match(lines[prompt_index]).group(1).strip()
        state, status = 'idle', '等待输入' + ('（有草稿）' if draft and draft.lower() != 'ask codex to do anything' else '')
    else:
        state, status = 'unknown', '状态未知'
    end = prompt_index if prompt_index is not None else len(lines)
    if codex and state in ('idle', 'unknown'):
        failure = detect_failure(lines[:end], '', False)
        if failure['error_kind']:
            state, status = ('interrupted', '本轮已中断') if failure['error_kind'] == 'interrupted' else ('error', '安全策略拦截' if failure['error_kind'] == 'safety' else '运行报错')
    if not process_check and codex and state in ('idle', 'running', 'approval', 'input'):
        state, status = 'unknown', '进程状态待核实'
        failure = issue('无法读取远端进程状态，暂不确定 Codex 是否仍在运行', 'unknown', 'warning')
    body = []
    for line in lines[:end]:
        s = line.strip()
        if not s or RUNNING.match(line) or RECOVERING.match(line) or FOOTER.search(s):
            continue
        if re.search(r'^Worked for |background terminals? running|^└ Tip:|Back to bottom|Queued follow-up inputs|^\?\s+\d+\s+questions?\b|^shift\+.*to answer', s, re.I):
            continue
        body.append(s)
    return dict(state=state, status=status, working=running, preview=body[-3:], activity='\n'.join(body[-3:]),
                **visible_dialogue(dialogue_context if dialogue_context is not None else screen, 'codex', scrolled), **failure)


def analyse_claude_screen(screen, command='claude', dead=False, agent_alive=None,
                         process_state='', exit_status='', in_mode=False, process_check=True, dialogue_context=None):
    lines = screen.splitlines()
    tail = lines[-24:]
    prompt = next((i for i in range(len(lines) - 1, max(-1, len(lines) - 16), -1)
                   if CLAUDE_PROMPT.match(lines[i])), None)
    end = prompt if prompt is not None else len(lines)
    working_index = max((i for i, x in enumerate(lines) if i >= len(lines) - 24 and CLAUDE_WORKING.match(x)), default=-1)
    finished_index = max((i for i, x in enumerate(lines) if re.match(r'^\s*[✢✳✶✻✽✱].*\bdone\b', x, re.I)), default=-1)
    working = lines[working_index].strip() if working_index > finished_index else ''
    workflow_match = next((re.search(r'^\s*[*·✢✳✶✻✽✱]?\s*Waiting for (\d+) (?:dynamic )?workflows? to finish\b', x, re.I)
                           for x in reversed(tail) if re.search(r'Waiting for \d+ (?:dynamic )?workflows? to finish', x, re.I)), None)
    workflows = int(workflow_match.group(1)) if workflow_match else 0
    # Finished spinner lines (e.g. "✻ Churned for 54s · done 1:48 AM")
    # and the always-present permission-mode footer are not active work.
    scrolled = in_mode or any('Back to bottom' in x or re.search(r'Jump to bottom\s*\(ctrl\+End\)', x, re.I) for x in tail)
    ui_agents = 0
    if prompt is not None and not scrolled:
        # This is Claude's live status area immediately above the current
        # input separator, not an assistant's mention of background work.
        for index in range(max(0, prompt - 6), prompt):
            match = re.fullmatch(r'\s{0,1}\* Waiting for ([1-9]\d*) background agents? to finish\s*', lines[index], re.I)
            if not match:
                continue
            footer = [line for line in lines[index + 1:prompt] if line.strip()]
            separator = any(re.match(r'^\s*[─━]{3,}', line) for line in footer)
            footer_only = all(re.match(r'^\s*(?:[─━]{3,}.*|✔\s+Update installed\b.*)$', line) for line in footer)
            # A fenced example of this line is still prose, even beside a prompt.
            fenced = sum(bool(re.match(r'^\s*(?:```|~~~)', line)) for line in lines[:index]) % 2
            if separator and footer_only and not fenced:
                ui_agents = int(match.group(1))
    dialog_lines = lines[-35:]
    approval = any(re.match(r'^\s*(?:Do you want to (?:proceed|allow|run|make|create|edit)|Would you like to (?:proceed|allow|run)|Allow .+\?|Claude (?:wants|requested) permission)', x, re.I)
                   for x in dialog_lines)
    choices = any(re.match(r'^\s*(?:❯\s*)?[1-9][.)]\s+(?:Yes|Allow|No|Deny|是|否|允许|拒绝)', x, re.I) for x in dialog_lines)
    approval = approval and choices and not working
    question = not working and any(re.search(r'Enter to (?:select|submit|confirm).*Esc to cancel|Tab/Arrow keys to navigate|Type (?:your answer|something)', x, re.I) for x in tail)
    failure = dict(EMPTY_ISSUE)
    if dead:
        state, status = 'stopped', '进程已退出'
        failure = issue('窗格进程已退出' + ('，退出码 ' + exit_status if exit_status else ''), 'stopped')
    elif agent_alive is False:
        state, status = 'stopped', 'Claude 已退出'
        failure = issue('Claude 进程已不存在，终端可能已返回 Shell', 'stopped')
    elif agent_alive and re.search(r'[Tt]', process_state):
        state, status = 'paused', '进程已挂起'
        failure = issue('Claude 进程被挂起，需要在终端恢复', 'paused')
    elif scrolled:
        state, status = 'history', '正在查看历史'
    elif approval:
        state, status = 'approval', '等待确认'
        failure = issue('需要批准操作后才能继续', 'approval', 'warning')
    elif question:
        state, status = 'input', '等待回答'
        failure = issue('需要回答问题后才能继续', 'question', 'warning')
    elif working:
        state, status = 'running', '运行中'
    elif ui_agents:
        state, status = 'running', '子智能体运行中（{}）'.format(ui_agents)
        working = '等待 {} 个后台子智能体完成'.format(ui_agents)
    elif workflows:
        state, status = 'running', '后台工作流运行中（{}）'.format(workflows)
        working = '等待 {} 个后台工作流完成'.format(workflows)
    elif prompt is not None:
        draft = CLAUDE_PROMPT.match(lines[prompt]).group(1).strip()
        state, status = 'idle', '等待输入' + ('（有草稿）' if draft else '')
    else:
        state, status = 'unknown', '状态未知'
    # Claude's top-level API failures use a distinct glyph. Do not promote
    # indented tool output or a quoted API error inside an answer to an alert.
    transcript = lines[:end]
    failure_index = next((i for i in range(len(transcript) - 1, max(-1, len(transcript) - 40), -1)
                          if re.match(r'^\s*(?:⎿\s*)?(?:API Error:|Error:.*(?:\b[45]\d\d\b|connect|timeout)|Interrupted\s*[·•]|You.ve hit your limit|Credit balance is too low|Not logged in)', transcript[i], re.I)), None)
    if failure_index is not None and state in ('idle', 'unknown', 'running'):
        newer = transcript[failure_index + 1:]
        historical = any(CLAUDE_PROMPT.match(x) or re.match(r'^●\s', x) or
                         re.match(r'^\s*[✢✳✶✻✽✱].*\bdone\b', x, re.I) for x in newer)
        if working and any(CLAUDE_WORKING.match(x) for x in newer):
            historical = True
        if not historical:
            block = ' '.join(x.strip() for x in transcript[failure_index:failure_index + 5])
            if re.search(r'Interrupted\s*[·•]', block, re.I):
                failure = issue('本轮已中断，需要重新输入或继续', 'interrupted')
            else:
                failure = detect_failure(['■ ' + block], '', False)
            retrying = re.search(r'retrying|reconnecting|retry (?:in|attempt)|attempt \d+/\d+', block, re.I)
            if retrying:
                state, status = 'running', '重连 / 重试中'
                failure = issue('正在重连 / 重试 · ' + failure['error_summary'], 'retrying')
            else:
                state, status = ('interrupted', '本轮已中断') if failure['error_kind'] == 'interrupted' else ('error', '运行报错')
    if not process_check and state in ('idle', 'running', 'approval', 'input'):
        state, status = 'unknown', '进程状态待核实'
        failure = issue('无法读取远端进程状态，暂不确定 Claude 是否仍在运行', 'unknown', 'warning')
    body = [x.strip() for x in transcript if x.strip() and not CLAUDE_WORKING.match(x)
            and not re.search(r'^\s*[─━]+|bypass permissions on|shift\+tab to cycle|until auto-compact|^\s*[✢✳✶✻✽✱].*\bdone\b|^\s*✔ Update installed', x, re.I)]
    progress = '|'.join(re.findall(r'\d+\s*/\s*\d+', '\n'.join(tail))) if workflows else ''
    return dict(state=state, status=status, working=working, preview=body[-3:], activity='\n'.join(body[-3:]),
                background_workflows=workflows, background_ui_agents=ui_agents, background_progress=progress,
                **visible_dialogue(dialogue_context if dialogue_context is not None else screen, 'claude', scrolled), **failure)


def read_processes():
    try:
        result = subprocess.run(['ps', '-eo', 'pid=,ppid=,stat=,comm='], text=True, capture_output=True, timeout=2)
        if result.returncode:
            return None
        return ProcessSnapshot({int(p): (int(parent), state, name) for p, parent, state, name in
                                (line.split(None, 3) for line in result.stdout.splitlines() if line.strip())})
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def find_codex(processes, root_pid):
    if processes is None:
        return None, ''
    # Choose the nearest Codex descendant, never a subagent under that process.
    processes = as_snapshot(processes)
    pending, seen = [root_pid], set()
    while pending:
        pid = pending.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        entry = processes.get(pid)
        if entry and os.path.basename(entry[2]) == 'codex' and 'Z' not in entry[1]:
            return pid, entry[1]
        pending.extend(processes.children.get(pid, ()))
    return 0, ''


def find_agent(processes, root_pid):
    if processes is None:
        return None, '', ''
    return as_snapshot(processes).find_agent(root_pid)


class UsageLogWatcher:
    """Rebuild root-session usage without borrowing visual activity counters.

    History is read from byte zero in bounded chunks, independently of the
    lifecycle reader's tail cursor. Numeric values describe observed records;
    completeness flags stay false until the scan reaches the current EOF.
    """
    TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cached_input_tokens',
                    'cache_write_input_tokens', 'total_tokens')
    CLAUDE_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                     'cache_creation_input_tokens')
    MAX_LINE_BYTES = 4194304

    def __init__(self, max_bytes=1048576):
        self.max_bytes = max(1, int(max_bytes))
        self.files = {}
        self.next_cleanup = 0

    @staticmethod
    def number(value):
        # bool is an int subclass, but it is not a token count.
        return value if type(value) is int and value >= 0 else None

    @classmethod
    def empty(cls, agent):
        result = dict(identity=None, agent=agent, scope='root_session',
                      usage_available=False, usage_complete=False,
                      tool_completions=None, tools_complete=False, active_tools=None)
        result.update((field, None) for field in cls.TOKEN_FIELDS)
        return result

    def new_file(self, agent, path, info, previous=None):
        generation = previous['generation'] + 1 if previous is not None else 0
        saved = dict(key=(info.st_dev, info.st_ino), offset=0, buffer=b'',
                     anchor=b'', discarding=False, generation=generation,
                     last_seen=time.monotonic(),
                     scan_error=False, usage_error=False, tools_error=False,
                     usage_position=-1, usage_barrier=-1, tail_usage_complete=False,
                     root=True if agent == 'claude' else None,
                     codex_usage=None, claude_usage={}, claude_sums=[0] * 4,
                     claude_unknown=[0] * 4, tool_ids={}, tool_parents=[], tool_count=0,
                     pending_tools=[], finished_tool_ids=set(), active_tools_error=False,
                     identity='{}:{}:{}:{}:{}'.format(agent, path, info.st_dev,
                                                     info.st_ino, generation))
        self.files[(agent, path)] = saved
        return saved

    @staticmethod
    def tool_result(saved, identities):
        if not identities:
            saved['tools_error'] = True
            return
        parents = saved['tool_parents']
        groups = set()
        for identity in identities:
            group = saved['tool_ids'].get(identity)
            if group is None:
                continue
            while parents[group] != group:
                parents[group] = parents[parents[group]]
                group = parents[group]
            groups.add(group)
        if not groups:
            group = len(parents)
            parents.append(group)
            saved['tool_count'] += 1
        else:
            group = min(groups)
            for other in groups:
                if other != group:
                    parents[other] = group
                    saved['tool_count'] -= 1
        # A later record may bridge two aliases previously seen separately.
        # Merge their equivalence groups instead of keeping both completions.
        for identity in identities:
            saved['tool_ids'][identity] = group

    @staticmethod
    def tool_started(saved, identities):
        identities = set(identities)
        if not identities:
            saved['active_tools_error'] = True
            return
        remaining = []
        for pending in saved['pending_tools']:
            if pending & identities:
                identities.update(pending)
            else:
                remaining.append(pending)
        # Replayed assistant fragments must not reopen a completed call.
        if not identities & saved['finished_tool_ids']:
            remaining.append(identities)
        saved['pending_tools'] = remaining

    @staticmethod
    def tool_finished(saved, identities):
        identities = set(identities)
        if not identities:
            saved['active_tools_error'] = True
            return
        remaining = []
        for pending in saved['pending_tools']:
            if pending & identities:
                identities.update(pending)
            else:
                remaining.append(pending)
        saved['pending_tools'] = remaining
        saved['finished_tool_ids'].update(identities)

    @staticmethod
    def tools_ended(saved):
        for pending in saved['pending_tools']:
            saved['finished_tool_ids'].update(pending)
        saved['pending_tools'] = []
        saved['active_tools_error'] = False

    @staticmethod
    def usage_gap(saved, agent, position):
        if agent == 'codex':
            if position < saved['usage_position']:
                return
            saved['usage_barrier'] = max(saved['usage_barrier'], position)
        saved['usage_error'] = True

    def consume_codex(self, saved, item, position=0):
        payload = item.get('payload')
        if not isinstance(payload, dict):
            if item.get('type') in ('session_meta', 'event_msg', 'response_item'):
                saved['scan_error'] = True
                self.usage_gap(saved, 'codex', position)
            return
        if item.get('type') == 'session_meta':
            source = payload.get('source')
            # A child/guardian rollout must never be billed again as a root.
            saved['root'] = isinstance(source, str) and bool(source.strip())
            return
        if saved['root'] is not True:
            return
        kind = payload.get('type')
        if item.get('type') == 'event_msg' and kind in (
                'turn_complete', 'task_complete', 'turn_aborted', 'task_aborted',
                'turn_failed', 'task_failed', 'error'):
            self.tools_ended(saved)
        if item.get('type') == 'response_item' and kind in ('function_call', 'custom_tool_call'):
            call = payload.get('call_id')
            if not isinstance(call, str) or not call:
                saved['active_tools_error'] = True
            else:
                identities = [('call', call)]
                if isinstance(payload.get('id'), str) and payload['id']:
                    identities.append(('item', payload['id']))
                self.tool_started(saved, identities)
        elif item.get('type') == 'response_item' and kind in (
                'web_search_call', 'local_shell_call', 'image_generation_call'):
            tid, status = payload.get('id'), payload.get('status')
            identities = [('item', tid)] if isinstance(tid, str) and tid else []
            if status in ('started', 'in_progress', 'running', 'queued', 'searching'):
                self.tool_started(saved, identities)
            elif status in ('completed', 'failed', 'cancelled', 'canceled', 'incomplete'):
                self.tool_finished(saved, identities)
            else:
                saved['active_tools_error'] = True
        elif item.get('type') == 'response_item' and isinstance(kind, str) and kind.endswith('_call'):
            saved['active_tools_error'] = True
        if item.get('type') == 'event_msg' and kind == 'token_count':
            if position < saved['usage_position']:
                return
            info = payload.get('info')
            if info is None:  # Rate-limit-only events contain no usage.
                return
            usage = info.get('total_token_usage') if isinstance(info, dict) else None
            if not isinstance(usage, dict):
                self.usage_gap(saved, 'codex', position)
                return
            incoming = {field: self.number(usage.get(field)) for field in self.TOKEN_FIELDS}
            if incoming['input_tokens'] is None or incoming['output_tokens'] is None:
                self.usage_gap(saved, 'codex', position)
                return
            total = incoming['input_tokens'] + incoming['output_tokens']
            # A later authoritative cumulative snapshot covers earlier usage
            # gaps, even when a malformed old record still makes tools partial.
            invalid = bool(
                'total_tokens' in usage and incoming['total_tokens'] != total or
                any(field in usage and incoming[field] is None
                    for field in ('cached_input_tokens', 'cache_write_input_tokens')))
            if invalid:
                saved['usage_barrier'] = max(saved['usage_barrier'], position)
            elif position >= saved['usage_barrier']:
                saved['usage_barrier'] = -1
            saved['usage_error'] = invalid or saved['usage_barrier'] > position
            incoming['total_tokens'] = total
            # These are cumulative snapshots, not per-event deltas.
            saved['codex_usage'] = incoming
            saved['usage_position'] = position
        elif item.get('type') == 'response_item' and kind in ('function_call_output', 'custom_tool_call_output'):
            identities = [('call', payload['call_id'])] if isinstance(payload.get('call_id'), str) and payload['call_id'] else []
            if isinstance(payload.get('id'), str) and payload['id']:
                identities.append(('item', payload['id']))
            self.tool_result(saved, identities)
            self.tool_finished(saved, identities)
            if not isinstance(payload.get('call_id'), str) or not payload['call_id']:
                # An output item's ID need not be the call item's ID. Without
                # the call alias, a pending function cannot reliably be closed.
                saved['active_tools_error'] = True
        elif (item.get('type') == 'response_item' and
              kind in ('web_search_call', 'local_shell_call', 'image_generation_call') and
              payload.get('status') in ('completed', 'failed')):
            tid = payload.get('id')
            self.tool_result(saved, [('item', tid)] if isinstance(tid, str) and tid else [])

    def consume_claude(self, saved, item):
        if item.get('isSidechain'):
            return
        message = item.get('message')
        if item.get('type') == 'assistant' and not item.get('isApiErrorMessage'):
            if not isinstance(message, dict) or not isinstance(message.get('id'), str) or not message['id']:
                saved['usage_error'] = True
            else:
                mid = message['id']
                usage = message.get('usage')
                if mid not in saved['claude_usage']:
                    saved['claude_usage'][mid] = [None] * 4
                    saved['claude_unknown'] = [count + 1 for count in saved['claude_unknown']]
                previous = saved['claude_usage'][mid]
                if not isinstance(usage, dict):
                    usage = {}
                for index, field in enumerate(self.CLAUDE_FIELDS):
                    value = self.number(usage.get(field))
                    if field in usage and value is None:
                        saved['usage_error'] = True
                    if value is None:
                        continue
                    old = previous[index]
                    # Streamed Claude records repeat the same billed message.
                    # Merge each counter once, including larger final snapshots.
                    if old is None:
                        saved['claude_unknown'][index] -= 1
                        saved['claude_sums'][index] += value
                        previous[index] = value
                    elif value > old:
                        saved['claude_sums'][index] += value - old
                        previous[index] = value
        content = message.get('content') if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use' and not item.get('isApiErrorMessage'):
                    tid = block.get('id')
                    self.tool_started(saved, [('tool', tid)] if isinstance(tid, str) and tid else [])
                if block.get('type') != 'tool_result':
                    continue
                tid = block.get('tool_use_id')
                identities = [('tool', tid)] if isinstance(tid, str) and tid else []
                self.tool_result(saved, identities)
                self.tool_finished(saved, identities)
        if (isinstance(message, dict) and message.get('stop_reason') == 'end_turn' or
                item.get('type') == 'assistant' and item.get('isApiErrorMessage') or
                item.get('type') == 'system' and item.get('subtype') in (
                    'turn_duration', 'turn_complete', 'task_complete', 'turn_aborted',
                    'task_aborted', 'turn_failed', 'task_failed', 'error') or
                item.get('type') == 'result' and (
                    item.get('subtype') == 'success' or
                    isinstance(item.get('subtype'), str) and item['subtype'].startswith('error_'))):
            self.tools_ended(saved)

    def consume_line(self, saved, agent, line, position=0):
        if not line.strip():
            return
        if len(line) > self.MAX_LINE_BYTES:
            saved['scan_error'] = True
            self.usage_gap(saved, agent, position)
            return
        try:
            item = json.loads(line)
        except (ValueError, TypeError, RecursionError):
            saved['scan_error'] = True
            self.usage_gap(saved, agent, position)
            return
        if not isinstance(item, dict):
            saved['scan_error'] = True
            self.usage_gap(saved, agent, position)
        elif agent == 'codex':
            self.consume_codex(saved, item, position)
        else:
            self.consume_claude(saved, item)

    def result(self, saved, agent, complete=False):
        result = self.empty(agent)
        result['identity'] = saved['identity']
        if saved['root'] is not True:
            return result
        usage = saved['codex_usage'] if agent == 'codex' else None
        if agent == 'claude' and saved['claude_usage']:
            values = [None if missing else count for missing, count in
                      zip(saved['claude_unknown'], saved['claude_sums'])]
            raw, output, cached, written = values
            # Both agents expose input INCLUDING cache, so total=input+output.
            incoming = raw + cached + written if all(value is not None for value in (raw, cached, written)) else None
            usage = dict(input_tokens=incoming, output_tokens=output,
                         cached_input_tokens=cached, cache_write_input_tokens=written,
                         total_tokens=incoming + output if incoming is not None and output is not None else None)
        if usage:
            result.update(usage)
            result['usage_available'] = usage['input_tokens'] is not None and usage['output_tokens'] is not None
        coverage = complete and not saved['scan_error']
        usage_coverage = complete or agent == 'codex' and saved['tail_usage_complete']
        result['usage_complete'] = bool(usage_coverage and result['usage_available'] and not saved['usage_error'])
        result['tool_completions'] = saved['tool_count']
        result['tools_complete'] = bool(coverage and not saved['tools_error'])
        if coverage and not saved['active_tools_error']:
            result['active_tools'] = len(saved['pending_tools'])
        return result

    def codex_tail_usage(self, saved, path):
        """Find a recent cumulative snapshot without waiting for tool replay.

        The bounded tail is checked again while history is rebuilding, so new
        usage remains observable. A partial or unverifiable tail stays partial.
        """
        try:
            with open(path, 'rb') as log:
                info = os.fstat(log.fileno())
                if saved['key'] != (info.st_dev, info.st_ino) or info.st_size < saved['offset']:
                    return
                start = max(0, info.st_size - self.max_bytes)
                partial_start = False
                if start:
                    log.seek(start - 1)
                    partial_start = log.read(1) != b'\n'
                log.seek(start)
                data = log.read(self.max_bytes)
                if os.fstat(log.fileno()).st_size != start + len(data):
                    return
            pieces = data.split(b'\n')
            position = start
            if partial_start:
                position += len(pieces.pop(0)) + 1  # A leading partial line.
            positioned = []
            for line in pieces:
                positioned.append((position, line))
                position += len(line) + 1
            pending_tail = False
            for position, line in reversed(positioned):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except (ValueError, TypeError, RecursionError):
                    if not data.endswith(b'\n') and position + len(line) == start + len(data):
                        pending_tail = True
                    else:
                        self.usage_gap(saved, 'codex', position)
                    continue
                if not isinstance(item, dict):
                    self.usage_gap(saved, 'codex', position)
                    continue
                payload = item.get('payload')
                if item.get('type') != 'event_msg' or not isinstance(payload, dict) or payload.get('type') != 'token_count':
                    continue
                if payload.get('info') is None:
                    continue
                self.consume_codex(saved, item, position)
                usage = payload['info'].get('total_token_usage') if isinstance(payload['info'], dict) else None
                if isinstance(usage, dict) and all(self.number(usage.get(field)) is not None for field in ('input_tokens', 'output_tokens')):
                    saved['tail_usage_complete'] = not saved['usage_error'] and not pending_tail
                    return
        except OSError:
            pass

    def sample(self, path, agent):
        if agent not in ('codex', 'claude') or path is None:
            return self.empty(agent)
        path = os.fsdecode(os.fspath(path))
        # Claude's background files can be resolved elsewhere for activity,
        # but never contribute another copy of root-session accounting here.
        if agent == 'claude' and ('subagents' in path.replace('\\', '/').split('/') or
                                  os.path.basename(path).startswith('agent-')):
            return self.empty(agent)
        saved = self.files.get((agent, path))
        now = time.monotonic()
        if saved is not None:
            saved['last_seen'] = now
            saved['tail_usage_complete'] = False
        if now >= self.next_cleanup:
            self.files = {key: value for key, value in self.files.items()
                          if now - value['last_seen'] <= 120}
            self.next_cleanup = now + 10
        try:
            with open(path, 'rb') as log:
                info = os.fstat(log.fileno())
                reset = saved is None or saved['key'] != (info.st_dev, info.st_ino) or info.st_size < saved['offset']
                if not reset and saved['anchor']:
                    log.seek(saved['offset'] - len(saved['anchor']))
                    reset = log.read(len(saved['anchor'])) != saved['anchor']
                if reset:
                    saved = self.new_file(agent, path, info, saved)
                log.seek(saved['offset'])
                data = log.read(self.max_bytes)
                saved['offset'] = log.tell()
                saved['anchor'] = (saved['anchor'] + data)[-64:]
                end_info = os.fstat(log.fileno())
                if end_info.st_size < saved['offset']:
                    return self.result(saved, agent)
            position = saved['offset'] - len(data) - len(saved['buffer'])
            pieces = (saved['buffer'] + data).split(b'\n')
            saved['buffer'] = pieces.pop()
            if saved['discarding']:
                if pieces:
                    position += len(pieces.pop(0)) + 1
                    saved['discarding'] = False
                else:
                    saved['buffer'] = b''
            for line in pieces:
                self.consume_line(saved, agent, line, position)
                position += len(line) + 1
            if len(saved['buffer']) > self.MAX_LINE_BYTES:
                saved['scan_error'] = True
                self.usage_gap(saved, agent, saved['offset'] - len(saved['buffer']))
                saved['buffer'] = b''
                saved['discarding'] = True
            at_end = saved['offset'] >= end_info.st_size
            # JSONL permits a complete final record without a trailing newline.
            # Keep a partial record until the writer finishes it.
            if at_end and saved['buffer'] and data and not saved['discarding']:
                try:
                    json.loads(saved['buffer'])
                except (ValueError, TypeError, RecursionError):
                    pass
                else:
                    self.consume_line(saved, agent, saved['buffer'], saved['offset'] - len(saved['buffer']))
                    saved['buffer'] = b''
            complete = at_end and not saved['buffer'] and not saved['discarding']
            if agent == 'codex' and saved['root'] is True and not complete:
                self.codex_tail_usage(saved, path)
            return self.result(saved, agent, complete)
        except OSError:
            return self.result(saved, agent) if saved is not None else self.empty(agent)


class ResultLogWatcher:
    """Stable root reply markers, separate from lifecycle alerts and activity.

    Repeated reads/reconnects return the same final marker.  A bounded history
    gap, malformed suffix or partial record remains unobservable until a newer
    verified result establishes a baseline; no text, tools or token counters
    are used as a substitute for a final reply.
    """
    def __init__(self, max_bytes=1048576):
        self.max_bytes = max_bytes
        self.files = {}

    @staticmethod
    def empty():
        return dict(result_identity=None, result_marker=None, result_observable=False)

    @staticmethod
    def decode(item, agent):
        if item.get('isSidechain'):
            return None, None
        kind = item.get('type')
        if agent == 'codex':
            payload = item.get('payload')
            if not isinstance(payload, dict):
                return None, None
            subtype = payload.get('type')
            if kind == 'event_msg' and subtype in ('task_started', 'turn_started'):
                return 'start', None
            if kind == 'event_msg' and subtype in ('task_complete', 'turn_complete'):
                return 'complete', None
            return None, None
        if agent != 'claude':
            return None, None
        message = item.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        if kind == 'api-request' and item.get('querySource') in (None, 'repl_main_thread'):
            return 'start', None
        if kind == 'user' and not item.get('isMeta'):
            texts = content if isinstance(content, str) else '\n'.join(
                block.get('text', '') for block in content if isinstance(block, dict)
                and isinstance(block.get('text'), str)) if isinstance(content, list) else ''
            if texts.strip() and '<task-notification>' not in texts and not texts.startswith('[Request interrupted by user'):
                return 'start', None
        if kind == 'assistant' and not item.get('isApiErrorMessage') and isinstance(message, dict) and message.get('stop_reason') == 'end_turn':
            text = content if isinstance(content, str) else '\n'.join(
                block.get('text', '') for block in content if isinstance(block, dict) and
                block.get('type') == 'text' and isinstance(block.get('text'), str)) if isinstance(content, list) else ''
            if text.strip():
                return 'reply', message.get('id')
        return None, None

    def sample(self, path, agent):
        if path is None or agent not in ('codex', 'claude'):
            return self.empty()
        path = os.fsdecode(os.fspath(path))
        if agent == 'claude' and ('subagents' in path.replace('\\', '/').split('/') or os.path.basename(path).startswith('agent-')):
            return self.empty()
        try:
            with open(path, 'rb') as log:
                info = os.fstat(log.fileno())
                first = log.readline(1048576)
                metadata = json.loads(first)
                if not isinstance(metadata, dict):
                    return self.empty()
                if agent == 'codex':
                    payload = metadata.get('payload') or {}
                    if (metadata.get('type') != 'session_meta' or not isinstance(payload, dict) or
                            not isinstance(payload.get('source'), str) or not payload['source'].strip()):
                        return self.empty()
                elif metadata.get('isSidechain'):
                    return self.empty()
                key = (info.st_dev, info.st_ino, hashlib.sha256(first).hexdigest())
                identity = hashlib.sha256(('{}:{}:{}'.format(agent, path, key)).encode()).hexdigest()
                saved = self.files.get((agent, path))
                reset = saved is None or saved['key'] != key
                replaced = False
                if not reset:
                    replaced = info.st_size < saved['offset']
                    if not replaced and saved['anchor']:
                        log.seek(saved['offset'] - len(saved['anchor']))
                        replaced = log.read(len(saved['anchor'])) != saved['anchor']
                    reset = replaced
                if reset:
                    if replaced:
                        log.seek(max(0, info.st_size - 64))
                        stamp = hashlib.sha256(first + str(info.st_size).encode() + log.read(64)).hexdigest()
                        identity += ':' + stamp
                    start = max(0, info.st_size - self.max_bytes)
                    saved = dict(key=key, identity=identity, offset=start, buffer=b'', anchor=b'',
                                 marker=None, answered=False, reply_ids=set(), observable=start == 0)
                    self.files[(agent, path)] = saved
                    if start:
                        log.seek(start - 1)
                        if log.read(1) != b'\n':
                            log.readline(self.max_bytes)
                            saved['offset'] = log.tell()
                elif info.st_size - saved['offset'] > self.max_bytes:
                    log.seek(info.st_size - self.max_bytes - 1)
                    if log.read(1) != b'\n':
                        log.readline(self.max_bytes)
                    saved.update(offset=log.tell(), buffer=b'', observable=False, answered=False)
                log.seek(saved['offset'])
                incoming = log.read(self.max_bytes)
                begin = saved['offset'] - len(saved['buffer'])
                data = saved['buffer'] + incoming
                saved['offset'] = log.tell()
                saved['anchor'] = (saved['anchor'] + incoming)[-64:]
                at_end = saved['offset'] == info.st_size
            pieces = data.split(b'\n')
            saved['buffer'] = pieces.pop()
            if at_end and saved['buffer']:
                try:
                    json.loads(saved['buffer'])
                except (ValueError, TypeError):
                    pass
                else:
                    pieces.append(saved['buffer']); saved['buffer'] = b''
            position = begin
            for line in pieces:
                if line.strip():
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict):
                            raise ValueError('Not a root record')
                        kind, reply_id = self.decode(item, agent)
                    except (ValueError, TypeError, AttributeError, RecursionError):
                        saved['observable'] = False
                        position += len(line) + 1
                        continue
                    if kind == 'start':
                        saved['answered'] = False
                    elif kind == 'reply':
                        duplicate = isinstance(reply_id, str) and reply_id in saved['reply_ids']
                        if not duplicate:
                            saved['marker'] = ('message:' + hashlib.sha256(reply_id.encode()).hexdigest()
                                               if isinstance(reply_id, str) and reply_id else
                                               '{}:{}'.format(position, hashlib.sha256(line).hexdigest()))
                            saved['observable'] = True
                            saved['answered'] = True
                            if isinstance(reply_id, str):
                                saved['reply_ids'].add(reply_id)
                    elif kind == 'complete' and not saved['answered']:
                        saved['marker'] = '{}:{}'.format(position, hashlib.sha256(line).hexdigest())
                        saved['observable'], saved['answered'] = True, True
                position += len(line) + 1
            if len(saved['buffer']) > self.max_bytes:
                saved['buffer'] = b''
                saved['observable'] = False
            if len(self.files) > 100:
                self.files = {(agent, path): saved}
            return dict(result_identity=saved['identity'], result_marker=saved['marker'],
                        result_observable=bool(saved['observable'] and at_end and not saved['buffer']))
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            return self.empty()


class RolloutWatcher:
    """Recover Codex state and read new alerts from an unambiguous CLI root log.

    No session-directory scans or cwd guesses: map logs through open process
    descriptors, reject subagents, and never replay alerts on first contact.
    """
    def __init__(self):
        self.roots, self.files = {}, {}
        self.cursors = {}

    recover_initial_lifecycle = True

    def tail_lifecycle(self, path, key, size):
        """Recover only current Codex state from a bounded, verified root tail.

        No historical error records become alert events. An unobserved gap
        without a complete lifecycle boundary cannot inherit an earlier state.
        """
        if not self.recover_initial_lifecycle:
            return '', b''
        try:
            with open(path, 'rb') as log:
                current = os.fstat(log.fileno())
                if (current.st_dev, current.st_ino) != key or current.st_size < size:
                    return '', b''
                start = max(0, size - 1048576)
                partial_start = False
                if start:
                    log.seek(start - 1)
                    partial_start = log.read(1) != b'\n'
                log.seek(start)
                data = log.read(size - start)
            lines = data.split(b'\n')
            if partial_start:
                lines.pop(0)  # A boundary before the tail is not observable.
            if lines and lines[-1].strip():
                # A writer may be halfway through a new boundary; wait for its
                # next complete record rather than recover an obsolete state.
                return '', lines[-1]
            last = ''
            for line in lines:
                if not line.strip():
                    continue
                try:
                    decoded = self.decode(json.loads(line))
                    if decoded is not None:
                        last = decoded[0]
                except (ValueError, TypeError, AttributeError):
                    last = ''
            return last, b''
        except OSError:
            return '', b''

    def decode(self, item):
        payload = item.get('payload', {})
        kind = payload.get('type')
        if item.get('type') != 'event_msg' or kind not in ('task_started', 'turn_started', 'task_complete', 'turn_complete', 'turn_aborted', 'turn_failed', 'error', 'stream_error'):
            return None
        message = payload.get('message', '')
        if not message and isinstance(payload.get('error'), dict):
            message = payload['error'].get('message', '')
        return kind, message, payload.get('reason', '')

    def root_log(self, pid, now):
        cached = self.roots.get(pid)
        if cached and now - cached[0] < 2:
            return cached[1]
        roots = set()
        try:
            for fd in os.listdir('/proc/{}/fd'.format(pid)):
                try:
                    path = os.readlink('/proc/{}/fd/{}'.format(pid, fd))
                    if not os.path.basename(path).startswith('rollout-') or not path.endswith('.jsonl'):
                        continue
                    with open(path, 'rb') as log:
                        metadata = json.loads(log.readline(1048576))
                    if metadata.get('type') == 'session_meta' and metadata.get('payload', {}).get('source') == 'cli':
                        roots.add(path)
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        path = next(iter(roots)) if len(roots) == 1 else None
        self.roots[pid] = (now, path)
        if len(self.roots) > 100:
            self.roots = {pid: self.roots[pid]}
        return path

    def read_path(self, path, cursor=None):
        try:
            info = os.stat(path)
            key = (info.st_dev, info.st_ino)
            saved = self.files.get(path)
            if saved is None:
                resumed = (cursor or {}).get('path') == path and (cursor or {}).get('device') == info.st_dev and (cursor or {}).get('inode') == info.st_ino
                offset = max(0, min(info.st_size, int((cursor or {}).get('offset', info.st_size)))) if resumed else info.st_size
                saved = dict(key=key, offset=offset, buffer=b'', events=[], last_type='', retry_signature='')
                saved['last_type'], saved['buffer'] = self.tail_lifecycle(path, key, offset)
                self.files[path] = saved
                if not resumed:
                    if len(self.files) > 100:
                        self.files = {path: self.files[path]}
                    return [], saved['last_type']
            if saved['key'] != key or info.st_size < saved['offset']:
                saved = dict(key=key, offset=info.st_size, buffer=b'', events=[], last_type='', retry_signature='')
                self.files[path] = saved
                saved['last_type'], saved['buffer'] = self.tail_lifecycle(path, key, info.st_size)
                return [], saved['last_type']
            if self.recover_initial_lifecycle and info.st_size - saved['offset'] > 1048576:
                saved.update(offset=info.st_size, buffer=b'', last_type='', retry_signature='')
                saved['last_type'], saved['buffer'] = self.tail_lifecycle(path, key, info.st_size)
                return list(saved['events']), saved['last_type']
            with open(path, 'rb') as log:
                # Bound work per snapshot, including exceptionally large tool output.
                if info.st_size - saved['offset'] > 1048576:
                    log.seek(info.st_size - 1048576)
                    log.readline()
                    saved['offset'], saved['buffer'] = log.tell(), b''
                else:
                    log.seek(saved['offset'])
                data = saved['buffer'] + log.read(1048576)
                saved['offset'] = log.tell()
            pieces = data.split(b'\n')
            saved['buffer'] = pieces.pop()
            position = saved['offset'] - len(data)
            for line in pieces:
                position += len(line) + 1
                try:
                    item = json.loads(line)
                    decoded = self.decode(item)
                    if decoded is None:
                        continue
                    kind, message, reason = decoded
                    saved['last_type'] = kind
                    if kind in ('task_started', 'turn_started', 'task_complete', 'turn_complete'):
                        saved['retry_signature'] = ''
                    if kind not in ('turn_aborted', 'turn_failed', 'error', 'stream_error'):
                        continue
                    summary = '本轮被中断' if kind == 'turn_aborted' else '运行事件记录报告失败'
                    if reason in ('interrupted', 'replaced', 'user_cancelled', 'user_canceled'):
                        summary += '（取消或新输入打断）'
                    if isinstance(message, str) and message:
                        summary = detect_failure(['■ ' + message], '', False)['error_summary'] or summary
                    event_kind = 'interrupted' if kind == 'turn_aborted' else 'retrying' if kind == 'stream_error' else 'fatal'
                    if kind == 'stream_error':
                        if saved['retry_signature'] == summary:
                            continue
                        saved['retry_signature'] = summary
                        summary = '正在重连 / 重试 · ' + summary
                    event_id = hashlib.sha256('{}:{}:{}'.format(path, key, position).encode()).hexdigest()
                    saved['events'].append(dict(id=event_id, kind=event_kind, summary=summary))
                    saved['events'] = saved['events'][-32:]
                except (ValueError, AttributeError, TypeError):
                    continue
            return list(saved['events']), saved['last_type']
        except OSError:
            return [], ''

    def sample(self, pid, pane_id):
        path = self.root_log(pid, time.monotonic()) if pid else None
        events, last = self.read_path(path, self.cursors.get(pane_id)) if path else ([], '')
        cursor = None
        if path:
            saved = self.files.get(path, {})
            if saved:
                cursor = dict(path=path, device=saved['key'][0], inode=saved['key'][1], offset=saved['offset'])
                self.cursors[pane_id] = cursor
        return events, last, bool(path), cursor

    def resume(self, cursors):
        if isinstance(cursors, dict):
            self.cursors = {key: value for key, value in list(cursors.items())[-200:]
                            if isinstance(key, str) and isinstance(value, dict)
                            and value.get('path', '').endswith('.jsonl')}


class ClaudeWatcher(RolloutWatcher):
    """Map one root Claude process to its own session, never another chat."""
    recover_initial_lifecycle = False
    def root_log(self, pid, now):
        cached = self.roots.get(pid)
        if cached and now - cached[0] < 2:
            return cached[1]
        path = None
        try:
            home = pwd.getpwuid(os.stat('/proc/{}'.format(pid)).st_uid).pw_dir
            config = os.path.join(home, '.claude')
            try:
                with open('/proc/{}/environ'.format(pid), 'rb') as env:
                    for entry in env.read(1048576).split(b'\0'):
                        if entry.startswith(b'CLAUDE_CONFIG_DIR='):
                            config = os.fsdecode(entry.split(b'=', 1)[1])
                            break
            except OSError:
                pass
            with open(os.path.join(config, 'sessions', str(pid) + '.json')) as metadata:
                meta = json.load(metadata)
            sid, cwd = meta.get('sessionId', ''), meta.get('cwd', '')
            if meta.get('pid') == pid and re.fullmatch(r'[a-fA-F0-9-]{36}', sid) and os.path.isabs(cwd):
                candidate = os.path.join(config, 'projects', re.sub(r'[^a-zA-Z0-9-]', '-', cwd), sid + '.jsonl')
                if os.path.isfile(candidate):
                    path = candidate
        except (OSError, ValueError, TypeError, KeyError):
            pass
        self.roots[pid] = (now, path)
        if len(self.roots) > 100:
            self.roots = {pid: self.roots[pid]}
        return path

    @staticmethod
    def decode(item):
        if item.get('isSidechain'):
            return None
        kind, subtype = item.get('type'), item.get('subtype')
        message = item.get('message') or {}
        content = message.get('content', []) if isinstance(message, dict) else []
        text = content if isinstance(content, str) else '\n'.join(c.get('text', '') for c in content if isinstance(c, dict))
        if kind == 'user' and re.search(r'^\[Request interrupted by user', text):
            return 'turn_aborted', '', 'interrupted'
        if kind == 'assistant' and item.get('isApiErrorMessage'):
            return 'error', text, ''
        if kind == 'system' and subtype in ('api_error', 'error'):
            error = item.get('error') or {}
            detail = error.get('message', '') if isinstance(error, dict) else str(error)
            retry = item.get('retryInMs') is not None or item.get('retryAttempt') is not None
            return ('stream_error' if retry else 'error'), detail, ''
        if kind == 'system' and subtype == 'turn_duration':
            return 'turn_complete', '', ''
        if (kind == 'assistant' and isinstance(message, dict) and
                message.get('stop_reason') == 'end_turn' and
                (isinstance(content, str) and content.strip() or
                 isinstance(content, list) and any(isinstance(block, dict) and block.get('type') == 'text' and
                                                  isinstance(block.get('text'), str) and block['text'].strip()
                                                  for block in content))):
            # A thinking-only fragment can also carry end_turn; it is not the
            # final answer or proof that the foreground request has ended.
            return 'turn_complete', '', ''
        if (kind == 'api-request' and item.get('querySource') in (None, 'repl_main_thread') or
                kind == 'user' and not item.get('isMeta') and '<task-notification>' not in text and
                                    (isinstance(content, str) or any(c.get('type') == 'text' for c in content if isinstance(c, dict)))):
            return 'turn_started', '', ''
        return None


class ClaudeBackgroundWatcher:
    """Reduce actual background launches and task notifications from one chat.

    Unlike lifecycle alerts, existing launch/completion records are replayed
    on startup to reconstruct tasks that outlive the foreground turn.
    """
    def __init__(self, clock=time.monotonic, activity_seconds=8.0, max_activity_gap=5.0):
        self.files = {}
        self.clock = clock
        self.activity_seconds = max(1.0, min(30.0, activity_seconds))
        self.max_activity_gap = max(1.0, max_activity_gap)

    def output_activity(self, saved, now, ready):
        """Only continuous growth of a task's own output is new progress.

        An old launch can survive the foreground turn, but its retained file
        is not evidence of work. Replays, file replacement, missing reads and
        long observation gaps establish a new baseline instead of activity.
        """
        previous = saved.get('output_samples', {})
        last = saved.get('output_sample_time')
        continuous = (ready and saved.get('output_sample_ready') and last is not None
                      and 0 < now - last <= self.max_activity_gap)
        samples, active_keys, count, total_size = {}, set(), 0, 0
        activity = saved.setdefault('terminal_activity', {})
        for task_id, task in saved['active'].items():
            outputs = list(task['outputs'])
            if task['kind'] == 'workflow' and task.get('transcript_dir'):
                try:
                    with os.scandir(task['transcript_dir']) as entries:
                        for index, entry in enumerate(entries):
                            if index >= 64:
                                break
                            if entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False):
                                outputs.append(entry.path)
                except OSError:
                    pass
            for output in dict.fromkeys(outputs):
                if count >= 128:
                    break
                count += 1
                key = (task_id, output)
                try:
                    info = os.stat(output)
                except OSError:
                    continue
                identity = (info.st_dev, info.st_ino)
                size = max(0, info.st_size)
                samples[key] = (identity, size)
                total_size += size
                prior = previous.get(key)
                same = continuous and prior is not None and identity == prior[0] and size >= prior[1]
                if same and size > prior[1]:
                    saved['output_observed_bytes'] = saved.get('output_observed_bytes', 0) + size - prior[1]
                if task['kind'] == 'terminal':
                    if same:
                        active_keys.add(key)
                        if size > prior[1]:
                            activity.setdefault(task_id, {}).setdefault('outputs', {})[key] = now
        # Losing/replacing a file must not retain its previous activity proof.
        for task_id, evidence in list(activity.items()):
            if task_id not in saved['active']:
                activity.pop(task_id, None)
                continue
            evidence['outputs'] = {key: stamp for key, stamp in evidence.get('outputs', {}).items()
                                   if key in active_keys}
        saved.update(output_samples=samples, output_sample_time=now, output_sample_ready=ready)
        return total_size

    def activity_summary(self, saved, now):
        active, session_processes, sources, ages = 0, 0, set(), []
        for task_id, evidence in saved.get('terminal_activity', {}).items():
            if saved['active'].get(task_id, {}).get('kind') != 'terminal':
                continue
            signals = [('output', stamp) for stamp in evidence.get('outputs', {}).values()]
            signals += list(evidence.get('process_signals', {}).items())
            recent = [(source, now - stamp) for source, stamp in signals
                      if 0 <= now - stamp <= self.activity_seconds]
            if recent:
                active += 1
                sources.update(source for source, _ in recent)
                ages.extend(age for _, age in recent)
        for evidence in saved.get('session_activity', {}).values():
            recent = [(source, now - stamp) for source, stamp in evidence.get('signals', {}).items()
                      if 0 <= now - stamp <= self.activity_seconds]
            if recent:
                session_processes += 1
                sources.update(source for source, _ in recent)
                ages.extend(age for _, age in recent)
        return dict(background_active_terminals=active,
                    background_active_session_processes=session_processes,
                    background_activity_age=round(min(ages), 3) if ages else None,
                    background_activity_source=next(iter(sources)) if len(sources) == 1 else 'mixed' if sources else '',
                    background_activity_sources=sorted(sources),
                    background_output_observed_bytes=saved.get('output_observed_bytes', 0))

    @staticmethod
    def activity_number(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < float('inf') else None

    def remember_process_activity(self, saved):
        watches = saved.setdefault('process_watches', {})
        observations = []
        for task_id, evidence in saved.get('terminal_activity', {}).items():
            if task_id in saved['active']:
                observations.extend((('task', task_id, row['pid'], row['start']), row, evidence.get('process_signals', {}))
                                    for row in evidence.get('process_rows', []))
        observations.extend((('session', '', pid, start), evidence.get('row', {}), evidence.get('signals', {}))
                            for (pid, start), evidence in saved.get('session_activity', {}).items())
        for key, row, signals in observations:
            for source, stamp in signals.items():
                value = (self.activity_number(row.get('cpu_percent')) if source == 'cpu' else
                         self.activity_number(row.get('gpu_utilization_percent')) if source == 'gpu' else
                         sum(self.activity_number(row.get(field)) or 0 for field in ('disk_read_per_second', 'disk_write_per_second')))
                if value is not None and value > 0:
                    record = watches.setdefault(key, {})
                    record[source] = max(record.get(source, -float('inf')), stamp)

    def process_observation_missing(self, saved, rows, now, discovery=None, complete=False):
        """A resource gap cannot finish previously confirmed background work."""
        watches = saved.setdefault('process_watches', {})
        for key, signals in list(watches.items()):
            kind, task_id, pid, _ = key
            if kind == 'task' and task_id not in saved['active']:
                watches.pop(key, None)
                continue
            row = rows.get(pid) if rows is not None else None
            exited = discovery is not None and (pid not in discovery or 'Z' in discovery.get(pid, (0, ''))[1])
            if rows is not None and row is None and (exited
                                                     or discovery is None and complete):
                watches.pop(key, None)  # Fresh complete coverage confirms exit.
                continue
            if row is None:
                continue
            cpu = self.activity_number(row.get('cpu_percent'))
            disk = [self.activity_number(row.get(field)) for field in ('disk_read_per_second', 'disk_write_per_second')]
            gpu = self.activity_number(row.get('gpu_utilization_percent'))
            gpu_age = self.activity_number(row.get('gpu_sample_age_seconds'))
            quiet = dict(cpu=cpu is not None and cpu < 3,
                         disk=all(value is not None for value in disk) and sum(disk) < 4096,
                         gpu=row.get('gpu_available') is True and gpu_age is not None and gpu_age <= 3 and gpu == 0)
            for source, stamp in list(signals.items()):
                if quiet.get(source, False) and now - stamp > self.activity_seconds:
                    signals.pop(source, None)
            if not signals:
                watches.pop(key, None)
        summary = self.activity_summary(saved, now)
        return bool(watches) and not (summary['background_active_terminals'] or summary['background_active_session_processes'])

    def session_process_activity(self, saved, processes, now, capture_time, continuous, duplicate):
        """Read explicit same-session associations without requiring a live tool.

        The resource collector validates the UID, exact Claude session context
        and process start identity for these rows. Their presence or allocated
        GPU memory alone never becomes work; only new execution samples do.
        """
        previous = saved.get('session_activity', {})
        current = {}
        for row in processes.values():
            if row.get('association') != 'claude_session_env':
                continue
            identity = (row['pid'], row['start'])
            evidence = previous.get(identity)
            if evidence is None or not continuous and not duplicate:
                evidence = dict(signals={}, cpu_streak=0, gpu_sample=None, checking=False)
            current[identity] = evidence
            evidence['row'] = row
            if duplicate:
                continue
            cpu = self.activity_number(row.get('cpu_percent'))
            evidence['cpu_streak'] = evidence['cpu_streak'] + 1 if continuous and cpu is not None and cpu >= 3 else 0
            if evidence['cpu_streak'] >= 2:
                evidence['signals']['cpu'] = capture_time
            gpu_age = self.activity_number(row.get('gpu_sample_age_seconds'))
            gpu = self.activity_number(row.get('gpu_utilization_percent'))
            gpu_id = row.get('gpu_sample_id')
            gpu_observed = row.get('gpu_available') is True and gpu_age is not None and gpu_age <= 3 and gpu is not None and gpu_id is not None
            if (continuous and gpu_observed and evidence.get('gpu_sample') is not None
                    and evidence['gpu_sample'] != gpu_id and gpu > 0):
                evidence['signals']['gpu'] = capture_time - gpu_age
            evidence['gpu_sample'] = gpu_id if gpu_observed else None
            candidate = cpu is not None and cpu >= 3 or gpu_observed and gpu > 0
            evidence['checking'] = bool(candidate and not any(0 <= now - stamp <= self.activity_seconds
                                                              for stamp in evidence['signals'].values()))
        # Missing/reused PIDs lose their previous evidence immediately. The
        # count denotes active processes, never a guessed number of jobs.
        saved['session_activity'] = current
        return len(current), any(evidence['checking'] for evidence in current.values())

    def with_process_activity(self, path, background, resources, now=None, root_pid=None, processes=None):
        """Supplement task progress only through an exact output-file binding.

        Claude's overall CPU and retained processes are deliberately excluded.
        A known task's output inode binds an executing shell; only its verified
        descendant branch can provide CPU, disk or GPU evidence for that task.
        """
        now = self.clock() if now is None else now
        saved = self.files.get(path)
        if saved is None:
            return background
        self.remember_process_activity(saved)
        activity = saved.setdefault('terminal_activity', {})
        resources = resources if isinstance(resources, dict) else {}
        age = self.activity_number(resources.get('sample_age_seconds', 0))
        sample_id, epoch = resources.get('sample_id'), resources.get('identity')
        rows = resources.get('process_activity')
        fresh = (age is not None and age <= 4 and isinstance(sample_id, int) and not isinstance(sample_id, bool)
                 and isinstance(epoch, str) and bool(epoch) and isinstance(rows, list))
        if not fresh:
            for evidence in activity.values():
                evidence.update(process_signals={}, cpu_streak=0, disk_streak=0, process_checking=False)
            saved['process_sample'] = None
            saved['session_activity'] = {}
            return dict(background, background_bound_terminals=0, background_process_observable=False,
                        background_process_checking=False, background_bound_session_processes=0,
                        background_process_unobserved=self.process_observation_missing(saved, None, now),
                        **self.activity_summary(saved, now))
        discovery = processes
        processes = {row['pid']: row for row in rows[:128] if isinstance(row, dict)
                     and isinstance(row.get('pid'), int) and row['pid'] > 0
                     and isinstance(row.get('parent'), int) and isinstance(row.get('start'), str)}
        bindings = {}
        for (task_id, _), (identity, _) in saved.get('output_samples', {}).items():
            if saved['active'].get(task_id, {}).get('kind') == 'terminal':
                bindings.setdefault(identity, set()).add(task_id)
        anchors = {}
        for pid, row in processes.items():
            if pid == root_pid:
                continue
            for output in row.get('output_files', [])[:2]:
                if isinstance(output, dict):
                    anchors.setdefault(pid, set()).update(bindings.get((output.get('device'), output.get('inode')), ()))
        owned = {}
        # A nested task's own output binding takes precedence over an outer
        # shell's branch. Ambiguous identical bindings do not get assigned.
        for pid, row in processes.items():
            cursor, seen = pid, set()
            while cursor in processes and cursor not in seen:
                seen.add(cursor)
                if anchors.get(cursor):
                    if len(anchors[cursor]) == 1:
                        owned.setdefault(next(iter(anchors[cursor])), []).append(row)
                    break
                cursor = processes[cursor]['parent']
        previous = saved.get('process_sample')
        capture_time = now - age
        current = (epoch, sample_id, capture_time)
        duplicate = previous is not None and previous[:2] == current[:2]
        continuous = previous is not None and previous[0] == epoch and 0 < capture_time - previous[2] <= self.max_activity_gap
        session_count, session_checking = self.session_process_activity(saved, processes, now, capture_time, continuous, duplicate)
        for task_id, task in saved['active'].items():
            if task['kind'] != 'terminal':
                continue
            evidence = activity.setdefault(task_id, {})
            branch = owned.get(task_id, [])
            previous_instances = {row['pid']: row['start'] for row in evidence.get('process_rows', [])}
            reused = any(row['pid'] in previous_instances and previous_instances[row['pid']] != row['start'] for row in branch)
            evidence['process_rows'] = branch
            direct = {pid for pid, owners in anchors.items() if owners == {task_id}}
            # Keep only highest anchors, so children inheriting stdout do not
            # make an otherwise stable shell binding change every experiment.
            roots = []
            for pid in direct:
                cursor, seen = processes[pid]['parent'], {pid}
                while cursor in processes and cursor not in seen and cursor not in direct:
                    seen.add(cursor); cursor = processes[cursor]['parent']
                if cursor not in direct:
                    roots.append((pid, processes[pid]['start']))
            binding = tuple(sorted(roots))
            same_binding = binding and evidence.get('process_binding') == binding and not reused
            if not same_binding or not continuous and not duplicate:
                evidence.update(process_signals={}, cpu_streak=0, disk_streak=0, gpu_samples={}, process_checking=False)
            evidence['process_binding'] = binding
            if duplicate or not binding:
                continue
            cpu = sum(value for value in (self.activity_number(row.get('cpu_percent')) for row in branch) if value is not None)
            disk = sum(value for row in branch for value in
                       (self.activity_number(row.get('disk_read_per_second')), self.activity_number(row.get('disk_write_per_second')))
                       if value is not None)
            evidence['cpu_streak'] = evidence.get('cpu_streak', 0) + 1 if same_binding and continuous and cpu >= 3 else 0
            evidence['disk_streak'] = evidence.get('disk_streak', 0) + 1 if same_binding and continuous and disk >= 4096 else 0
            signals = evidence.setdefault('process_signals', {})
            if evidence['cpu_streak'] >= 2:
                signals['cpu'] = capture_time
            if evidence['disk_streak'] >= 2:
                signals['disk'] = capture_time
            gpu = []
            for row in branch:
                gpu_age = self.activity_number(row.get('gpu_sample_age_seconds'))
                value = self.activity_number(row.get('gpu_utilization_percent'))
                gpu_id = row.get('gpu_sample_id')
                if row.get('gpu_available') is True and gpu_age is not None and gpu_age <= 3 and value is not None and gpu_id is not None:
                    gpu.append((row['pid'], row['start'], gpu_id, value, capture_time - gpu_age))
            previous_gpu = evidence.get('gpu_samples', {})
            observed_gpu = [(value, stamp) for pid, start, gid, value, stamp in gpu
                            if (pid, start) in previous_gpu and previous_gpu[(pid, start)] != gid]
            if same_binding and continuous and any(value > 0 for value, _ in observed_gpu):
                signals['gpu'] = max(stamp for value, stamp in observed_gpu if value > 0)
            evidence['gpu_samples'] = {(pid, start): gid for pid, start, gid, _, _ in gpu}
            evidence['process_checking'] = bool((cpu >= 3 or disk >= 4096 or any(value > 0 for _, _, _, value, _ in gpu))
                                                and not any(0 <= now - stamp <= self.activity_seconds for stamp in signals.values()))
        if not duplicate:
            saved['process_sample'] = current
        self.remember_process_activity(saved)
        unobserved = self.process_observation_missing(saved, processes, now, discovery, resources.get('tree_complete') is True)
        return dict(background, background_bound_terminals=len(owned), background_process_observable=bool(owned),
                    background_bound_session_processes=session_count,
                    background_process_unobserved=unobserved,
                    background_process_checking=session_checking or any(activity.get(task_id, {}).get('process_checking', False) for task_id in owned),
                    **self.activity_summary(saved, now))

    @staticmethod
    def text(content):
        if isinstance(content, str):
            return content
        return '\n'.join(c.get('text', '') for c in content if isinstance(c, dict) and isinstance(c.get('text'), str)) if isinstance(content, list) else ''

    @staticmethod
    def foreground_event(saved, item, position):
        decoded = ClaudeWatcher.decode(item)
        if decoded is None or position < saved.get('foreground_position', -1):
            return
        kind = decoded[0]
        if kind not in ('turn_started', 'task_started', 'stream_error', 'turn_complete',
                        'task_complete', 'turn_aborted', 'turn_failed', 'error'):
            return
        saved.update(foreground=kind in ('turn_started', 'task_started', 'stream_error'),
                     foreground_position=position, foreground_event=kind)

    def foreground_tail(self, saved, path, info):
        """Observe recent foreground events independently of background replay.

        Offsets, rather than a timeout or replay order, determine which event
        wins. A trailing partial record or an unobserved gap remains unknown.
        """
        signature = (info.st_size, info.st_mtime_ns)
        if signature == saved.get('foreground_signature'):
            return
        saved['foreground_signature'] = signature
        prior_until = saved.get('foreground_checked_until', 0)
        prior_observable = saved.get('foreground_observable', False)
        try:
            with open(path, 'rb') as log:
                current = os.fstat(log.fileno())
                if (current.st_dev, current.st_ino) != saved['key'] or current.st_size < info.st_size:
                    saved['foreground_observable'] = False
                    return
                start = max(0, info.st_size - 1048576)
                partial_start = False
                if start:
                    log.seek(start - 1)
                    partial_start = log.read(1) != b'\n'
                log.seek(start)
                data = log.read(info.st_size - start)
            position, latest, last_bad = start, None, -1
            pieces = data.split(b'\n')
            truncated_record = partial_start and len(pieces) == 1
            if partial_start:
                position += len(pieces.pop(0)) + 1
            for line in pieces:
                if line.strip():
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict):
                            raise ValueError('not a record')
                        if ClaudeWatcher.decode(item) is not None:
                            latest = (position, item)
                    except (ValueError, TypeError, AttributeError):
                        last_bad = position
                position += len(line) + 1
            if latest is not None:
                self.foreground_event(saved, latest[1], latest[0])
            saved['foreground_observable'] = not truncated_record and (last_bad < 0 or latest is not None and latest[0] > last_bad) and (
                latest is not None or start == 0 or start <= prior_until and prior_observable)
            saved['foreground_checked_until'] = info.st_size
        except OSError:
            saved['foreground_observable'] = False

    def consume(self, saved, item, path):
        if item.get('isSidechain'):
            return
        message = item.get('message') or {}
        blocks = message.get('content', []) if isinstance(message, dict) else []
        kind = item.get('type')
        self.foreground_event(saved, item, saved.get('record_position', 0))
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                name, args, tool_id = block.get('name'), block.get('input') or {}, block.get('id')
                args = args if isinstance(args, dict) else {}
                if name in ('Agent', 'Task', 'Bash'):
                    if name in ('Agent', 'Task') and tool_id and tool_id not in saved['tools'] and 'tool:' + tool_id not in saved['completed']:
                        saved['active']['tool:' + tool_id] = dict(kind='agent', outputs=[])
                    saved['tools'][tool_id] = dict(kind='agent' if name in ('Agent', 'Task') else 'terminal', background=bool(args.get('run_in_background')))
                elif name in ('TaskOutput', 'TaskStop'):
                    saved['tools'][tool_id] = dict(kind=name, task_id=args.get('task_id') or args.get('taskId'))
                elif tool_id:
                    saved['tools'][tool_id] = dict(kind='workflow' if name == 'Workflow' else 'unknown', name=name,
                                                  background=bool(args.get('run_in_background')))
            if block.get('type') != 'tool_result':
                continue
            tool = saved['tools'].get(block.get('tool_use_id'), {})
            if block.get('tool_use_id'):
                provisional_id = 'tool:' + block['tool_use_id']
                saved['active'].pop(provisional_id, None)
                saved['completed'].add(provisional_id)
            result = item.get('toolUseResult') or {}
            if not isinstance(result, dict):
                result = {}
            text = self.text(block.get('content'))
            # Newer Claude Agent calls may choose asynchronous execution even
            # without a run_in_background argument. The result is authoritative;
            # a plain synchronous agentId remains a completed foreground call.
            asynchronous = tool.get('background') or result.get('isAsync') is True or result.get('status') == 'async_launched'
            task_type = result.get('taskType')
            workflow = task_type == 'local_workflow' or tool.get('kind') == 'workflow'
            persistent = result.get('persistent') is True
            task_id = result.get('backgroundTaskId') or (result.get('agentId') if asynchronous else None)
            if not task_id and (asynchronous or persistent):
                task_id = result.get('taskId') or result.get('task_id')
            if not task_id and asynchronous:
                match = re.search(r'(?:agentId|task[_ ]?id|background.{0,15}ID)\s*[:=]\s*([a-zA-Z0-9_-]+)', text, re.I)
                task_id = match.group(1) if match else None
            if task_id and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', str(task_id)) and task_id not in saved['completed']:
                kind = ('workflow' if workflow else 'terminal' if result.get('backgroundTaskId') or task_type in ('local_bash', 'bash', 'terminal')
                        else tool.get('kind', 'unknown'))
                outputs = [p for p in re.findall(r'/(?:private/)?tmp/[^\s<>\"]+\.output', text + '\n' + str(result.get('stdout', '')))
                           if os.path.basename(p) == str(task_id) + '.output']
                for field in ('outputFile', 'output_file'):
                    output = result.get(field)
                    if isinstance(output, str) and os.path.isabs(output) and os.path.basename(output) == str(task_id) + '.output':
                        outputs.append(output)
                if kind == 'agent':
                    outputs.append(os.path.join(os.path.dirname(path), 'subagents', 'agent-' + str(task_id) + '.jsonl'))
                transcript_dir = result.get('transcriptDir')
                saved['active'][task_id] = dict(kind=kind, outputs=list(dict.fromkeys(outputs)),
                                               transcript_dir=transcript_dir if workflow and isinstance(transcript_dir, str) and os.path.isabs(transcript_dir) else None)
            nested = result.get('task') or {}
            if not isinstance(nested, dict):
                nested = {}
            terminal_status = result.get('status') or nested.get('status')
            finished = terminal_status in ('completed', 'failed', 'killed', 'stopped', 'cancelled', 'canceled')
            finished_task = task_id or result.get('taskId') or result.get('task_id')
            if finished_task and finished:
                saved['active'].pop(finished_task, None); saved['completed'].add(finished_task)
            if tool.get('kind') in ('TaskOutput', 'TaskStop') and (finished or (tool['kind'] == 'TaskStop' and not block.get('is_error'))):
                finished_id = tool.get('task_id') or nested.get('task_id') or nested.get('taskId')
                if finished_id:
                    saved['active'].pop(finished_id, None); saved['completed'].add(finished_id)
        # Completion can be queued before Claude starts another foreground turn.
        text = self.text(blocks) + '\n' + self.text(item.get('content'))
        for notification in re.findall(r'<task-notification>(.*?)</task-notification>', text, re.S) if item.get('type') in ('user', 'queue-operation', 'system') else []:
            task = re.search(r'<task-id>(.*?)</task-id>', notification, re.S)
            status = re.search(r'<status>(.*?)</status>', notification, re.S)
            if task and status and status.group(1).strip() in ('completed', 'failed', 'killed', 'stopped', 'cancelled', 'canceled'):
                task_id = task.group(1).strip()
                saved['active'].pop(task_id, None); saved['completed'].add(task_id)
        data = item.get('data') or {}
        if item.get('type') == 'progress' and isinstance(data, dict) and data.get('type') in ('agent_progress', 'bash_progress'):
            saved['revision'] += 1
        if len(saved['tools']) > 1024:
            saved['tools'] = dict(list(saved['tools'].items())[-512:])

    def read_path(self, path):
        if not path:
            return dict(background_count=0, background_agents=0, background_terminals=0, background_checking=False,
                        background_observable=False, background_output_bytes=0, background_revision=0, foreground_running=None,
                        background_active_terminals=0, background_active_session_processes=0,
                        background_activity_age=None, background_activity_source='',
                        background_activity_sources=[], background_output_observed_bytes=0)
        try:
            info = os.stat(path); key = (info.st_dev, info.st_ino)
            saved = self.files.get(path)
            if not saved or saved['key'] != key or info.st_size < saved['offset']:
                saved = dict(key=key, offset=0, buffer=b'', active={}, completed=set(), tools={}, revision=0, foreground=None,
                             foreground_position=-1, foreground_event=None, foreground_observable=False,
                             foreground_checked_until=0, history_error=False)
                self.files[path] = saved
            # CodexBackgroundWatcher supplies its own lifecycle reducer and
            # keeps its existing read path; this tail protocol is Claude-only.
            claude_foreground = type(self).consume is ClaudeBackgroundWatcher.consume
            if claude_foreground:
                self.foreground_tail(saved, path, info)
            with open(path, 'rb') as log:
                log.seek(saved['offset'])
                data = saved['buffer'] + log.read(8388608)
                saved['offset'] = log.tell()
            position = saved['offset'] - len(data)
            pieces = data.split(b'\n'); saved['buffer'] = pieces.pop()
            # A large unfinished media record must not grow without bound.
            if len(saved['buffer']) > 16777216:
                saved['buffer'] = b''
                saved['history_error'] = True
            for line in pieces:
                if not line.strip():
                    position += len(line) + 1
                    continue
                try:
                    saved['record_position'] = position
                    self.consume(saved, json.loads(line), path)
                except (ValueError, TypeError, AttributeError):
                    saved['history_error'] = True
                position += len(line) + 1
            if claude_foreground and saved['offset'] >= info.st_size and saved['buffer']:
                try:
                    item = json.loads(saved['buffer'])
                except (ValueError, TypeError):
                    pass
                else:
                    saved['record_position'] = saved['offset'] - len(saved['buffer'])
                    try:
                        self.consume(saved, item, path)
                    except (ValueError, TypeError, AttributeError):
                        saved['history_error'] = True
                    saved['buffer'] = b''
            complete = saved['offset'] >= info.st_size and not saved['buffer'] and not saved['history_error']
            if claude_foreground and complete:
                saved['foreground_observable'] = True
            active = list(saved['active'].values())
            now = self.clock()
            output_bytes = self.output_activity(saved, now, complete)
            if len(self.files) > 100:
                self.files = {path: saved}
            return dict(background_count=len(active), background_agents=sum(x['kind'] == 'agent' for x in active),
                        background_terminals=sum(x['kind'] == 'terminal' for x in active),
                        background_log_workflows=sum(x['kind'] == 'workflow' for x in active),
                        background_checking=not complete if claude_foreground else saved['offset'] < info.st_size,
                        background_observable=not saved['history_error'] if claude_foreground else True,
                        background_output_bytes=output_bytes, background_revision=saved['revision'],
                        foreground_running=saved['foreground'] if not claude_foreground or saved['foreground_observable'] else None,
                        foreground_event=saved.get('foreground_event'),
                        foreground_event_offset=saved.get('foreground_position'),
                        foreground_observable=saved.get('foreground_observable') if claude_foreground else None,
                        **self.activity_summary(saved, now))
        except OSError:
            return self.read_path(None)


def apply_claude_background(result, background):
    screen_state = result.get('foreground_screen_state', result['state'])
    ui_workflows = result.get('background_workflows', 0)
    log_workflows = background.get('background_log_workflows', 0)
    workflows = max(ui_workflows, log_workflows)
    ui_agents = result.get('background_ui_agents', 0)
    log_agents = background.get('background_agents', 0)
    background = dict(background, background_count=background['background_count'] + max(0, ui_workflows - log_workflows) + max(0, ui_agents - log_agents),
                      background_agents=max(log_agents, ui_agents), background_workflows=workflows)
    result.update(background)
    result['foreground_input_ready'] = bool(background.get('foreground_observable') is True
                                            and background.get('foreground_running') is False
                                            and background.get('foreground_event') == 'turn_complete'
                                            and result['state'] in ('idle', 'running', 'unknown')
                                            and not result.get('error_kind'))
    if result['state'] in ('idle', 'running', 'unknown') and not result.get('error_kind'):
        foreground = background.get('foreground_running')
        verified = background.get('foreground_observable') is True
        if background.get('foreground_observable') is False:
            foreground = None
        completed = verified and foreground is False and background.get('foreground_event') == 'turn_complete'
        if verified and foreground is True:
            result.update(state='running', status='运行中', working=result.get('working') or '主对话尚未结束')
        elif ui_agents:
            # A current footer describes unfinished children after the root's
            # final answer; that root completion cannot finish its children.
            result.update(state='running', status='子智能体运行中（{}）'.format(background['background_agents']),
                          working='等待 {} 个后台子智能体完成'.format(background['background_agents']))
        elif background.get('background_active_terminals', 0) or background.get('background_active_session_processes', 0):
            status = '后台实验运行中' if background.get('background_active_session_processes', 0) else '后台实验运行中（{}）'.format(background['background_active_terminals'])
            result.update(state='running', status=status,
                          working='前台可输入，已确认后台任务仍有进展' if result['foreground_input_ready'] else '已确认后台任务仍有进展')
        elif background.get('background_process_checking'):
            result.update(state='unknown', status='正在核对后台实验', working='已关联到后台任务，等待连续资源样本')
        elif background['background_checking']:
            if completed or result['state'] != 'running':
                result.update(state='unknown', status='正在核对后台任务', working='')
        elif background.get('background_agents', 0) or log_workflows or workflows and not completed:
            if workflows and not background.get('background_agents', 0):
                result.update(state='running', status='后台工作流运行中（{}）'.format(workflows),
                              working='等待 {} 个后台工作流完成'.format(workflows))
            elif result['state'] != 'running':
                result.update(state='running', status='子智能体运行中（{}）'.format(background['background_agents']),
                              working='等待子智能体完成')
            else:
                result['status'] = '运行中 · 子智能体 {}'.format(background.get('background_agents', 0)) if not workflows else result['status']
        elif background.get('background_process_unobserved'):
            result.update(state='unknown', status='后台状态待核实', working='前台可输入，后台资源采集暂缺失' if result['foreground_input_ready'] else '后台资源采集暂缺失')
        elif completed and workflows:
            # An old visible workflow line cannot overturn a newer root-log
            # completion, but unverified workflow work must not become idle.
            result.update(state='unknown', status='前台已结束，后台工作流待核实', working='')
        elif completed:
            result.update(state='idle', status='等待输入', working='')
        elif background.get('foreground_observable') is False and screen_state != 'running':
            result.update(state='unknown', status='前台状态待核实', working='')
        elif foreground is True:
            result.update(state='running', status='运行中', working=result.get('working') or '主对话尚未结束')
        elif foreground is False and screen_state != 'running':
            result.update(state='idle', status='等待输入', working='')
        elif background.get('background_observable') is False and result['state'] == 'idle':
            result.update(state='unknown', status='后台状态待核实')
    return result


class ClaudeCompletionGuard:
    """Confirm a stable input-ready page; retained terminal handles are metadata."""
    def __init__(self, seconds=2):
        self.seconds, self.panes = seconds, {}

    def apply(self, result, pane_id, pid, now):
        saved = self.panes.get(pane_id)
        if saved is None or saved['pid'] != pid:
            saved = dict(pid=pid, running=False, idle_since=None, content=None)
            self.panes[pane_id] = saved
        state = result['state']
        content = result.get('activity') or '\n'.join(result.get('preview', []))
        if state == 'running':
            saved.update(running=True, idle_since=None)
        elif state == 'idle' and saved['running']:
            if saved['idle_since'] is None or content != saved['content']:
                saved['idle_since'] = now
            if now - saved['idle_since'] < self.seconds:
                result.update(state='running', status='正在确认输入状态', working='确认正文已稳定且当前可输入')
            else:
                saved.update(running=False, idle_since=None)
        elif state != 'idle':
            saved.update(running=False, idle_since=None)
        saved['content'] = content
        return result


def codex_terminal_count(processes, root_pid, read_stat=None):
    """Count live terminal sessions, not shells and all their child processes."""
    if processes is None or root_pid not in processes:
        return 0, False
    if read_stat is None:
        def read_stat(pid):
            with open('/proc/{}/stat'.format(pid)) as stat:
                fields = stat.read().rsplit(')', 1)[1].split()
            return int(fields[3]), int(fields[4])  # OS session, controlling TTY
    try:
        root_session, root_tty = read_stat(root_pid)
    except (OSError, ValueError, IndexError):
        return 0, False
    processes = as_snapshot(processes)
    pending, seen, terminals, complete = list(processes.children.get(root_pid, ())), set(), set(), True
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        pending.extend(processes.children.get(pid, ()))
        entry = processes.get(pid)
        if not entry or 'Z' in entry[1] or re.search('[Tt]', entry[1]):
            continue
        try:
            session, tty = read_stat(pid)
        except FileNotFoundError:
            continue  # A command ended between ps and stat.
        except (OSError, ValueError, IndexError):
            complete = False
            continue
        if tty and tty != root_tty:
            terminals.add(('tty', tty))
        elif not tty and session != root_session and session == pid and os.path.basename(entry[2]) in ('sh', 'bash', 'zsh', 'dash', 'fish'):
            terminals.add(('session', session))
    return len(terminals), complete


class CodexBackgroundWatcher(ClaudeBackgroundWatcher):
    """Read child lifecycle logs opened by this exact Codex process.

    Parent-thread metadata establishes ownership, including nested children.
    Nothing is inferred from prose, task names, or log modification times.
    """
    def __init__(self):
        super().__init__()
        self.process_logs = {}

    def consume(self, saved, item, path):
        if item.get('type') != 'event_msg':
            return
        kind = (item.get('payload') or {}).get('type')
        if kind in ('task_started', 'turn_started'):
            saved['foreground'] = True
        elif kind in ('task_complete', 'turn_complete', 'turn_aborted', 'turn_failed', 'error'):
            saved['foreground'] = False

    def child_logs(self, pid, root_path, now):
        cached = self.process_logs.get(pid)
        if cached and cached[1] == root_path and now - cached[0] < 2:
            return cached[2], cached[3]
        paths, observed = {}, True
        try:
            with open(root_path, 'rb') as root:
                metadata = json.loads(root.readline(1048576)).get('payload', {})
            root_id = metadata.get('id') or metadata.get('session_id')
            if not root_id:
                return [], False
            for fd in os.listdir('/proc/{}/fd'.format(pid)):
                try:
                    path = os.readlink('/proc/{}/fd/{}'.format(pid, fd))
                    if not os.path.basename(path).startswith('rollout-') or not path.endswith('.jsonl') or path == root_path:
                        continue
                    with open(path, 'rb') as log:
                        item = json.loads(log.readline(1048576))
                    meta = item.get('payload', {})
                    source = meta.get('source')
                    spawn = source.get('subagent', {}).get('thread_spawn', {}) if isinstance(source, dict) else {}
                    child_id, parent_id = meta.get('id') or meta.get('session_id'), spawn.get('parent_thread_id')
                    if item.get('type') == 'session_meta' and child_id and parent_id:
                        paths[child_id] = (parent_id, path)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, TypeError, AttributeError):
                    observed = False
            owned, found = {root_id}, []
            for _ in range(len(paths)):
                added = [(cid, path) for cid, (parent, path) in paths.items() if parent in owned and cid not in owned]
                if not added:
                    break
                for cid, path in added:
                    owned.add(cid); found.append(path)
        except (OSError, ValueError, TypeError, AttributeError):
            return [], False
        self.process_logs[pid] = (now, root_path, found, observed)
        if len(self.process_logs) > 100:
            self.process_logs = {pid: self.process_logs[pid]}
        return found, observed

    def sample(self, pid, root_path, processes, now):
        paths, observable = self.child_logs(pid, root_path, now) if root_path else ([], False)
        total = len(paths) if observable else None
        running, checking = 0, False
        for path in paths:
            state = self.read_path(path)
            checking = checking or state['background_checking']
            if not state['background_observable'] or state['foreground_running'] is None:
                observable = False
            running += state['foreground_running'] is True
        terminals, terminal_observable = codex_terminal_count(processes, pid)
        return dict(background_agents=running, background_agents_total=total,
                    background_terminals=terminals, background_count=running + terminals,
                    background_agents_observable=observable and not checking,
                    background_terminals_observable=terminal_observable,
                    background_observable=observable and terminal_observable,
                    background_checking=checking)


def apply_codex_background(result, background):
    result.update(background)
    # Open terminal handles and online agents describe resources, not active work.
    # Only verified in-progress child turns may supplement the foreground UI.
    if (result['state'] in ('idle', 'running') and not result.get('error_kind')
            and background.get('background_agents_observable')
            and not background.get('background_checking') and background.get('background_agents', 0)):
        result.update(state='running', status='子智能体运行中（{}）'.format(background['background_agents']))
    return result


ROLLOUTS = RolloutWatcher()
CLAUDE_LOGS = ClaudeWatcher()
CLAUDE_BACKGROUND = ClaudeBackgroundWatcher()
CODEX_BACKGROUND = CodexBackgroundWatcher()
CLAUDE_COMPLETIONS = ClaudeCompletionGuard()
CODEX_COMPLETIONS = ClaudeCompletionGuard()
# Limit history work to leave room for the live 0.5s remote refresh.
# Local reads use the larger default.
USAGE_LOGS = UsageLogWatcher(max_bytes=262144)
RESULT_LOGS = ResultLogWatcher()
RESOURCE_SAMPLES = AsyncProcessResources()
KNOWN_AGENTS = {}


def snapshot(socket, selected):
    def tmux(*args):
        p = subprocess.run(['tmux'] + (['-S', socket] if socket else []) + list(args), text=True, capture_output=True, timeout=3)
        if p.returncode:
            raise RuntimeError(p.stderr.strip() or 'tmux 读取失败')
        return p.stdout
    started = time.monotonic()
    fields = '#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_dead}\t#{pane_pid}\t#{pane_dead_status}\t#{pane_in_mode}\t#{pane_height}'
    processes = as_snapshot(read_processes())
    panes, listed, owners, roots, claude_paths, session_contexts, pane_heights = [], [], {}, {}, {}, {}, {}
    for line in tmux('list-panes', '-a', '-F', fields).splitlines():
        bits = line.split('\t')
        if len(bits) not in (10, 11):
            continue
        if len(bits) == 11:
            try:
                height = int(bits.pop())
                if 1 <= height <= 4096:
                    pane_heights[bits[3]] = height
            except ValueError:
                continue
        session, window, pane, pane_id, command, cwd, dead, root_pid, exit_status, in_mode = bits
        try:
            pane_root = int(root_pid)
        except ValueError:
            continue
        if pane_root not in roots:
            roots[pane_root] = find_agent(processes, pane_root)
        process = roots[pane_root]
        if process[0]:
            owners.setdefault(process[0], set()).add(session)
        listed.append((bits, process))
    # All listed owners count, even when only one of their sessions is watched.
    # Listing metadata does not capture unselected screens or open their logs.
    for bits, process in listed:
        session, window, pane, pane_id, command, cwd, dead, root_pid, exit_status, in_mode = bits
        if selected and session not in selected:
            continue
        try:
            height = pane_heights.get(pane_id)
            # One bounded capture retains assistant anchors above a small
            # viewport. Only current rows drive state, errors and UI preview.
            context = tmux('capture-pane', '-p', '-S', '-256', '-t', pane_id) if height else tmux('capture-pane', '-p', '-t', pane_id)
            screen = '\n'.join(context.splitlines()[-height:]) + '\n' if height else context
        except RuntimeError:
            # Pane may disappear between listing and capture.
            continue
        pid, process_state, agent = process
        if pid:
            KNOWN_AGENTS[pane_id] = agent
        agent = agent or KNOWN_AGENTS.get(pane_id) or (command if command in ('codex', 'claude') else '')
        analyser = analyse_claude_screen if agent == 'claude' else analyse_screen
        result = analyser(screen, agent or command, dead == '1',
                                None if pid is None else bool(pid), process_state, exit_status, in_mode == '1', processes is not None,
                                dialogue_context=context)
        if agent == 'claude':
            result['foreground_screen_state'] = result['state']
        watcher = CLAUDE_LOGS if agent == 'claude' else ROLLOUTS
        events, last_event, has_events, lifecycle_cursor = watcher.sample(pid, pane_id)
        if last_event in ('task_started', 'turn_started', 'stream_error') and result['state'] in ('idle', 'unknown'):
            result.update(state='running', status='重连 / 重试中' if last_event == 'stream_error' else '运行中（事件记录）')
            if last_event == 'stream_error' and events:
                result.update(issue(events[-1]['summary'], 'retrying'))
        # Never let an old completion record override a newer live spinner.
        if last_event in ('turn_aborted', 'turn_failed', 'error') and result['state'] in ('idle', 'unknown'):
            aborted = last_event == 'turn_aborted'
            result.update(state='interrupted' if aborted else 'error', status='本轮已中断' if aborted else '运行报错',
                          **issue('运行事件确认本轮已中断' if aborted else '运行事件确认本轮失败', 'interrupted' if aborted else 'fatal'))
        if events and last_event in ('turn_aborted', 'turn_failed', 'error', 'stream_error') and result.get('error_kind') == events[-1]['kind']:
            result['issue_event_id'] = events[-1]['id']
        if agent == 'claude' and pid:
            claude_path = CLAUDE_LOGS.root_log(pid, time.monotonic())
            claude_paths[pane_id] = (claude_path, CLAUDE_BACKGROUND.read_path(claude_path))
            if claude_path:
                name = os.path.basename(claude_path)
                sid = name[:-6] if name.endswith('.jsonl') else ''
                if re.fullmatch(r'[a-fA-F0-9-]{36}', sid):
                    session_contexts[pid] = sid
        elif agent == 'codex' and pid:
            apply_codex_background(result, CODEX_BACKGROUND.sample(pid, ROLLOUTS.root_log(pid, time.monotonic()), processes, time.monotonic()))
            CODEX_COMPLETIONS.apply(result, pane_id, pid, time.monotonic())
        usage_path = watcher.root_log(pid, time.monotonic()) if pid and agent in ('codex', 'claude') else None
        panes.append(dict(session=session, window=window, pane=pane, pane_id=pane_id,
                          command=command, agent=agent, cwd=cwd, screen=screen,
                          fingerprint=hashlib.sha256(screen.encode()).hexdigest(),
                          process_id=pid, event_source=has_events, lifecycle_events=events, lifecycle_cursor=lifecycle_cursor,
                          telemetry=USAGE_LOGS.sample(usage_path, agent), **RESULT_LOGS.sample(usage_path, agent), **result))
    resource_args = dict(session_contexts=session_contexts) if session_contexts else {}
    resources = RESOURCE_SAMPLES.sample_all([pane.get('process_id') for pane in panes], processes, **resource_args)
    for pane in panes:
        value = resources.get(pane.get('process_id'))
        if value is not None:
            value = dict(value)
            count = len(owners.get(pane.get('process_id'), ()))
            value.update(attribution='shared' if count > 1 else 'exclusive' if count else 'unattributed',
                         attribution_source='tmux_process_tree')
            if count > 1:
                value.update(scope='shared_process', shared_sessions=count)
        pane['resources'] = value
        if pane.get('agent') == 'claude' and pane.get('process_id'):
            path, background = claude_paths[pane['pane_id']]
            background = CLAUDE_BACKGROUND.with_process_activity(path, background, value, root_pid=pane['process_id'], processes=processes)
            apply_claude_background(pane, background)
            CLAUDE_COMPLETIONS.apply(pane, pane['pane_id'], pane['process_id'], time.monotonic())
    return dict(panes=panes, sampled_at=time.time(), collection_ms=round((time.monotonic() - started) * 1000))


def main():
    socket = sys.argv[1]
    selected = set(json.loads(sys.argv[2])) if len(sys.argv) > 2 else set()
    stream = len(sys.argv) > 3 and sys.argv[3] == '--stream'
    interval = max(.2, float(sys.argv[4])) if stream else 0
    if stream and len(sys.argv) > 5:
        try:
            ROLLOUTS.resume(json.loads(sys.argv[5]))
            CLAUDE_LOGS.resume(json.loads(sys.argv[5]))
        except ValueError:
            pass
    sequence = 0
    pending = b''
    while True:
        started = time.monotonic()
        try:
            frame = snapshot(socket, selected)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
            frame = dict(error=str(e), sampled_at=time.time())
        sequence += 1
        frame['sequence'] = sequence
        print(json.dumps(frame, ensure_ascii=False), flush=True)
        if not stream:
            return 1 if 'error' in frame else 0
        # Commands on stdin change the subscription without reconnecting.
        deadline = started + interval
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], max(0, deadline - time.monotonic()))
            if ready:
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    return 0
                pending += data
                while b'\n' in pending:
                    command, pending = pending.split(b'\n', 1)
                    value = json.loads(command)
                    selected = set(value.get('selected', []))
            if time.monotonic() >= deadline:
                break


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (BrokenPipeError, KeyboardInterrupt):
        pass
