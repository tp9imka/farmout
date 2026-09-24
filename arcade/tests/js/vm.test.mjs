import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  C, fmtT, clock, tileVM, tagFor, stageOf, spritePose, coinsStr, rows, hud, hofVM,
  focusJobVM, focusSessionVM, confirmVM, configDraftOps, setupVM, isDirty, crewOf, DEFAULT_TIMEOUT_MIN,
  actionMsgVM, autoBenched, isBenched, benchOps,
} from '../../static/vm.js';
import { poseGrid, shadowOf } from '../../static/sprite.js';

const FIX = fileURLToPath(new URL('../fixtures/state.sample.json', import.meta.url));
const state = () => JSON.parse(readFileSync(FIX, 'utf8'));
const job = (over = {}) => ({ ...state().jobs[0], ...over });
const sess = (over = {}) => ({ ...state().sessions[0], ...over });

const SESSION_KEYS = ['id', 'name', 'repo', 'branch', 'cwd', 'model', 'status', 'title', 'prompt', 'tool',
  'started', 'elapsed_s', 'tokens', 'score', 'replay', 'files', 'crew'];
const JOB_KEYS = ['id', 'out', 'cli', 'kind', 'mode', 'title', 'brief', 'repo', 'branch', 'model', 'effort', 'status',
  'pose', 'land', 'error', 'elapsed_s', 'timeout_s', 'started', 'ended', 'tool', 'tokens', 'coins', 'replay',
  'files', 'worktree', 'parent_session', 'route'];

test('sample state carries every frozen-schema key and every pose', () => {
  const s = state();
  for (const k of ['now', 'stall_min', 'machine', 'errors', 'hud', 'sessions', 'jobs', 'hof']) assert.ok(k in s, k);
  for (const k of ['score', 'live', 'shown', 'credits', 'premium']) assert.ok(k in s.hud, k);
  for (const x of s.sessions) for (const k of SESSION_KEYS) assert.ok(k in x, `session ${k}`);
  for (const x of s.jobs.concat(s.hof)) for (const k of JOB_KEYS) assert.ok(k in x, `job ${k}`);
  const poses = new Set(s.jobs.concat(s.hof).map(j => j.pose));
  for (const p of ['play', 'pause', 'lost', 'clear', 'over', 'ready', 'landed', 'conflict', 'discarded']) assert.ok(poses.has(p), p);
  const st = new Set(s.sessions.map(x => x.status));
  for (const p of ['play', 'think', 'turn', 'idle']) assert.ok(st.has(p), p);
});

test('job pose => stage, sprite pose and tag', () => {
  const cases = [
    ['play', 'play', 'play', 'PLAY', C.R, '1'],
    ['pause', 'pause', 'pause', 'PAUSE', C.AMB, 'blink'],
    ['lost', 'lost', 'lost', 'LOST', 'transparent', '1'],
    ['clear', 'clear', 'clear', 'CLEAR', C.W, '1'],
    ['ready', 'clear', 'clear', 'READY TO LAND', C.W, 'blink'],
    ['landed', 'clear', 'clear', 'LANDED', C.W, '1'],
    ['conflict', 'clear', 'conflict', 'CONFLICT', 'transparent', '1'],
    ['discarded', 'clear', 'discarded', 'DISCARDED', 'transparent', '1'],
  ];
  for (const [pose, stage, sp, label, bg, op] of cases) {
    const j = job({ pose, status: pose === 'lost' ? 'lost' : pose === 'play' || pose === 'pause' ? 'running' : 'ok' });
    assert.equal(stageOf('worker', j), stage, pose);
    assert.equal(spritePose('worker', j), sp, pose);
    const t = tagFor('worker', j, 'blink');
    assert.equal(t.label, label, pose);
    assert.equal(t.bg, bg, pose);
    assert.equal(t.op, op, pose);
  }
  assert.equal(tagFor('worker', job({ pose: 'lost' }), '1').bs, 'dashed');
});

test('over carries the reason from the status', () => {
  for (const [status, reason] of [['timeout', 'TIMEOUT'], ['rate-limited', 'RATE-LIMITED'], ['failed', 'FAILED'],
    ['killed', 'KILLED'], ['empty', 'EMPTY']]) {
    const j = job({ pose: 'over', status });
    assert.equal(tagFor('worker', j, '1').label, `OVER · ${reason}`);
    const v = tileVM(j, 'worker', 0, { blink: '1' });
    assert.equal(v.overOv, true);
    assert.equal(v.reason, reason);
  }
});

test('session statuses', () => {
  const cases = [['play', 'PLAY', '1'], ['think', 'THINK', '1'], ['turn', 'YOUR TURN', 'blink'], ['idle', 'IDLE', '1']];
  for (const [status, label, op] of cases) {
    const t = tagFor('claude', sess({ status }), 'blink');
    assert.equal(t.label, label);
    assert.equal(t.op, op);
  }
  const raw = sess({ status: 'SHELL' });
  assert.equal(tagFor('claude', raw, '1').label, 'SHELL');
  assert.equal(spritePose('claude', raw), 'idle');
  const turn = tileVM(sess({ status: 'turn', tool: null }), 'claude', 0, { blink: '1' });
  assert.equal(turn.isTurn, true);
  assert.equal(turn.tool, 'WAITING');
  assert.equal(tileVM(sess({ status: 'idle' }), 'claude', 0, { blink: '1' }).dim, '0.8');
});

test('null renders as "--"', () => {
  const v = tileVM(job({ tokens: null, coins: null, tool: null, elapsed_s: null, timeout_s: null, replay: [] }), 'worker', 0, { blink: '1' });
  assert.equal(v.score, '--');
  assert.equal(v.coins, '--');
  assert.equal(v.tool, '--');
  assert.equal(v.timeStr, '--');
  assert.equal(v.lastMsg, '--');
  assert.equal(v.barVal, '-- / --');
  const s = tileVM(sess({ tokens: null, score: null, model: null }), 'claude', 0, { blink: '1' });
  assert.equal(s.score, '--');
  assert.equal(s.model, '--');
  const f = focusSessionVM(sess({ tokens: null, score: null, files: [{ n: 'b.md', a: 9, d: null }], replay: [{ t: null, m: 'x' }] }), state(), 0, '1');
  assert.equal(f.tIn, '--');
  assert.equal(f.files[0].d, '--');
  assert.equal(f.replay[0].t, '--');
  assert.equal(fmtT(null), '--');
  assert.equal(clock(null), '--');
});

test('job model null means the CLI default', () => {
  assert.equal(tileVM(job({ model: null }), 'worker', 0, { blink: '1' }).model, 'DEFAULT');
});

test('coins units', () => {
  assert.equal(coinsStr({ value: 0.07, unit: 'CR' }), '0.07 CR');
  assert.equal(coinsStr({ value: 3, unit: 'PR' }), '3 PR');
  assert.equal(coinsStr(null), '--');
  assert.equal(coinsStr({ value: null, unit: 'CR' }), '--');
});

test('stage bar is elapsed/timeout in 20 blocks', () => {
  const v = tileVM(job({ pose: 'play', elapsed_s: 900, timeout_s: 1800 }), 'worker', 0, { blink: '1' });
  assert.equal(v.bar.length, 20);
  assert.equal(v.bar.filter(b => b.c === C.R).length, 10);
  assert.equal(v.barVal, '15:00 / 30:00');
  const over = tileVM(job({ pose: 'pause', elapsed_s: 4000, timeout_s: 1800 }), 'worker', 0, { blink: '1' });
  assert.equal(over.bar.filter(b => b.c === C.AMB).length, 20);
  const none = tileVM(job({ elapsed_s: 10, timeout_s: null }), 'worker', 0, { blink: '1' });
  assert.equal(none.bar.filter(b => b.c !== C.G8).length, 0);
});

test('items: read-only jobs and file counts', () => {
  assert.equal(tileVM(job({ mode: 'read', files: null }), 'worker', 0, { blink: '1' }).items, 'READ ONLY');
  assert.equal(tileVM(job({ mode: 'write', files: [{ n: 'a', a: 1, d: 1 }] }), 'worker', 0, { blink: '1' }).items, '01 ITEMS');
  assert.equal(tileVM(sess({ files: [] }), 'claude', 0, { blink: '1' }).items, '00 ITEMS');
});

test('rows: one record fills one row, INSERT COIN pads it', () => {
  const r = rows({ sessions: [], jobs: [job({ repo: 'r1' })] }, 2, 0);
  assert.equal(r.nPages, 1);
  assert.equal(r.pageRows.length, 2);
  assert.equal(r.pageRows[0].real, true);
  assert.equal(r.pageRows[0].tiles.filter(t => t.empty).length, 3);
  assert.equal(r.pageRows[1].real, false);
});

test('rows: packed across repos, controllers first, a new page only when full', () => {
  const sessions = [sess({ id: 's1', repo: 'b' }), sess({ id: 's2', repo: 'a' })];
  const jobs = [job({ id: 'ja1', repo: 'a' }), job({ id: 'jc1', repo: 'c' }), job({ id: 'jb1', repo: 'b' })];
  const ids = r => r.pageRows.filter(x => x.real).flatMap(x => x.tiles.filter(t => t.real).map(t => t.rec.id));
  const r0 = rows({ sessions, jobs }, 2, 0);
  assert.equal(r0.nPages, 1);
  // Sessions keep their order; jobs follow, grouped by their session's repo, orphans last.
  assert.deepEqual(ids(r0), ['s1', 's2', 'jb1', 'ja1', 'jc1']);
  assert.equal(r0.pageRows[0].tiles.filter(t => t.empty).length, 0);
  assert.equal(r0.pageRows[1].tiles.filter(t => t.empty).length, 3);
  const many = { sessions, jobs: Array.from({ length: 7 }, (_, i) => job({ id: `j${i}`, repo: 'a' })) };
  assert.equal(rows(many, 2, 0).nPages, 2); // 9 tiles, 8 slots per page
  assert.deepEqual(ids(rows(many, 2, 1)), ['j6']);
  assert.equal(rows(many, 2, 2).page, 0);
  assert.equal(rows(many, 2, -1).page, 1);
  assert.equal(rows({ sessions: [], jobs: [] }, 2, 0).total, 0);
});

test('hud strings', () => {
  assert.deepEqual(hud(state()), { score: '01234567', inPlay: '8/10', coins: '0.19 CR · 3 PR' });
  assert.deepEqual(hud(null), { score: '--------', inPlay: '--', coins: '--' });
  assert.deepEqual(hud({ hud: { score: null, live: 0, shown: 0, credits: null, premium: null } }),
    { score: '--------', inPlay: '0/0', coins: '-- CR · -- PR' });
});

test('hall of fame rows', () => {
  const h = hofVM(state());
  assert.equal(h.length, 5);
  assert.equal(h[0].rank, '01');
  assert.equal(h[0].name, 'COPILOT');
  assert.equal(h[0].tag.label, 'OVER · TIMEOUT');
  assert.equal(h[0].time, '20:00');
});

test('crew comes from parent_session ids, falling back to hof', () => {
  const s = state();
  const crew = crewOf(s.sessions[0], s);
  assert.deepEqual(crew.map(j => j.id), ['20260923-141502-codex-a1b2', '20260923-130500-cursor-0a1b']);
  const v = tileVM(s.sessions[0], 'claude', 0, { blink: '1', state: s });
  assert.equal(v.crewVal, '2 WORKERS');
  assert.equal(v.crew.length, 2);
  assert.deepEqual(crewOf(sess({ crew: ['nope'] }), s), []);
});

test('focus job actions follow the pose', () => {
  const s = state();
  const byPose = p => s.jobs.concat(s.hof).find(j => j.pose === p);
  const f = p => focusJobVM(byPose(p), 0, '1');
  assert.deepEqual([f('play').canKill, f('play').canLand, f('play').canDiscard], [true, false, false]);
  assert.equal(f('pause').canKill, true);
  assert.equal(f('lost').canKill, true);
  assert.deepEqual([f('ready').canLand, f('ready').canDiscard], [true, true]);
  assert.deepEqual([f('conflict').canLand, f('conflict').canDiscard, f('conflict').showConflict], [false, true, true]);
  assert.equal(f('landed').showLanded, true);
  assert.equal(f('landed').actionNote, 'PATCH LANDED · NO ACTIONS');
  assert.equal(f('discarded').actionNote, 'WORKTREE DISCARDED · NO ACTIONS');
  assert.equal(f('over').actionNote, 'JOB ENDED · NO ACTIONS');
  assert.equal(f('clear').actionNote, 'READ JOB · NOTHING TO LAND');
  const meta = Object.fromEntries(f('ready').meta.map(m => [m.k, m.v]));
  assert.equal(meta.WORKTREE, '/Users/me/.cache/farmout/worktrees/20260923-130500-cursor-0a1b');
  assert.equal(meta['JOB ID'], '20260923-130500-cursor-0a1b');
  assert.equal(meta.TOKENS, '96,410');
  assert.equal(f('play').readOnly, true);
});

test('confirm modal copy', () => {
  const j = state().jobs.find(x => x.pose === 'ready');
  const land = confirmVM('land', j);
  assert.equal(land.title, 'LAND PATCH?');
  assert.equal(land.files.length, 3);
  assert.equal(land.body, "Applies 3 files from CURSOR job 20260923-130500-cursor-0a1b to notes-app's current checkout as uncommitted changes. Nothing is committed.");
  assert.equal(confirmVM('kill', j).hasFiles, false);
  assert.equal(confirmVM('discard', j).yes, 'DISCARD');
});

test('land copy names the checkout, never the source branch', () => {
  const j = job({ mode: 'write', pose: 'ready', status: 'ok', repo: 'app', branch: 'feature/x', files: [{ n: 'a', a: 1, d: 0 }] });
  assert.doesNotMatch(confirmVM('land', j).body, /feature\/x/);
  const landed = focusJobVM({ ...j, pose: 'landed' }, 0, '1').landedMsg;
  assert.equal(landed, "1 files applied to app's current checkout as uncommitted changes.");
  assert.doesNotMatch(landed, /feature\/x/);
});

test('kill copy mentions the worktree only for write jobs', () => {
  const w = job({ id: 'J1', cli: 'codex', mode: 'write' });
  const r = job({ id: 'J2', cli: 'kiro', mode: 'read' });
  assert.equal(confirmVM('kill', w).body, 'Stops CODEX job J1 now. Partial changes stay in its worktree.');
  assert.equal(confirmVM('kill', r).body, 'Stops KIRO job J2 now.');
});

test('action message: a null code never reads as HTTP 200', () => {
  const timedOut = { ok: false, code: null, timed_out: true, out: '', err: '\n(timed out after 120s)' };
  assert.equal(actionMsgVM('kill', 200, timedOut), 'KILL TIMED OUT · (timed out after 120s)');
  const spawnFailed = { ok: false, code: null, timed_out: false, out: '', err: 'No such file or directory' };
  assert.equal(actionMsgVM('land', 200, spawnFailed), 'LAND FAILED · No such file or directory');
  assert.equal(actionMsgVM('land', 200, { ok: false, code: null, err: '' }), 'LAND FAILED');
  for (const b of [timedOut, spawnFailed]) assert.doesNotMatch(actionMsgVM('kill', 200, b), /HTTP/);
  assert.equal(actionMsgVM('discard', 200, { ok: false, code: 2, err: 'no such job' }), 'DISCARD FAILED (EXIT 2) · no such job');
  assert.equal(actionMsgVM('land', 200, { ok: true, code: 0 }), null);
  assert.equal(actionMsgVM('land', 200, { ok: false, conflict: true, code: 3 }), 'CONFLICT · PATCH DOES NOT APPLY');
  assert.equal(actionMsgVM('kill', 409, { error: 'busy' }), 'ANOTHER ACTION IS RUNNING ON THIS JOB');
  assert.equal(actionMsgVM('kill', 403, { error: 'missing token' }), 'KILL FAILED (HTTP 403) · missing token');
  assert.equal(actionMsgVM('kill', 200, null), 'KILL FAILED · BAD RESPONSE');
});

const CFG = {
  version: 1,
  workers: {
    codex: { enabled: true, model: null, effort: null, timeout_min: 30, models: ['gpt-5'] },
    kiro: { enabled: true, model: null, effort: 'high', timeout_min: 30, models: [] },
    copilot: { enabled: false, model: null, effort: null, timeout_min: 30, models: [] },
    cursor: { enabled: true, model: 'composer-1', effort: null, timeout_min: 240, models: [] },
  },
  routing: [{ kind: 'review', prefer: 'codex', fallback: null }, { kind: 'research', prefer: 'kiro', fallback: 'codex' }],
  limits: { stall_min: 10, max_jobs: 4 },
};

test('config draft ops are pure and map UI values', () => {
  const O = configDraftOps;
  const snap = JSON.stringify(CFG);
  assert.equal(O.toggle(CFG, 'codex').workers.codex.enabled, false);
  assert.equal(O.setModel(CFG, 'codex', 'gpt-5').workers.codex.model, 'gpt-5');
  assert.equal(O.setModel(CFG, 'cursor', 'DEFAULT').workers.cursor.model, null);
  assert.equal(O.setEffort(CFG, 'codex', 'MED').workers.codex.effort, 'medium');
  assert.equal(O.setEffort(CFG, 'kiro', 'HIGH').workers.kiro.effort, null);
  assert.equal(O.bumpTimeout(CFG, 'codex', 5).workers.codex.timeout_min, 35);
  assert.equal(O.bumpTimeout(CFG, 'cursor', 5).workers.cursor.timeout_min, 240);
  assert.equal(O.bumpTimeout(O.bumpTimeout(CFG, 'codex', -30), 'codex', -5).workers.codex.timeout_min, 1);
  assert.equal(O.setRule(CFG, 0, 'fallback', '--').routing[0].fallback, null);
  assert.equal(O.setRule(CFG, 0, 'prefer', 'kiro').routing[0].prefer, 'kiro');
  assert.deepEqual(O.moveRule(CFG, 1, -1).routing.map(r => r.kind), ['research', 'review']);
  assert.deepEqual(O.moveRule(CFG, 0, -1).routing.map(r => r.kind), ['review', 'research']);
  assert.equal(O.delRule(CFG, 0).routing.length, 1);
  assert.deepEqual(O.addRule(CFG).routing[2], { kind: 'review', prefer: 'codex', fallback: null });
  assert.equal(O.bumpLimit(CFG, 'stall_min', 200).limits.stall_min, 120);
  assert.equal(O.bumpLimit(CFG, 'max_jobs', -10).limits.max_jobs, 1);
  assert.equal(O.bumpLimit(CFG, 'max_jobs', 100).limits.max_jobs, 32);
  assert.equal(JSON.stringify(CFG), snap);
});

test('setup view model', () => {
  const doctor = [{ cli: 'codex', status: 'ok', version: '0.155.1' }, { cli: 'kiro', status: 'logged-out', version: null }];
  const v = setupVM(CFG, CFG, doctor, false);
  assert.deepEqual(v.workers.map(w => w.cli), ['CODEX', 'KIRO', 'COPILOT', 'CURSOR']);
  assert.equal(v.workers[0].health, 'OK');
  assert.equal(v.workers[0].version, '0.155.1');
  assert.equal(v.workers[1].health, 'LOGGED OUT');
  assert.equal(v.workers[1].version, '--');
  assert.equal(v.workers[2].health, '--');
  assert.equal(v.workers[2].rowOp, '0.5');
  assert.deepEqual(v.workers[0].models, ['DEFAULT', 'gpt-5']);
  assert.equal(v.workers[0].model, 'DEFAULT');
  assert.deepEqual(v.workers[3].models, ['DEFAULT', 'composer-1']);
  assert.deepEqual(v.workers[1].efforts.map(e => e.on), [false, false, true]);
  assert.equal(v.rules[0].fb, '--');
  assert.equal(v.rules[0].n, '01');
  assert.equal(v.cliOpts.find(o => o.v === 'copilot').l, 'COPILOT (OFF)');
  assert.equal(v.fbOpts[0].v, '--');
  assert.equal(setupVM(CFG, CFG, null, true).workers[0].health, 'CHECKING…');
  assert.equal(isDirty(CFG, CFG), false);
  assert.equal(isDirty(configDraftOps.toggle(CFG, 'kiro'), CFG), true);
});

test('sprite grids are 14x14 for every pose and kind', () => {
  for (const kind of ['claude', 'worker'])
    for (const pose of ['play', 'pause', 'lost', 'clear', 'over', 'conflict', 'discarded', 'think', 'turn', 'idle'])
      for (const f of [0, 1, 2, 3, 5]) {
        const g = poseGrid(kind, pose, f);
        assert.equal(g.length, 14);
        for (const row of g) assert.equal(row.length, 14);
      }
  assert.match(shadowOf('worker', 'play', 0, 5), /px 0 0 #/);
});

test('READ ONLY keys on mode only; unknown write files render "--"', () => {
  const w = job({ mode: 'write', files: null, pose: 'ready', status: 'ok' });
  assert.equal(tileVM(w, 'worker', 0, { blink: '1' }).items, '-- ITEMS');
  assert.equal(tileVM(job({ mode: 'read', files: [] }), 'worker', 0, { blink: '1' }).items, 'READ ONLY');
  assert.match(focusJobVM({ ...w, pose: 'landed' }, 0, '1').landedMsg, /^-- files applied/);
  assert.match(confirmVM('land', w).body, /^Applies -- files from/);
  assert.match(confirmVM('land', { ...w, files: [] }).body, /^Applies 0 files from/);
  assert.equal(tileVM(sess({ files: null }), 'claude', 0, { blink: '1' }).items, '-- ITEMS');
});

test('null limits are a no-op; null timeout bumps from the default', () => {
  const cfg = { ...CFG, limits: { stall_min: null, max_jobs: 4 },
    workers: { ...CFG.workers, codex: { ...CFG.workers.codex, timeout_min: null } } };
  assert.equal(configDraftOps.bumpLimit(cfg, 'stall_min', 1).limits.stall_min, null);
  assert.equal(configDraftOps.bumpTimeout(cfg, 'codex', 5).workers.codex.timeout_min, DEFAULT_TIMEOUT_MIN + 5);
});

test('bench: suspended, ended and idle sessions auto-bench; live ones and jobs do not', () => {
  for (const st of ['SUSPENDED', 'ENDED', 'idle']) assert.equal(autoBenched('claude', sess({ status: st })), true, st);
  for (const st of ['play', 'think', 'turn']) assert.equal(autoBenched('claude', sess({ status: st })), false, st);
  assert.equal(autoBenched('worker', job({})), false);
});

test('bench: manual bench and return override the automatic rule', () => {
  const live = sess({ id: 'a', status: 'play' }), susp = sess({ id: 'b', status: 'SUSPENDED' });
  let b = benchOps.bench({}, 'a');
  assert.equal(isBenched('claude', live, b), true);
  b = benchOps.ret(b, 'b');
  assert.equal(isBenched('claude', susp, b), false);
  assert.deepEqual(b, { benched: ['a'], returned: ['b'] });
  b = benchOps.ret(b, 'a');
  assert.equal(isBenched('claude', live, b), false);
  assert.deepEqual(b.benched, []);
});

test('rows: view filters bench vs play and counts both', () => {
  const s = { sessions: [sess({ id: 's1', repo: 'a', status: 'turn' }), sess({ id: 's2', repo: 'a', status: 'SUSPENDED' })], jobs: [job({ id: 'j1', repo: 'a' })] };
  const play = rows(s, 2, 0);
  assert.deepEqual(play.pageRows[0].tiles.filter(t => t.real).map(t => t.rec.id), ['s1', 'j1']);
  assert.equal(play.nPlay, 2); assert.equal(play.nBench, 1); assert.equal(play.records, 3);
  const bench = rows(s, 2, 0, 'bench', {});
  assert.deepEqual(bench.pageRows[0].tiles.filter(t => t.real).map(t => t.rec.id), ['s2']);
  assert.equal(rows(s, 2, 0, 'bench', { benched: ['j1'] }).nBench, 2);
});

test('tile: title carries the task, model sits in its own row value', () => {
  const t = tileVM(sess({ title: 'Fix login', model: 'claude-opus-5-5', status: 'play' }), 'claude', 0, { blink: '1' });
  assert.equal(t.mission, 'Fix login');
  assert.equal(t.model, 'claude-opus-5-5');
  assert.equal(t.sub, 'SESSION · NOTES-APP');
  assert.equal(tileVM(sess({ status: 'SUSPENDED' }), 'claude', 0, { blink: '1' }).dim, '0.55');
});

test('focus job: error from meta reaches the view', () => {
  assert.equal(focusJobVM(job({ status: 'failed', pose: 'over', error: 'worker exited with code 1' }), 0, '1').error, 'worker exited with code 1');
  assert.equal(focusJobVM(job({ error: null }), 0, '1').error, null);
});

test('session END: only suspended sessions offer it, with its own confirm and 409 text', () => {
  assert.equal(focusSessionVM(sess({ status: 'SUSPENDED' }), {}, 0, '1').canEnd, true);
  assert.equal(focusSessionVM(sess({ status: 'turn' }), {}, 0, '1').canEnd, false);
  const cf = confirmVM('end', sess({ title: 'Push service' }));
  assert.equal(cf.title, 'END SESSION?');
  assert.match(cf.body, /Push service/);
  assert.equal(actionMsgVM('end', 409, { ok: false }), 'SESSION IS NOT SUSPENDED ANYMORE');
  assert.equal(actionMsgVM('kill', 409, {}), 'ANOTHER ACTION IS RUNNING ON THIS JOB');
  assert.equal(actionMsgVM('end', 200, { ok: false, survivors: [1], err: 'still running after SIGTERM' }), 'END FAILED · still running after SIGTERM');
});

test('focus job: out files reach the view, none by default', () => {
  assert.deepEqual(focusJobVM(job({ out: ['/j/out/report.md'] }), 0, '1').out, ['/j/out/report.md']);
  assert.deepEqual(focusJobVM(job({}), 0, '1').out, []);
});
