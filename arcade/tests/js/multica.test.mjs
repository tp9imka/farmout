import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  C, tileVM, tagFor, stageOf, spritePose, rows, findRecord, focusAgentVM, autopilotsVM, confirmVM,
  actionMsgVM, autoBenched, quickFor, mcStatusVM, mcAgents,
} from '../../static/vm.js';
import { poseGrid } from '../../static/sprite.js';

const DEMO = fileURLToPath(new URL('../../demo/state.json', import.meta.url));
const demo = () => JSON.parse(readFileSync(DEMO, 'utf8'));
const agent = (name, over = {}) => ({ ...demo().multica.agents.find(a => a.name === name), ...over });

test('agent status maps to stage, pose and tag', () => {
  assert.equal(stageOf('agent', agent('Builder')), 'play');
  assert.equal(spritePose('agent', agent('Builder')), 'play');
  assert.equal(stageOf('agent', agent('Reviewer')), 'queued');
  assert.equal(spritePose('agent', agent('Reviewer')), 'think');
  assert.equal(spritePose('agent', agent('Nightly')), 'lost');
  assert.equal(spritePose('agent', agent('Janitor')), 'idle');
  assert.equal(tagFor('agent', agent('Orchestrator'), '1').label, 'WORKING · +1');
  assert.equal(tagFor('agent', agent('Reviewer'), '1').label, 'QUEUED · 1');
  assert.equal(tagFor('agent', agent('Janitor'), '1').label, 'IDLE · LAST FAILED');
  assert.equal(tagFor('agent', agent('Nightly'), '1').label, 'OFFLINE');
  assert.equal(tagFor('agent', agent('Janitor', { last: null }), '1').label, 'IDLE');
});

test('agent tile shows the issue, the live tool and its quick actions', () => {
  const s = demo();
  const t = tileVM(agent('Orchestrator'), 'agent', 0, { state: s });
  assert.equal(t.isAgent, true);
  assert.equal(t.badge, 'AGENT');
  assert.equal(t.mission, 'ACM-41 · Check my open pull requests');
  assert.equal(t.tool, 'Bash');
  assert.equal(t.sub, 'MULTICA · CLAUDE · LAPTOP');
  assert.equal(t.lastMsg, 'Pushed the lint fix to #215; waiting on CI');
  assert.deepEqual(t.quick.map(q => q.label), ['CHECK MY PRS', 'CLEANUP & MERGE']);
  assert.equal(t.queue.length, 1);
  assert.equal(t.bColor, C.AMB);

  const idle = tileVM(agent('Janitor'), 'agent', 0, { state: s });
  assert.equal(idle.mission, 'LAST · ACM-35 · Prune stale branches');
  assert.equal(idle.lastMsg, 'ERROR · git push rejected: protected branch');
  const queued = tileVM(agent('Reviewer'), 'agent', 0, { state: s });
  assert.equal(queued.mission, 'ACM-39 · Review payments refactor');
  assert.equal(queued.tool, 'WAITING FOR RUNTIME');
  assert.equal(tileVM(agent('Nightly', { replay: [], queue: [], task: null, last: null }), 'agent', 0, { state: s }).mission, 'NO TASK');
});

test('quick actions can be limited to named agents, case-insensitively', () => {
  const s = demo();
  s.multica.quick_actions[1].agents = ['orchestrator'];
  assert.deepEqual(quickFor(agent('Orchestrator'), s).map(q => q.i), [0, 1]);
  assert.deepEqual(quickFor(agent('Builder'), s).map(q => q.i), [0]);
});

test('agents join the board after workers; idle and offline ones start on the bench', () => {
  const s = demo();
  assert.equal(autoBenched('agent', agent('Janitor')), true);
  assert.equal(autoBenched('agent', agent('Nightly')), true);
  assert.equal(autoBenched('agent', agent('Builder')), false);
  const r = rows(s, 10, 0, 'play', {});
  const kinds = r.pageRows.flatMap(row => (row.tiles || []).filter(t => t.real).map(t => t.kind));
  assert.deepEqual(kinds.slice(-3), ['agent', 'agent', 'agent']);
  assert.ok(kinds.lastIndexOf('worker') < kinds.indexOf('agent'));
  const bench = rows(s, 10, 0, 'bench', {});
  const benched = bench.pageRows.flatMap(row => (row.tiles || []).filter(t => t.kind === 'agent').map(t => t.rec.name));
  assert.deepEqual(benched.sort(), ['Janitor', 'Nightly']);
});

test('a disabled multica contributes no agents', () => {
  const s = demo();
  s.multica.enabled = false;
  assert.deepEqual(mcAgents(s), []);
  assert.equal(findRecord(s, s.multica.agents[0].id), null);
  assert.equal(mcStatusVM(s), null);
  assert.deepEqual(mcAgents({}), []);
});

test('findRecord resolves agents', () => {
  const s = demo();
  const a = s.multica.agents[1];
  assert.deepEqual(findRecord(s, a.id), { kind: 'agent', rec: a });
});

test('focus agent VM: meta, links, queue and cancel', () => {
  const s = demo();
  const fa = focusAgentVM(agent('Orchestrator'), s, 0, '1');
  assert.equal(fa.missionUrl, 'http://localhost:3000/acme/issues/dddddddd-0000-4000-8000-000000000001');
  assert.equal(fa.url, 'http://localhost:3000/acme/agents/aaaaaaaa-0000-4000-8000-000000000001');
  assert.equal(fa.canCancel, true);
  assert.equal(fa.cancelId, 'cccccccc-0000-4000-8000-000000000001');
  assert.equal(fa.queueList.length, 1);
  assert.equal(fa.queueList[0].issue, 'ACM-42 · Clean up and merge ready pull requests');
  assert.equal(fa.replay.length, 6);
  assert.equal(fa.meta.find(m => m.k === 'ATTEMPT').v, '1/2');
  const idle = focusAgentVM(agent('Janitor'), s, 0, '1');
  assert.equal(idle.canCancel, false);
  assert.equal(idle.mission, 'NO TASK RUNNING');
  assert.equal(idle.lastError, 'git push rejected: protected branch');
});

test('autopilots VM', () => {
  const rowsV = autopilotsVM(demo());
  assert.equal(rowsV.length, 3);
  assert.equal(rowsV[0].agent, 'ORCHESTRATOR');
  assert.equal(rowsV[1].triggers, 'SCHEDULE · WEBHOOK');
  assert.equal(rowsV[2].active, false);
  assert.equal(rowsV[2].next, '--');
  assert.deepEqual(autopilotsVM({}), []);
});

test('multica confirm dialogs and result lines', () => {
  const a = agent('Orchestrator');
  assert.equal(confirmVM('mc-cancel', a).title, 'CANCEL RUN?');
  assert.match(confirmVM('mc-cancel', a).body, /ACM-41/);
  const q = confirmVM('mc-quick', a, { title: 'Check my open pull requests', prompt: 'Go through' });
  assert.equal(q.yes, 'QUEUE');
  assert.match(q.body, /“Check my open pull requests” assigned to ORCHESTRATOR/);
  assert.match(q.body, /Brief: Go through/);
  assert.match(confirmVM('mc-trigger', demo().multica.autopilots[0]).body, /Morning PR sweep/);
  assert.equal(actionMsgVM('mc-quick', 400, { ok: false, err: 'unknown agent' }), 'QUEUE FAILED (HTTP 400) · unknown agent');
  assert.equal(actionMsgVM('mc-trigger', 200, { ok: false, err: 'SKIPPED · runtime offline' }), 'RUN NOW FAILED · SKIPPED · runtime offline');
  assert.equal(actionMsgVM('mc-cancel', 409, { ok: false }), 'ANOTHER ACTION IS RUNNING');
  assert.equal(actionMsgVM('mc-cancel', 200, { ok: true }), null);
});

test('status line', () => {
  const s = demo();
  assert.deepEqual(mcStatusVM(s), { label: 'MULTICA · ACME', color: C.W, title: 'http://localhost:8080' });
  s.multica.connected = false; s.multica.error = 'GET /api/agents: HTTP 401';
  assert.equal(mcStatusVM(s).label, 'MULTICA · OFFLINE');
  assert.equal(mcStatusVM(s).title, 'GET /api/agents: HTTP 401');
});

test('robot sprite: 14x14 in every pose, red eyes unless closed', () => {
  for (const pose of ['play', 'idle', 'think', 'lost', 'over', 'clear']) {
    const g = poseGrid('agent', pose, 1);
    assert.equal(g.length, 14);
    g.forEach(r => assert.equal(r.length, 14));
  }
  assert.equal(poseGrid('agent', 'play', 0)[4][4], 'r');
  assert.equal(poseGrid('agent', 'idle', 0)[4][4], 'k');
  assert.notDeepEqual(poseGrid('agent', 'play', 0), poseGrid('worker', 'play', 0));
});
