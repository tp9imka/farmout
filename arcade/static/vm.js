// Pure state => view mapping. No DOM here: tested with `node --test`.

export const C = { R: '#ec3013', W: '#f3f2f2', INK: '#0e0d0d', AMB: '#f0a830', G4: '#9b9797', G6: '#605d5d', G8: '#2d2b2b' };
export const DASH = '--';
export const KINDS = ['review', 'bulk-read', 'research', 'implement', 'second-opinion'];
export const CLIS = ['codex', 'kiro', 'copilot', 'cursor'];
export const EFFORTS = [['LOW', 'low'], ['MED', 'medium'], ['HIGH', 'high']];
export const TILES_PER_ROW = 4;
export const STAGE_BLOCKS = 20;
export const REPLAY_SHOWN = 12;
export const HOF_SHOWN = 10;
export const TIMEOUT_STEP = 5;
export const DEFAULT_TIMEOUT_MIN = 30;
export const BOUNDS = { timeout_min: [1, 240], stall_min: [1, 120], max_jobs: [1, 32], in_place_max: [1, 32] };

const SESSION_STAGES = ['play', 'think', 'turn', 'idle'];
// Multica agent status -> design stage (and sprite pose).
const AGENT_STAGE = { working: 'play', queued: 'queued', idle: 'idle', offline: 'lost', unbound: 'lost' };
const AGENT_POSE = { play: 'play', queued: 'think', idle: 'idle', lost: 'lost' };
const HEALTH = { ok: 'OK', 'not-verified': 'NOT VERIFIED', 'logged-out': 'LOGGED OUT', missing: 'MISSING' };
const HEALTH_STYLE = {
  OK: [C.W, C.INK, C.W, 'solid'], 'NOT VERIFIED': ['transparent', C.AMB, C.AMB, 'solid'],
  'LOGGED OUT': ['transparent', C.R, C.R, 'solid'], MISSING: ['transparent', C.G4, C.G4, 'dashed'],
  [DASH]: ['transparent', C.G4, C.G6, 'dashed'],
};

export const pad = n => String(n).padStart(2, '0');
const isNum = n => typeof n === 'number' && Number.isFinite(n);
export const fmtT = s => {
  if (!isNum(s)) return DASH;
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return h ? `${h}:${pad(m)}:${pad(x)}` : `${pad(m)}:${pad(x)}`;
};
export const clock = ms => {
  if (!isNum(ms)) return DASH;
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
export const fmtN = n => isNum(n) ? n.toLocaleString('en-US') : DASH;
const dash = v => v == null || v === '' ? DASH : String(v);
const up = v => v == null ? DASH : String(v).toUpperCase();
const seedOf = id => { const s = String(id || ''); return s.charCodeAt(s.length - 1) || 0; };

export function coinsStr(c) {
  if (!c || !isNum(c.value)) return DASH;
  return c.unit === 'CR' ? `${c.value.toFixed(2)} CR` : `${c.value} ${c.unit || ''}`.trim();
}
export const scoreStr = (n, width = 7) => isNum(n) ? String(n).padStart(width, '0') : DASH;
const tokensOf = (rec, kind) => kind === 'claude' ? rec.score : rec.tokens;

// Design "status" per record: session play/think/turn/idle/raw, job play/pause/lost/clear/over.
export function stageOf(kind, rec) {
  if (kind === 'claude') return SESSION_STAGES.includes(rec.status) ? rec.status : 'raw';
  if (kind === 'agent') return AGENT_STAGE[rec.status] || 'idle';
  const p = rec.pose;
  if (p === 'play' || p === 'pause' || p === 'lost' || p === 'over' || p === 'queued') return p;
  return 'clear';
}

export function spritePose(kind, rec) {
  if (kind === 'claude') { const st = stageOf(kind, rec); return st === 'raw' ? 'idle' : st; }
  if (kind === 'agent') return AGENT_POSE[stageOf(kind, rec)];
  if (rec.pose === 'queued') return 'idle';
  return ['ready', 'landed', 'review', 'accepted'].includes(rec.pose) ? 'clear' : rec.pose;
}

export const overReason = rec => up(rec.status);

export function tagFor(kind, rec, blink) {
  const st = stageOf(kind, rec);
  const T = (label, bg, fg, bd, bl, bs) => ({ label, bg, fg, bd, bs: bs || 'solid', op: bl ? blink : '1' });
  if (kind === 'claude') {
    return {
      think: T('THINK', C.R, C.W, C.R), play: T('PLAY', C.R, C.W, C.R), turn: T('YOUR TURN', C.R, C.W, C.R, true),
      idle: T('IDLE', 'transparent', C.G4, C.G4),
    }[st] || T(up(rec.status), 'transparent', C.G4, C.G4);
  }
  if (kind === 'agent') {
    const nq = list(rec.queue).length;
    if (st === 'play') return T(nq ? `WORKING · +${nq}` : 'WORKING', C.R, C.W, C.R);
    if (st === 'queued') return T(`QUEUED · ${nq}`, 'transparent', C.AMB, C.AMB, true, 'dashed');
    if (st === 'lost') return T(up(rec.status), 'transparent', C.G4, C.G4, false, 'dashed');
    const last = rec.last && rec.last.status;
    return last === 'failed' ? T('IDLE · LAST FAILED', 'transparent', C.R, C.R) : T('IDLE', 'transparent', C.G4, C.G4);
  }
  if (st === 'play') return T('PLAY', C.R, C.W, C.R);
  if (st === 'pause') return T('PAUSE', C.AMB, C.INK, C.AMB, true);
  if (st === 'lost') return T('LOST', 'transparent', C.G4, C.G4, false, 'dashed');
  if (st === 'queued') return T('QUEUED', 'transparent', C.AMB, C.AMB, true, 'dashed');
  if (st === 'over') return T('OVER · ' + overReason(rec), 'transparent', C.R, C.R);
  return {
    ready: T('READY TO LAND', C.W, C.INK, C.W, true), landed: T('LANDED', C.W, C.INK, C.W),
    review: T('READY TO REVIEW', C.W, C.INK, C.W, true), accepted: T('ACCEPTED', C.W, C.INK, C.W),
    conflict: T('CONFLICT', 'transparent', C.AMB, C.AMB), discarded: T('DISCARDED', 'transparent', C.G4, C.G6),
  }[rec.pose] || T('CLEAR', C.W, C.INK, C.W);
}

function stageBar(rec, st) {
  const frac = isNum(rec.elapsed_s) && isNum(rec.timeout_s) && rec.timeout_s > 0 ? Math.min(1, rec.elapsed_s / rec.timeout_s) : 0;
  const barC = st === 'pause' ? C.AMB : st === 'clear' ? C.W : st === 'lost' ? C.G4 : C.R;
  const lit = Math.round(frac * STAGE_BLOCKS);
  return Array.from({ length: STAGE_BLOCKS }, (_, i) => ({ c: i < lit ? barC : C.G8 }));
}

export function crewOf(session, state) {
  const all = ((state && state.jobs) || []).concat((state && state.hof) || []);
  return (session.crew || []).map(id => all.find(j => j.id === id)).filter(Boolean);
}

const fileCount = rec => Array.isArray(rec.files) ? rec.files.length : null;
const itemsStr = (rec, kind) => {
  if (kind === 'worker' && rec.mode === 'read') return 'READ ONLY';
  const n = fileCount(rec);
  return (n == null ? DASH : pad(n)) + ' ITEMS';
};

export function tileVM(rec, kind, f, opts = {}) {
  if (kind === 'agent') return agentTileVM(rec, f, opts);
  const blink = opts.blink || '1';
  const isC = kind === 'claude';
  const st = stageOf(kind, rec);
  const tag = tagFor(kind, rec, blink);
  const replay = rec.replay || [];
  const last = replay[replay.length - 1];
  const crewW = isC ? crewOf(rec, opts.state) : [];
  return {
    real: true, id: rec.id, statusKey: dash(rec.status), kind, name: isC ? 'CLAUDE' : up(rec.cli), isClaude: isC, isWorker: !isC, isTurn: isC && st === 'turn',
    badge: isC ? 'CONTROL' : 'PLAYER', badgeBg: isC ? C.R : 'transparent', badgeBd: isC ? C.R : C.G4,
    sub: (isC ? 'SESSION' : `${up(rec.kind)} · ${rec.mode === 'in-place' ? 'IN PLACE' : up(rec.mode)}`) + (rec.repo ? ` · ${up(rec.repo)}` : ''),
    repo: dash(rec.repo), branch: dash(rec.branch),
    model: rec.model ? rec.model : isC ? DASH : 'DEFAULT',
    tool: rec.tool ? rec.tool : st === 'turn' ? 'WAITING' : DASH,
    score: scoreStr(tokensOf(rec, kind)),
    mission: dash(rec.title), lastMsg: last ? dash(last.m) : DASH,
    timeStr: fmtT(rec.elapsed_s), coins: isC ? DASH : coinsStr(rec.coins),
    items: itemsStr(rec, kind),
    bar: isC ? [] : stageBar(rec, st), barVal: isC ? '' : `${fmtT(rec.elapsed_s)} / ${fmtT(rec.timeout_s)}`,
    crew: crewW.map(w => { const t = tagFor('worker', w, '1'); return { c: t.bg === 'transparent' ? C.G8 : t.bg, bd: t.bd }; }),
    crewVal: `${crewW.length} WORKER${crewW.length === 1 ? '' : 'S'}`,
    tag,
    bStyle: st === 'lost' ? 'dashed' : 'solid',
    bColor: isC && st === 'turn' ? (blink === '1' ? C.R : C.G6) : st === 'lost' ? C.G4 : isC ? C.G6 : '#444141',
    topBar: isC ? C.R : 'transparent',
    dim: st === 'idle' ? '0.8' : st === 'raw' ? '0.55' : '1',
    clearOv: !isC && st === 'clear', overOv: !isC && st === 'over', reason: isC ? null : overReason(rec),
    pose: spritePose(kind, rec), f: f + seedOf(rec.id),
  };
}

// ---- Multica agents ------------------------------------------------------

export const mcState = state => (state && state.multica) || null;
export const mcAgents = state => { const m = mcState(state); return m && m.enabled ? list(m.agents) : []; };
const issueStr = issue => {
  if (!issue) return DASH;
  const id = issue.identifier || '', t = issue.title || '';
  return id && t ? `${id} · ${t}` : (id || t || DASH);
};

// Configured quick actions this agent may run (agents: null = every agent).
export function quickFor(rec, state) {
  const m = mcState(state);
  const name = String(rec.name || '').toLowerCase();
  return list(m && m.quick_actions).filter(q => !q.agents || list(q.agents).some(a => String(a).toLowerCase() === name));
}

function agentTileVM(rec, f, opts = {}) {
  const blink = opts.blink || '1';
  const st = stageOf('agent', rec);
  const task = rec.task;
  const nq = list(rec.queue).length;
  const replay = list(rec.replay);
  const last = replay[replay.length - 1];
  const where = [rec.provider, rec.runtime && rec.runtime.name].filter(Boolean).map(up).join(' · ');
  return {
    real: true, id: rec.id, statusKey: dash(rec.status) + ':' + (task ? task.id : ''), kind: 'agent',
    name: up(rec.name), isClaude: false, isWorker: false, isAgent: true, isTurn: false,
    badge: 'AGENT', badgeBg: 'transparent', badgeBd: C.AMB,
    sub: where ? `MULTICA · ${where}` : 'MULTICA',
    repo: DASH, branch: task ? dash(task.branch) : DASH,
    model: rec.model || 'DEFAULT',
    tool: rec.tool ? rec.tool : st === 'queued' ? 'WAITING FOR RUNTIME' : st === 'lost' ? up(rec.status) : DASH,
    score: '',
    mission: task ? issueStr(task.issue) : nq ? issueStr(rec.queue[0].issue) : rec.last ? `LAST · ${issueStr(rec.last.issue)}` : 'NO TASK',
    lastMsg: last ? dash(last.m) : rec.last && rec.last.error ? `ERROR · ${rec.last.error}` : DASH,
    timeStr: task ? fmtT(task.elapsed_s) : DASH, coins: '', items: nq ? `${nq} QUEUED` : '',
    bar: [], barVal: '',
    queue: Array.from({ length: Math.min(nq, 8) }, () => ({ c: C.AMB })), queueVal: `${nq} QUEUED`,
    quick: quickFor(rec, opts.state).slice(0, 3).map(q => ({ i: q.i, label: q.label })),
    crew: [], crewVal: '',
    tag: tagFor('agent', rec, blink),
    bStyle: st === 'lost' ? 'dashed' : 'solid',
    bColor: st === 'lost' ? C.G4 : st === 'play' ? C.AMB : '#444141',
    topBar: st === 'play' ? C.AMB : 'transparent',
    dim: st === 'idle' ? '0.8' : st === 'lost' ? '0.6' : '1',
    clearOv: false, overOv: false, reason: null,
    pose: spritePose('agent', rec), f: f + seedOf(rec.id),
  };
}

const taskRowVM = (t, base) => ({
  id: t.id, issue: issueStr(t.issue), status: up(t.status), time: fmtT(t.elapsed_s),
  url: base && t.issue && t.issue.id ? base + t.issue.id : null,
});

export function focusAgentVM(rec, state, f, blink) {
  const o = agentTileVM(rec, f, { blink, state });
  const task = rec.task, base = rec.issue_base || null;
  o.replay = replayVM(rec.replay);
  o.mission = task ? issueStr(task.issue) : 'NO TASK RUNNING';
  o.missionUrl = task && base && task.issue && task.issue.id ? base + task.issue.id : null;
  o.description = rec.description ? String(rec.description) : null;
  o.url = rec.url || null;
  o.queueList = list(rec.queue).map(t => taskRowVM(t, base));
  o.lastRow = rec.last ? taskRowVM(rec.last, base) : null;
  o.lastError = rec.last && rec.last.error ? String(rec.last.error) : null;
  o.meta = [
    { k: 'PROVIDER', v: up(rec.provider) }, { k: 'MODEL', v: rec.model || 'DEFAULT' },
    { k: 'RUNTIME', v: dash(rec.runtime && rec.runtime.name) }, { k: 'RUNTIME STATUS', v: up(rec.runtime && rec.runtime.status) },
    { k: 'RUN', v: task ? up(task.status) : DASH }, { k: 'ELAPSED', v: task ? fmtT(task.elapsed_s) : DASH },
    { k: 'ATTEMPT', v: task && isNum(task.attempt) ? `${task.attempt}/${dash(task.max_attempts)}` : DASH },
    { k: 'MAX PARALLEL', v: dash(rec.max_concurrent) },
    { k: 'BRANCH', v: task ? dash(task.branch) : DASH, span: '1 / -1' },
    { k: 'WORK DIR', v: task ? dash(task.work_dir) : DASH, span: '1 / -1' },
  ].map(m => ({ span: 'auto', ...m }));
  o.quickAll = quickFor(rec, state).map(q => ({ i: q.i, label: q.label, title: q.title }));
  o.canCancel = !!task;
  o.cancelId = task ? task.id : null;
  return o;
}

export function autopilotsVM(state) {
  return list(mcState(state) && mcState(state).autopilots).map(a => ({
    id: a.id, title: dash(a.title), desc: a.description ? String(a.description) : '',
    agent: a.agent ? up(a.agent) : a.assignee_type ? up(a.assignee_type) : DASH,
    triggers: list(a.triggers).map(up).join(' · ') || 'MANUAL',
    mode: up(a.mode), next: a.next_run ? when(a.next_run, state && state.now) : DASH,
    last: a.last_run ? when(a.last_run, state && state.now) : 'NEVER',
    active: a.status === 'active',
    tag: a.status === 'active' ? { label: 'ACTIVE', bg: C.W, fg: C.INK, bd: C.W, bs: 'solid', op: '1' }
      : { label: up(a.status), bg: 'transparent', fg: C.G4, bd: C.G4, bs: 'dashed', op: '1' },
  }));
}

// "14:05:00" today, else "OCT 03 09:00".
function when(ms, nowMs) {
  const d = new Date(ms), n = new Date(isNum(nowMs) ? nowMs : Date.now());
  if (d.toDateString() === n.toDateString()) return clock(ms);
  const mon = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'][d.getMonth()];
  return `${mon} ${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function mcStatusVM(state) {
  const m = mcState(state);
  if (!m || !m.enabled) return null;
  const ws = m.workspace && (m.workspace.name || m.workspace.slug);
  if (m.connected) return { label: `MULTICA · ${up(ws || 'CONNECTED')}`, color: C.W, title: m.server || '' };
  return { label: 'MULTICA · OFFLINE', color: C.AMB, title: m.error || m.why || '' };
}

// land applies to whatever the repo has checked out now, not to source_branch.
const checkoutOf = rec => `${dash(rec.repo)}'s current checkout`;

const filesVM = files => (files || []).map(x => ({ n: dash(x.n), a: dash(x.a), d: dash(x.d) }));
const replayVM = replay => (replay || []).slice(-REPLAY_SHOWN).map(l => ({ t: clock(l.t), m: dash(l.m) }));
const list = value => Array.isArray(value) ? value : [];
const commitsVM = commits => list(commits).filter(c => c && typeof c === 'object').map(c => {
  const files = list(c.files);
  const count = key => files.every(x => x && Number.isInteger(x[key]) && x[key] >= 0)
    ? files.reduce((n, x) => n + x[key], 0) : null;
  return {
    sha: dash(c.sha).slice(0, 7), subject: dash(c.subject),
    summary: `+${dash(count('added'))} −${dash(count('deleted'))} · ${files.length} FILES`,
    sharedLabel: c.shared === true ? 'SHARED' : null,
    availabilityLabel: c.available === false ? 'UNAVAILABLE · NOT ON CURRENT BRANCH' : null,
  };
});
const HEURISTIC_LABEL = 'Attribution is heuristic: exact when concurrent lanes keep to their --owns paths; hashes may change after rebase.';
const unattributedText = commit => {
  if (typeof commit === 'string') return dash(commit);
  if (!commit || typeof commit !== 'object') return DASH;
  const text = value => typeof value === 'string' ? dash(value) : DASH;
  return `${text(commit.sha)} ${text(commit.subject)}`;
};

export function focusJobVM(rec, f, blink) {
  const o = tileVM(rec, 'worker', f, { blink });
  const st = stageOf('worker', rec);
  const inPlace = rec.mode === 'in-place';
  const nFiles = dash(fileCount(rec));
  o.brief = dash(rec.brief);
  o.replay = replayVM(rec.replay);
  o.files = filesVM(rec.files);
  o.meta = [
    { k: 'CLI', v: up(rec.cli) }, { k: 'MODEL', v: rec.model || 'DEFAULT' }, { k: 'MODE', v: up(rec.mode) }, { k: 'TIMEOUT', v: fmtT(rec.timeout_s) },
    { k: 'JOB ID', v: dash(rec.id) }, { k: 'TOKENS', v: fmtN(rec.tokens) }, { k: 'COINS', v: coinsStr(rec.coins) }, { k: 'TASK KIND', v: dash(rec.kind) },
    ...(inPlace ? [
      { k: 'CHECKOUT', v: dash(rec.checkout), span: '1 / -1', wrap: true },
      { k: 'OWNS', v: list(rec.owns).map(String).join('\n') || DASH, lines: list(rec.owns).map(String), span: '1 / -1' },
    ] : [{ k: 'WORKTREE', v: dash(rec.worktree), span: '1 / -1' }]),
  ].map(m => ({ span: 'auto', ...m }));
  o.readOnly = rec.mode === 'read';
  o.canKill = st === 'play' || st === 'pause' || st === 'lost';
  o.canAccept = inPlace && rec.pose === 'review';
  o.canLand = !inPlace && rec.pose === 'ready';
  o.canDiscard = !inPlace && (rec.pose === 'ready' || rec.pose === 'conflict');
  o.noActions = !o.canKill && !o.canLand && !o.canDiscard && !o.canAccept;
  o.actionNote = st === 'queued' ? 'QUEUED · WAITING FOR A SLOT' : st === 'over' ? 'JOB ENDED · NO ACTIONS'
    : inPlace ? rec.pose === 'accepted' ? 'COMMITS REVIEWED · NO ACTIONS' : 'IN PLACE · NO ACTIONS'
      : rec.pose === 'landed' ? 'PATCH LANDED · NO ACTIONS' : rec.pose === 'discarded' ? 'WORKTREE DISCARDED · NO ACTIONS' : 'READ JOB · NOTHING TO LAND';
  o.showConflict = !inPlace && rec.pose === 'conflict';
  o.showLanded = !inPlace && rec.pose === 'landed';
  o.showCommits = inPlace;
  o.commits = inPlace ? commitsVM(rec.commits) : [];
  o.heuristicLabel = inPlace && (list(rec.commits).some(c => c && c.shared === true) || list(rec.unattributed).length > 0) ? HEURISTIC_LABEL : null;
  o.warnings = inPlace ? [
    ['UNCOMMITTED OWNED PATHS', rec.uncommitted], ['PATHS OUTSIDE --owns', rec.outside_owns], ['UNATTRIBUTED COMMITS', list(rec.unattributed).map(unattributedText)],
  ].map(([title, items]) => ({ title, items: list(items).map(String) })).filter(w => w.items.length > 0) : [];
  o.error = rec.error ? String(rec.error) : null;
  o.out = (rec.out || []).map(String);
  o.landedMsg = `${nFiles} files applied to ${checkoutOf(rec)} as uncommitted changes.`;
  return o;
}

export function focusSessionVM(rec, state, f, blink) {
  const o = tileVM(rec, 'claude', f, { blink, state });
  const t = rec.tokens || {};
  o.prompt = dash(rec.prompt);
  o.replay = replayVM(rec.replay);
  o.files = filesVM(rec.files);
  o.tIn = fmtN(t.in); o.tOut = fmtN(t.out); o.tCache = fmtN(t.cache);
  const crew = crewOf(rec, state);
  o.crewList = crew.map(w => tileVM(w, 'worker', f, { blink }));
  o.crewCount = `${crew.length} WORKER${crew.length === 1 ? '' : 'S'}`;
  o.noCrew = crew.length === 0;
  // Only a stopped (Ctrl+Z) session can be ended from here; live ones stay watch-only.
  o.canEnd = rec.status === 'SUSPENDED';
  return o;
}

export function confirmVM(type, rec, extra = {}) {
  if (type === 'mc-cancel') {
    return { title: 'CANCEL RUN?', body: `Stops ${up(rec.name)}'s run on ${issueStr(rec.task && rec.task.issue)} in Multica. The issue stays open.`, yes: 'CANCEL RUN', hasFiles: false, files: [] };
  }
  if (type === 'mc-quick' || type === 'mc-queue') {
    const title = dash(extra.title), prompt = extra.prompt ? String(extra.prompt) : '';
    return {
      title: 'QUEUE TASK?', yes: 'QUEUE',
      body: `Creates the Multica issue “${title}” assigned to ${up(rec.name)}. Its run starts when ${up(rec.name)}'s runtime picks it up.${prompt ? ' Brief: ' + prompt : ''}`,
      hasFiles: false, files: [],
    };
  }
  if (type === 'mc-trigger') {
    return { title: 'RUN AUTOPILOT NOW?', body: `Fires “${dash(rec.title)}” once${rec.agent ? ` for ${up(rec.agent)}` : ''}, outside its schedule.`, yes: 'RUN NOW', hasFiles: false, files: [] };
  }
  const cli = up(rec.cli), id = dash(rec.id), files = filesVM(rec.files), n = dash(fileCount(rec));
  const killNote = rec.mode === 'in-place' ? ` Partial changes stay uncommitted in ${dash(rec.repo)}'s checkout.`
    : rec.mode === 'write' ? ' Partial changes stay in its worktree.' : '';
  return {
    accept: { title: 'ACCEPT COMMITS?', body: `Marks the ${list(rec.commits).length} commits on ${dash(rec.branch)} as reviewed. Nothing in git changes.`, yes: 'ACCEPT', hasFiles: false, files: [] },
    land: { title: 'LAND PATCH?', body: `Applies ${n} files from ${cli} job ${id} to ${checkoutOf(rec)} as uncommitted changes. Nothing is committed.`, yes: 'CONFIRM LAND', hasFiles: true, files },
    discard: { title: 'DISCARD PATCH?', body: `Deletes the worktree for ${cli} job ${id}. The patch cannot be recovered.`, yes: 'DISCARD', hasFiles: true, files },
    kill: { title: 'KILL JOB?', body: `Stops ${cli} job ${id} now.${killNote}`, yes: 'KILL', hasFiles: false, files: [] },
    end: { title: 'END SESSION?', body: `Ends the suspended Claude session "${dash(rec.title)}". Its conversation stays resumable with claude --resume.`, yes: 'END', hasFiles: false, files: [] },
  }[type];
}

const ACTION_NAMES = { 'mc-cancel': 'CANCEL', 'mc-quick': 'QUEUE', 'mc-queue': 'QUEUE', 'mc-trigger': 'RUN NOW' };

// The action result line, or null on success. A null exit code means the
// server never got one (spawn failure or timeout), not an HTTP-level failure.
export function actionMsgVM(type, httpStatus, body) {
  const T = ACTION_NAMES[type] || type.toUpperCase();
  if (body && body.conflict) return 'CONFLICT · PATCH DOES NOT APPLY';
  if (httpStatus === 409) return type === 'end' ? 'SESSION IS NOT SUSPENDED ANYMORE' : type.startsWith('mc-') ? 'ANOTHER ACTION IS RUNNING' : 'ANOTHER ACTION IS RUNNING ON THIS JOB';
  const detail = body && (body.err || body.error) ? String(body.err || body.error).trim() : '';
  const tail = detail ? ` · ${detail}` : '';
  if (httpStatus < 200 || httpStatus >= 300) return `${T} FAILED (HTTP ${httpStatus})${tail}`;
  if (!body) return `${T} FAILED · BAD RESPONSE`;
  if (body.ok) return null;
  if (body.timed_out) return `${T} TIMED OUT${tail}`;
  if (body.code != null) return `${T} FAILED (EXIT ${body.code})${tail}`;
  return `${T} FAILED${tail}`;
}

// Idle, suspended and ended sessions sit on the bench unless returned by hand.
export const autoBenched = (kind, rec) => (kind === 'claude' && ['idle', 'raw'].includes(stageOf(kind, rec)))
  || (kind === 'agent' && ['idle', 'lost'].includes(stageOf(kind, rec)));
export function isBenched(kind, rec, bench) {
  const b = bench || {};
  if ((b.benched || []).includes(rec.id)) return true;
  if ((b.returned || []).includes(rec.id)) return false;
  return autoBenched(kind, rec);
}
export const benchOps = {
  bench: (b, id) => ({ benched: [...(b.benched || []).filter(x => x !== id), id], returned: (b.returned || []).filter(x => x !== id) }),
  ret: (b, id) => ({ benched: (b.benched || []).filter(x => x !== id), returned: [...(b.returned || []).filter(x => x !== id), id] }),
};

// Packed grid: every CONTROL tile, then every PLAYER tile (grouped by repo in
// the order the sessions list them), filling each row and page before the
// next one starts. INSERT COIN fills only the last row.
// view 'play' | 'bench' filters by isBenched; counts cover both views.
export function rows(state, rowsPerPage, page, view = 'play', bench = null) {
  const every = ((state && state.sessions) || []).map(rec => ({ kind: 'claude', rec }))
    .concat(((state && state.jobs) || []).map(rec => ({ kind: 'worker', rec })))
    .concat(mcAgents(state).map(rec => ({ kind: 'agent', rec })));
  const onBench = every.filter(r => isBenched(r.kind, r.rec, bench));
  const recs = view === 'bench' ? onBench : every.filter(r => !onBench.includes(r));
  const ctrl = recs.filter(r => r.kind === 'claude');
  const repoRank = repo => { const i = ctrl.findIndex(r => r.rec.repo === repo); return i < 0 ? ctrl.length : i; };
  const players = recs.filter(r => r.kind === 'worker')
    .map((r, i) => ({ r, i })).sort((a, b) => repoRank(a.r.rec.repo) - repoRank(b.r.rec.repo) || a.i - b.i).map(x => x.r);
  const ordered = ctrl.concat(players, recs.filter(r => r.kind === 'agent'));
  const all = [];
  for (let i = 0; i < ordered.length; i += TILES_PER_ROW) {
    const tiles = ordered.slice(i, i + TILES_PER_ROW).map(r => ({ real: true, ...r }));
    while (tiles.length < TILES_PER_ROW) tiles.push({ empty: true });
    all.push({ real: true, tiles });
  }
  const nPages = Math.max(1, Math.ceil(all.length / rowsPerPage));
  const p = (((page || 0) % nPages) + nPages) % nPages;
  const pageRows = all.slice(p * rowsPerPage, p * rowsPerPage + rowsPerPage);
  while (pageRows.length < rowsPerPage) pageRows.push({ real: false });
  return { pageRows, nPages, page: p, total: all.length, records: every.length, nPlay: every.length - onBench.length, nBench: onBench.length };
}

export function hud(state) {
  const h = state && state.hud;
  if (!h) return { score: '--------', inPlay: DASH, coins: DASH };
  return {
    score: isNum(h.score) ? String(h.score).padStart(8, '0') : '--------',
    inPlay: `${dash(h.live)}/${dash(h.shown)}`,
    coins: `${isNum(h.credits) ? h.credits.toFixed(2) : DASH} CR · ${dash(h.premium)} PR`,
  };
}

export function hofVM(state) {
  return ((state && state.hof) || []).slice(0, HOF_SHOWN).map((rec, i) => ({
    id: rec.id, rank: pad(i + 1), name: up(rec.cli), mission: dash(rec.title), repo: dash(rec.repo),
    tag: tagFor('worker', rec, '1'), time: fmtT(rec.elapsed_s),
  }));
}

export function findRecord(state, id) {
  if (!state || !id) return null;
  const s = (state.sessions || []).find(x => x.id === id);
  if (s) return { kind: 'claude', rec: s };
  const j = (state.jobs || []).concat(state.hof || []).find(x => x.id === id);
  if (j) return { kind: 'worker', rec: j };
  const a = mcAgents(state).find(x => x.id === id);
  return a ? { kind: 'agent', rec: a } : null;
}

// SETUP draft: operations on the server's config shape, each returning a new object.
const clone = o => JSON.parse(JSON.stringify(o));
const clamp = (v, [lo, hi]) => Math.max(lo, Math.min(hi, v));
const edit = (cfg, fn) => { const d = clone(cfg); fn(d); return d; };

export const configDraftOps = {
  toggle: (cfg, cli) => edit(cfg, d => { d.workers[cli].enabled = !d.workers[cli].enabled; }),
  setModel: (cfg, cli, v) => edit(cfg, d => { d.workers[cli].model = v === 'DEFAULT' ? null : v; }),
  // Picking the active effort again clears it back to null (flag not passed).
  setEffort: (cfg, cli, label) => edit(cfg, d => {
    const v = (EFFORTS.find(e => e[0] === label) || [])[1] || null;
    d.workers[cli].effort = d.workers[cli].effort === v ? null : v;
  }),
  bumpTimeout: (cfg, cli, delta) => edit(cfg, d => {
    d.workers[cli].timeout_min = clamp((d.workers[cli].timeout_min ?? DEFAULT_TIMEOUT_MIN) + delta, BOUNDS.timeout_min);
  }),
  setRule: (cfg, i, field, v) => edit(cfg, d => { d.routing[i][field] = v === DASH ? null : v; }),
  moveRule: (cfg, i, delta) => edit(cfg, d => {
    const j = i + delta;
    if (j < 0 || j >= d.routing.length) return;
    [d.routing[i], d.routing[j]] = [d.routing[j], d.routing[i]];
  }),
  delRule: (cfg, i) => edit(cfg, d => { d.routing.splice(i, 1); }),
  addRule: cfg => edit(cfg, d => { d.routing.push({ kind: 'review', prefer: 'codex', fallback: null }); }),
  bumpLimit: (cfg, key, delta) => edit(cfg, d => {
    if (!d.limits || d.limits[key] == null || !BOUNDS[key]) return;
    if (key === 'in_place_max') {
      // Config loads accept digit strings; never concatenate them or save NaN.
      const v = typeof d.limits[key] === 'string' && /^\d+$/.test(d.limits[key]) ? Number(d.limits[key]) : d.limits[key];
      if (typeof v !== 'number' || !Number.isSafeInteger(v) || !Number.isSafeInteger(delta)) return;
      d.limits[key] = clamp(v + delta, BOUNDS[key]);
    } else d.limits[key] = clamp(d.limits[key] + delta, BOUNDS[key]);
  }),
};

export const isDirty = (draft, saved) => JSON.stringify(draft) !== JSON.stringify(saved);

export function setupVM(draft, saved, doctor, checking) {
  const workers = draft.workers || {};
  const keys = CLIS.filter(k => k in workers).concat(Object.keys(workers).filter(k => !CLIS.includes(k)));
  const docOf = k => (doctor || []).find(d => d.cli === k);
  const ws = keys.map(k => {
    const w = workers[k], doc = docOf(k);
    const health = doc && HEALTH[doc.status] ? HEALTH[doc.status] : DASH;
    const [hBg, hFg, hBd, hBs] = HEALTH_STYLE[health];
    const models = ['DEFAULT'].concat(w.models || []);
    if (w.model && !models.includes(w.model)) models.push(w.model);
    return {
      key: k, cli: k.toUpperCase(), on: !!w.enabled, onLabel: w.enabled ? 'ON' : 'OFF',
      onBg: w.enabled ? C.R : 'transparent', onBd: w.enabled ? C.R : C.G6, rowOp: w.enabled ? '1' : '0.5',
      health: checking ? 'CHECKING…' : health, hBg, hFg, hBd, hBs,
      version: doc ? dash(doc.version) : DASH,
      model: w.model || 'DEFAULT', models,
      efforts: EFFORTS.map(([label, v]) => ({ v: label, on: w.effort === v, bg: w.effort === v ? C.R : 'transparent' })),
      timeout: dash(w.timeout_min),
    };
  });
  const routing = draft.routing || [];
  const offMark = c => workers[c] && workers[c].enabled === false ? ' (OFF)' : '';
  const cliOpts = keys.map(v => ({ v, l: v.toUpperCase() + offMark(v) }));
  return {
    workers: ws,
    rules: routing.map((r, i) => ({
      n: pad(i + 1), kind: r.kind, pref: r.prefer, fb: r.fallback == null ? DASH : r.fallback,
      upOp: i === 0 ? '0.3' : '1', downOp: i === routing.length - 1 ? '0.3' : '1',
    })),
    kinds: KINDS, cliOpts, fbOpts: [{ v: DASH, l: 'NONE' }].concat(cliOpts),
    lim: { stall: dash((draft.limits || {}).stall_min), maxJobs: dash((draft.limits || {}).max_jobs), inPlaceMax: dash((draft.limits || {}).in_place_max) },
    dirty: isDirty(draft, saved),
  };
}
