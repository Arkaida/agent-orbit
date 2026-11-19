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

PROMPT = re.compile(r'^\s*[›»]\s*(.*)$')
RUNNING = re.compile(r'^\s*(?:[•●◦]\s*)?.*\([^\n]*esc to interrupt\)', re.I)
RECOVERING = re.compile(r'^\s*[•●◦]\s*(?:Reconnecting|Retrying|重新连接|重试)(?:\.{1,3})?(?:\s|$)', re.I)
FOOTER = re.compile(r'\bGPT[- ]|\bMain \[|\? for shortcuts|Goal (?:paused|stalled)|warnings? · f2', re.I)
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


def analyse_screen(screen, command='codex', dead=False, codex_alive=None, process_state='', exit_status='', in_mode=False, process_check=True):
    lines = screen.splitlines()
    # Input is below the transcript, directly above the model/footer. Never
    # interpret an arbitrary transcript mention of "approval" as an approval UI.
    prompt_index = None
    for i in range(len(lines) - 1, max(-1, len(lines) - 16), -1):
        if PROMPT.match(lines[i]) and (any(FOOTER.search(x) for x in lines[i + 1:]) or i >= len(lines) - 5):
            prompt_index = i
            break
    tail = lines[max(0, len(lines) - 16):]
    running = next((x.strip() for x in reversed(tail) if RUNNING.match(x) or RECOVERING.match(x)), '')
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
    return dict(state=state, status=status, working=running, preview=body[-3:], activity='\n'.join(body[-3:]), **failure)


def analyse_claude_screen(screen, command='claude', dead=False, agent_alive=None,
                         process_state='', exit_status='', in_mode=False, process_check=True):
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
                background_workflows=workflows, background_progress=progress, **failure)


def read_processes():
    try:
        result = subprocess.run(['ps', '-eo', 'pid=,ppid=,stat=,comm='], text=True, capture_output=True, timeout=2)
        if result.returncode:
            return None
        return {int(p): (int(parent), state, name) for p, parent, state, name in
                (line.split(None, 3) for line in result.stdout.splitlines() if line.strip())}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def find_codex(processes, root_pid):
    if processes is None:
        return None, ''
    # Choose the nearest Codex descendant, never a subagent under that process.
    children = {}
    for pid, (parent, state, name) in processes.items():
        children.setdefault(parent, []).append(pid)
    pending, seen = [root_pid], set()
    while pending:
        pid = pending.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        entry = processes.get(pid)
        if entry and os.path.basename(entry[2]) == 'codex' and 'Z' not in entry[1]:
            return pid, entry[1]
        pending.extend(children.get(pid, []))
    return 0, ''


def find_agent(processes, root_pid):
    if processes is None:
        return None, '', ''
    children = {}
    for pid, (parent, state, name) in processes.items():
        children.setdefault(parent, []).append(pid)
    pending, seen, stopped = [root_pid], set(), []
    while pending:
        pid = pending.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        entry = processes.get(pid)
        if entry:
            agent = os.path.basename(entry[2])
            if agent in ('codex', 'claude') and 'Z' not in entry[1]:
                if not re.search('[Tt]', entry[1]):
                    return pid, entry[1], agent
                stopped.append((pid, entry[1], agent))
                # A nested worker is not the foreground root agent.
                continue
        pending.extend(children.get(pid, []))
    return stopped[0] if stopped else (0, '', '')


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
                      tool_completions=None, tools_complete=False)
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
                if not isinstance(block, dict) or block.get('type') != 'tool_result':
                    continue
                tid = block.get('tool_use_id')
                self.tool_result(saved, [('tool', tid)] if isinstance(tid, str) and tid else [])

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


class RolloutWatcher:
    """Read only new lifecycle events from an unambiguous CLI root log.

    No session-directory scans or cwd guesses: map logs through open process
    descriptors, reject subagents, and skip existing history on first contact.
    """
    def __init__(self):
        self.roots, self.files = {}, {}
        self.cursors = {}

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
                self.files[path] = saved
                if not resumed:
                    if len(self.files) > 100:
                        self.files = {path: self.files[path]}
                    return [], ''
            if saved['key'] != key or info.st_size < saved['offset']:
                saved = dict(key=key, offset=info.st_size, buffer=b'', events=[], last_type='', retry_signature='')
                self.files[path] = saved
                return [], ''
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

    def decode(self, item):
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
        if kind == 'api-request' or (kind == 'user' and not item.get('isMeta') and '<task-notification>' not in text and
                                    (isinstance(content, str) or any(c.get('type') == 'text' for c in content if isinstance(c, dict)))):
            return 'turn_started', '', ''
        return None


class ClaudeBackgroundWatcher:
    """Reduce actual background launches and task notifications from one chat.

    Unlike lifecycle alerts, existing launch/completion records are replayed
    on startup to reconstruct tasks that outlive the foreground turn.
    """
    def __init__(self):
        self.files = {}

    @staticmethod
    def text(content):
        if isinstance(content, str):
            return content
        return '\n'.join(c.get('text', '') for c in content if isinstance(c, dict) and isinstance(c.get('text'), str)) if isinstance(content, list) else ''

    def consume(self, saved, item, path):
        if item.get('isSidechain'):
            return
        message = item.get('message') or {}
        blocks = message.get('content', []) if isinstance(message, dict) else []
        kind = item.get('type')
        if kind == 'api-request' and item.get('querySource') in (None, 'repl_main_thread'):
            saved['foreground'] = True
        elif kind == 'system' and item.get('subtype') == 'turn_duration':
            saved['foreground'] = False
        elif kind == 'assistant' and item.get('isApiErrorMessage'):
            saved['foreground'] = False
        elif kind == 'user' and not item.get('isMeta'):
            text = self.text(blocks)
            if text and '<task-notification>' not in text and not text.startswith('[Request interrupted by user'):
                saved['foreground'] = True
            elif text.startswith('[Request interrupted by user'):
                saved['foreground'] = False
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                name, args, tool_id = block.get('name'), block.get('input') or {}, block.get('id')
                if name in ('Agent', 'Task', 'Bash'):
                    if name in ('Agent', 'Task') and tool_id and tool_id not in saved['tools'] and 'tool:' + tool_id not in saved['completed']:
                        saved['active']['tool:' + tool_id] = dict(kind='agent', outputs=[])
                    saved['tools'][tool_id] = dict(kind='agent' if name in ('Agent', 'Task') else 'terminal', background=bool(args.get('run_in_background')))
                elif name in ('TaskOutput', 'TaskStop'):
                    saved['tools'][tool_id] = dict(kind=name, task_id=args.get('task_id'))
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
            task_id = result.get('backgroundTaskId') or (result.get('agentId') if tool.get('background') else None)
            if not task_id and tool.get('background'):
                match = re.search(r'(?:agentId|task[_ ]?id|background.{0,15}ID)\s*[:=]\s*([a-zA-Z0-9_-]+)', text, re.I)
                task_id = match.group(1) if match else None
            if task_id and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', str(task_id)) and task_id not in saved['completed']:
                kind = 'terminal' if result.get('backgroundTaskId') else tool.get('kind', 'agent')
                outputs = [p for p in re.findall(r'/(?:private/)?tmp/[^\s<>\"]+\.output', text + '\n' + str(result.get('stdout', '')))
                           if os.path.basename(p) == str(task_id) + '.output']
                if kind == 'agent':
                    outputs.append(os.path.join(os.path.dirname(path), 'subagents', 'agent-' + str(task_id) + '.jsonl'))
                saved['active'][task_id] = dict(kind=kind, outputs=outputs)
            nested = result.get('task') or {}
            if not isinstance(nested, dict):
                nested = {}
            terminal_status = result.get('status') or nested.get('status')
            finished = terminal_status in ('completed', 'failed', 'killed', 'stopped', 'cancelled', 'canceled')
            if task_id and finished:
                saved['active'].pop(task_id, None); saved['completed'].add(task_id)
            if tool.get('kind') in ('TaskOutput', 'TaskStop') and (finished or (tool['kind'] == 'TaskStop' and not block.get('is_error'))):
                finished_id = tool.get('task_id') or nested.get('task_id')
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
                        background_observable=False, background_output_bytes=0, background_revision=0, foreground_running=None)
        try:
            info = os.stat(path); key = (info.st_dev, info.st_ino)
            saved = self.files.get(path)
            if not saved or saved['key'] != key or info.st_size < saved['offset']:
                saved = dict(key=key, offset=0, buffer=b'', active={}, completed=set(), tools={}, revision=0, foreground=None)
                self.files[path] = saved
            with open(path, 'rb') as log:
                log.seek(saved['offset'])
                data = saved['buffer'] + log.read(8388608)
                saved['offset'] = log.tell()
            pieces = data.split(b'\n'); saved['buffer'] = pieces.pop()
            # A large unfinished media record must not grow without bound.
            if len(saved['buffer']) > 16777216:
                saved['buffer'] = b''
            for line in pieces:
                try:
                    self.consume(saved, json.loads(line), path)
                except (ValueError, TypeError, AttributeError):
                    continue
            active = list(saved['active'].values())
            output_bytes = 0
            for task in active:
                for output in task['outputs']:
                    try:
                        output_bytes += os.stat(output).st_size
                    except OSError:
                        pass
            if len(self.files) > 100:
                self.files = {path: saved}
            return dict(background_count=len(active), background_agents=sum(x['kind'] == 'agent' for x in active),
                        background_terminals=sum(x['kind'] == 'terminal' for x in active),
                        background_checking=saved['offset'] < info.st_size, background_observable=True,
                        background_output_bytes=output_bytes, background_revision=saved['revision'], foreground_running=saved['foreground'])
        except OSError:
            return self.read_path(None)


def apply_claude_background(result, background):
    screen_state = result.get('foreground_screen_state', result['state'])
    workflows = result.get('background_workflows', 0)
    background = dict(background, background_count=background['background_count'] + workflows)
    result.update(background)
    if result['state'] in ('idle', 'running', 'unknown') and not result.get('error_kind'):
        if background['background_checking']:
            if result['state'] != 'running':
                result.update(state='unknown', status='正在核对后台任务', working='')
        elif workflows or background.get('background_agents', 0):
            if workflows and not background.get('background_agents', 0):
                result.update(state='running', status='后台工作流运行中（{}）'.format(workflows),
                              working='等待 {} 个后台工作流完成'.format(workflows))
            elif result['state'] != 'running':
                result.update(state='running', status='子智能体运行中（{}）'.format(background['background_agents']),
                              working='等待子智能体完成')
            else:
                result['status'] = '运行中 · 子智能体 {}'.format(background.get('background_agents', 0)) if not workflows else result['status']
        elif background.get('foreground_running') is True:
            result.update(state='running', status='运行中', working=result.get('working') or '主对话尚未结束')
        elif background.get('foreground_running') is False and screen_state != 'running':
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
    children = {}
    for pid, (parent, _, _) in processes.items():
        children.setdefault(parent, []).append(pid)
    pending, seen, terminals, complete = list(children.get(root_pid, [])), set(), set(), True
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        pending.extend(children.get(pid, []))
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
KNOWN_AGENTS = {}


def snapshot(socket, selected):
    def tmux(*args):
        p = subprocess.run(['tmux'] + (['-S', socket] if socket else []) + list(args), text=True, capture_output=True, timeout=3)
        if p.returncode:
            raise RuntimeError(p.stderr.strip() or 'tmux 读取失败')
        return p.stdout
    started = time.monotonic()
    fields = '#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_dead}\t#{pane_pid}\t#{pane_dead_status}\t#{pane_in_mode}'
    processes = read_processes()
    panes = []
    for line in tmux('list-panes', '-a', '-F', fields).splitlines():
        bits = line.split('\t')
        if len(bits) != 10:
            continue
        session, window, pane, pane_id, command, cwd, dead, root_pid, exit_status, in_mode = bits
        if selected and session not in selected:
            continue
        try:
            screen = tmux('capture-pane', '-p', '-t', pane_id)
        except RuntimeError:
            # Pane may disappear between listing and capture.
            continue
        pid, process_state, agent = find_agent(processes, int(root_pid))
        if pid:
            KNOWN_AGENTS[pane_id] = agent
        agent = agent or KNOWN_AGENTS.get(pane_id) or (command if command in ('codex', 'claude') else '')
        analyser = analyse_claude_screen if agent == 'claude' else analyse_screen
        result = analyser(screen, agent or command, dead == '1',
                                None if pid is None else bool(pid), process_state, exit_status, in_mode == '1', processes is not None)
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
            apply_claude_background(result, CLAUDE_BACKGROUND.read_path(CLAUDE_LOGS.root_log(pid, time.monotonic())))
            CLAUDE_COMPLETIONS.apply(result, pane_id, pid, time.monotonic())
        elif agent == 'codex' and pid:
            apply_codex_background(result, CODEX_BACKGROUND.sample(pid, ROLLOUTS.root_log(pid, time.monotonic()), processes, time.monotonic()))
            CODEX_COMPLETIONS.apply(result, pane_id, pid, time.monotonic())
        usage_path = watcher.root_log(pid, time.monotonic()) if pid and agent in ('codex', 'claude') else None
        panes.append(dict(session=session, window=window, pane=pane, pane_id=pane_id,
                          command=command, agent=agent, cwd=cwd, screen=screen,
                          fingerprint=hashlib.sha256(screen.encode()).hexdigest(),
                          process_id=pid, event_source=has_events, lifecycle_events=events, lifecycle_cursor=lifecycle_cursor,
                          telemetry=USAGE_LOGS.sample(usage_path, agent), **result))
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
