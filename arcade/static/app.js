import { html, render, Component } from './vendor/htm-preact-standalone.mjs';
import {
  C, clock, rows, hud, benchOps, hofVM, tileVM, findRecord, focusJobVM, focusSessionVM, confirmVM,
  configDraftOps, setupVM, isDirty, TIMEOUT_STEP, actionMsgVM,
} from './vm.js';
import { Cabinet } from './cabinet.js';
import { FocusJob, FocusSession } from './focus.js';
import { Setup } from './setup.js';
import { Confirm } from './modal.js';

const POLL_MS = 1000;
const NO_SIGNAL_AFTER = 3;
const POLL_TIMEOUT_MS = 5000;
// The server's action timeout is 120 s; leave headroom for its response.
const ACTION_TIMEOUT_MS = 130000;
const FRAME_MS = 420;
const HEADER_H = 72, HOF_H = 186, PAGER_H = 30;
const TOKEN = new URLSearchParams(location.search).get('t') || '';
const TABS = ['workers', 'routing', 'limits', 'machine'];
const BENCH_KEY = 'arcade.bench';
const loadBench = () => { try { const b = JSON.parse(localStorage.getItem(BENCH_KEY)); return b && typeof b === 'object' ? b : {}; } catch (e) { return {}; } };
const saveBench = b => { try { localStorage.setItem(BENCH_KEY, JSON.stringify(b)); } catch (e) { /* per-viewer convenience only */ } };
const SND = { toggle: [[880, 1320], 0.06], save: [[660, 990], 0.07], win: [[523, 659, 784, 1047], 0.09], lose: [[392, 330, 262], 0.14] };

// Resolves {r, body}; the timeout also covers reading the body, and rejects on expiry.
async function api(path, opts = {}, timeoutMs = POLL_TIMEOUT_MS) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(new Error(`TIMEOUT AFTER ${timeoutMs / 1000}S`)), timeoutMs);
  try {
    const r = await fetch(path, { ...opts, cache: 'no-store', signal: ctl.signal, headers: { 'X-Arcade-Token': TOKEN, ...(opts.headers || {}) } });
    let body = null;
    try { body = await r.json(); } catch (e) { if (ctl.signal.aborted) throw ctl.signal.reason; }
    return { r, body };
  } finally {
    clearTimeout(timer);
  }
}
const errText = e => String((e && e.message) || e);

// GET /api/config: accept {config, etag} or the flat config carrying its etag.
function splitConfig(body, r) {
  const etag = (body && body.etag) || r.headers.get('ETag');
  let cfg = body && body.config ? body.config : { ...(body || {}) };
  delete cfg.etag;
  return { cfg, etag };
}

class App extends Component {
  constructor() {
    super();
    this.state = {
      data: null, fails: 0, lastErr: '', f: 0, sound: false, screen: 'cabinet', focusId: null, page: 0, rpp: 2, px: 5,
      confirm: null, busy: false, actionMsg: null, saving: false,
      saved: null, draft: null, etag: null, cfgStale: false, cfgErr: null, tab: 'workers', savedNote: 'ALL CHANGES SAVED',
      doctor: null, checking: false, lastCheck: null, view: 'play', bench: loadBench(),
    };
  }

  componentDidMount() {
    this.onResize = () => {
      const H = Math.max(900, window.innerHeight);
      const avail = H - HEADER_H - 20 - PAGER_H - 10 - HOF_H - 10;
      const rpp = avail >= 930 ? 3 : 2;
      const tileH = avail / rpp - 32;
      const px = tileH < 280 ? 5 : tileH < 350 ? 6 : 7;
      if (rpp !== this.state.rpp || px !== this.state.px) this.setState({ rpp, px });
    };
    this.onResize(); window.addEventListener('resize', this.onResize);
    this.frameT = setInterval(() => this.setState(s => ({ f: s.f + 1 })), FRAME_MS);
    this.onKey = e => {
      const S = this.state;
      if (e.key === 'Escape') { if (S.confirm) this.setState({ confirm: null }); else this.home(); }
      if (S.screen === 'cabinet' && e.key === 'ArrowRight') this.nav(1);
      if (S.screen === 'cabinet' && e.key === 'ArrowLeft') this.nav(-1);
      const inField = e.target && /^(SELECT|INPUT|TEXTAREA)$/.test(e.target.tagName);
      if (S.screen === 'setup' && !inField && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        e.preventDefault();
        const i = TABS.indexOf(S.tab) + (e.key === 'ArrowDown' ? 1 : -1);
        this.setState({ tab: TABS[(i + TABS.length) % TABS.length] });
      }
    };
    window.addEventListener('keydown', this.onKey);
    this.poll();
  }

  componentWillUnmount() {
    this.dead = true;
    clearInterval(this.frameT); clearTimeout(this.pollT);
    window.removeEventListener('keydown', this.onKey); window.removeEventListener('resize', this.onResize);
  }

  async poll() {
    try {
      const { r, body: data } = await api('/api/state');
      if (!r.ok || !data) throw new Error(r.ok ? 'BAD JSON' : `HTTP ${r.status}`);
      this.setState(s => {
        const lost = s.screen === 'focus' && !findRecord(data, s.focusId);
        return { data, fails: 0, lastErr: '', ...(lost ? { screen: 'cabinet', focusId: null, confirm: null } : {}) };
      });
    } catch (e) {
      this.setState(s => ({ fails: s.fails + 1, lastErr: errText(e) }));
    } finally {
      if (!this.dead) this.pollT = setTimeout(() => this.poll(), POLL_MS);
    }
  }

  nav(d) { this.setState(s => ({ page: s.page + d })); }
  home() { this.setState({ screen: 'cabinet', focusId: null, confirm: null }); }
  open(id) { this.setState({ screen: 'focus', focusId: id, actionMsg: null }); }

  beep([seq, dur]) {
    if (!this.state.sound) return;
    try {
      const ctx = this.ctx || (this.ctx = new (window.AudioContext || window.webkitAudioContext)());
      if (ctx.state === 'suspended') ctx.resume();
      let t = ctx.currentTime;
      seq.forEach(fq => { const o = ctx.createOscillator(), g = ctx.createGain(); o.type = 'square'; o.frequency.value = fq; g.gain.setValueAtTime(0.05, t); g.gain.exponentialRampToValueAtTime(0.001, t + dur); o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + dur); t += dur; });
    } catch (e) { /* audio is optional */ }
  }

  async act() {
    const { confirm, busy } = this.state;
    if (!confirm || busy) { this.setState({ confirm: null }); return; }
    const { type, id } = confirm;
    this.setState({ confirm: null, busy: true, actionMsg: null });
    let msg = null, won = false;
    try {
      const path = type === 'end' ? `/api/sessions/${encodeURIComponent(id)}/end` : `/api/jobs/${encodeURIComponent(id)}/${type}`;
      const { r, body } = await api(path, { method: 'POST' }, ACTION_TIMEOUT_MS);
      msg = actionMsgVM(type, r.status, body);
      won = msg === null && type === 'land';
    } catch (e) {
      msg = `${type.toUpperCase()} FAILED · ${errText(e)}`;
    }
    this.setState({ busy: false, actionMsg: msg });
    this.beep(won ? SND.win : SND.lose);
  }

  async openSetup() {
    this.setState({ screen: 'setup', focusId: null, confirm: null });
    if (!this.state.saved) await this.loadConfig();
  }

  async loadConfig() {
    try {
      const { r, body } = await api('/api/config');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const { cfg, etag } = splitConfig(body, r);
      this.setState({ saved: cfg, draft: JSON.parse(JSON.stringify(cfg)), etag, cfgStale: false, cfgErr: null });
    } catch (e) {
      this.setState({ cfgErr: `CAN'T READ /api/config · ${errText(e)}` });
    }
  }

  async save() {
    const { draft, saved, etag, saving } = this.state;
    if (saving || !draft || !isDirty(draft, saved)) return;
    const sent = JSON.parse(JSON.stringify(draft));
    this.setState({ saving: true });
    try {
      const { r, body } = await api('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-Match': etag || '' }, body: JSON.stringify(sent) });
      if (r.status === 409) { this.setState({ cfgStale: true, cfgErr: 'CHANGED ON DISK · REVERT TO RELOAD' }); return; }
      if (!r.ok) {
        const errs = body && (body.errors || body.error);
        this.setState({ cfgErr: `NOT SAVED (HTTP ${r.status})${errs ? ' · ' + [].concat(errs).join('; ') : ''}` });
        return;
      }
      const next = (body && body.etag) || r.headers.get('ETag');
      this.setState({ saved: sent, etag: next, cfgErr: null, savedNote: `SAVED ${clock(Date.now())}` });
      this.beep(SND.save);
    } catch (e) {
      this.setState({ cfgErr: `NOT SAVED · ${errText(e)}` });
    } finally {
      this.setState({ saving: false });
    }
  }

  revert() {
    if (this.state.cfgStale || !this.state.saved) { this.loadConfig(); return; }
    if (!isDirty(this.state.draft, this.state.saved)) return;
    this.setState(s => ({ draft: JSON.parse(JSON.stringify(s.saved)), cfgErr: null }));
  }

  async runDoctor() {
    if (this.state.checking) return;
    this.setState({ checking: true });
    try {
      const { r, body: doctor } = await api('/api/doctor', { method: 'POST' }, ACTION_TIMEOUT_MS);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      this.setState({ doctor: Array.isArray(doctor) ? doctor : [], lastCheck: Date.now(), checking: false });
    } catch (e) {
      this.setState({ checking: false, cfgErr: `DOCTOR FAILED · ${errText(e)}` });
    }
  }

  draftOps() {
    const wrap = fn => (...args) => this.setState(s => (s.draft ? { draft: fn(s.draft, ...args) } : null));
    const O = configDraftOps;
    return {
      toggle: wrap(O.toggle), setModel: wrap(O.setModel), setEffort: wrap(O.setEffort),
      bumpTimeout: wrap((d, cli, dir) => O.bumpTimeout(d, cli, dir * TIMEOUT_STEP)),
      setRule: wrap(O.setRule), moveRule: wrap(O.moveRule), delRule: wrap(O.delRule), addRule: wrap(O.addRule),
      bumpLimit: wrap(O.bumpLimit),
    };
  }

  render(_, S) {
    const blink = S.f % 2 ? '1' : '0.3';
    const noSignal = S.fails >= NO_SIGNAL_AFTER;
    const data = noSignal ? null : S.data;
    const hudv = hud(data);
    const found = S.screen === 'focus' ? findRecord(S.data, S.focusId) : null;
    const screen = S.screen === 'focus' && !found ? 'cabinet' : S.screen;
    const screenLabel = screen === 'setup' ? 'OPERATOR MENU' : screen === 'focus' ? (found.kind === 'claude' ? 'FOCUS · SESSION' : 'FOCUS · JOB') : 'CABINET';
    const hdrBtn = 'font-family:\'Press Start 2P\',monospace; font-size:9px; padding:8px 10px; border:2px solid var(--color-bg); cursor:pointer;';

    let body = null;
    if (screen === 'cabinet') {
      const r = rows(data, S.rpp, S.page, S.view, S.bench);
      const onBenchView = S.view === 'bench';
      const tiles = r.pageRows.map(row => !row.real ? row : ({
        ...row, tiles: row.tiles.map(t => t.real ? tileVM(t.rec, t.kind, S.f, { blink, state: data }) : t),
      }));
      body = html`<${Cabinet} v=${{
        dataLive: !noSignal && !!data && r.records > 0, dataEmpty: !noSignal && !!data && r.records === 0, noSignal,
        view: S.view, nPlay: r.nPlay, nBench: r.nBench, viewEmpty: r.total === 0,
        viewEmptyTitle: onBenchView ? 'BENCH IS EMPTY' : 'NOTHING IN PLAY',
        viewEmptyText: onBenchView ? 'Idle and ended sessions move here, and so does any tile you bench.' : 'Every session is on the bench.',
        benchLabel: onBenchView ? 'RETURN' : 'BENCH',
        onView: view => { this.setState({ view, page: 0 }); },
        onBench: id => this.setState(s => { const bench = (onBenchView ? benchOps.ret : benchOps.bench)(s.bench, id); saveBench(bench); return { bench }; }),
        pageRows: tiles, blink, px: S.px, stageMin: (S.px * 14 + 6) + 'px', frame: S.f, hud: hudv,
        errors: (data && data.errors) || [],
        pageDots: Array.from({ length: r.nPages }, (_, i) => (i === r.page ? C.R : C.G6)),
        pageLabel: `PAGE ${r.page + 1}/${r.nPages}`, pagerOp: r.nPages > 1 ? '1' : '0.3',
        onGo: i => { this.setState({ page: i }); }, onNav: d => this.nav(d), onOpen: id => this.open(id),
        hof: hofVM(data), noSignalMsg: `Can't reach /api/state${S.lastErr ? ` (${S.lastErr})` : ''}`,
        retry: Math.ceil(POLL_MS / 1000), staticAngle: (S.f * 37 % 180) + 'deg',
      }} />`;
    } else if (screen === 'focus' && found.kind === 'worker') {
      body = html`<${FocusJob} fw=${focusJobVM(found.rec, S.f, blink)} onBack=${() => this.home()} busy=${S.busy} actionMsg=${S.busy ? 'WORKING…' : S.actionMsg}
        onAsk=${type => { if (!this.state.busy) this.setState({ confirm: { type, id: found.rec.id } }); }} />`;
    } else if (screen === 'focus') {
      body = html`<${FocusSession} fc=${focusSessionVM(found.rec, S.data, S.f, blink)} onBack=${() => this.home()} onOpen=${id => this.open(id)}
        busy=${S.busy} actionMsg=${S.busy ? null : S.actionMsg}
        onAsk=${type => { if (!this.state.busy) this.setState({ confirm: { type, id: found.rec.id } }); }} />`;
    } else {
      const sv = S.draft ? setupVM(S.draft, S.saved, S.doctor, S.checking) : null;
      const dirty = !!(sv && sv.dirty);
      const machine = S.data && S.data.machine ? S.data.machine : '--';
      const hints = { workers: sv ? `${sv.workers.length} CLIS` : '--', routing: sv ? `${sv.rules.length} RULES` : '--', limits: '2 SET', machine: machine.toUpperCase() };
      body = html`<${Setup} v=${{
        sv, tab: S.tab, blink, dirty, machine, ops: this.draftOps(), cfgErr: S.cfgErr,
        cfgMissing: S.cfgErr || 'LOADING CONFIG…',
        menu: TABS.map(k => ({ k, label: k.toUpperCase(), hint: hints[k], cur: k === S.tab ? '1' : '0', bg: k === S.tab ? C.G8 : 'transparent' })),
        onTab: k => this.setState({ tab: k }),
        doctorLabel: S.checking ? 'CHECKING…' : 'RUN DOCTOR', doctorNote: S.lastCheck ? `LAST CHECK ${clock(S.lastCheck)}` : 'NOT CHECKED YET',
        runDoctor: () => this.runDoctor(),
        savedNote: S.savedNote, dirtyOp: dirty && !S.saving ? '1' : '0.35', revertOp: dirty || S.cfgStale ? '1' : '0.35',
        back: () => this.home(), revert: () => this.revert(), save: () => this.save(),
      }} />`;
    }

    const rec = S.confirm && findRecord(S.data, S.confirm.id);
    const cf = rec ? confirmVM(S.confirm.type, rec.rec) : null;

    return html`
      <div data-screen-label="Farmout Arcade" style="position:relative; width:100%; min-width:1440px; height:100vh; min-height:900px; display:flex; flex-direction:column; background:#0e0d0d; color:var(--color-bg); font-family:var(--font-body); overflow:hidden;">
        <div style="height:72px; flex:none; display:flex; align-items:center; gap:32px; padding:0 22px; background:var(--color-accent); color:var(--color-bg);">
          <div onClick=${() => this.home()} style="font-size:34px; font-weight:800; letter-spacing:-0.02em; line-height:1; cursor:pointer;">FARMOUT ARCADE</div>
          <div style="font-family:'Press Start 2P',monospace; font-size:9px; padding:6px 7px; border:2px solid var(--color-bg);">${screenLabel}</div>
          <div style="flex:1;"></div>
          <div style="display:flex; flex-direction:column; gap:7px; font-family:'Press Start 2P',monospace; font-size:8px;">HI-SCORE<span style="font-size:14px;">${hudv.score}</span></div>
          <div style="display:flex; flex-direction:column; gap:7px; font-family:'Press Start 2P',monospace; font-size:8px;">IN PLAY<span style="font-size:14px;">${hudv.inPlay}</span></div>
          <div style="display:flex; flex-direction:column; gap:7px; font-family:'Press Start 2P',monospace; font-size:8px;">COINS<span style="font-size:14px;">${hudv.coins}</span></div>
          <div style="display:flex; gap:8px;">
            <div class="h-acc6" onClick=${() => this.openSetup()} style=${hdrBtn}>SETUP</div>
            <div class="h-acc6" onClick=${() => { const on = !S.sound; this.setState({ sound: on }, () => { if (on) this.beep(SND.toggle); }); }} style=${hdrBtn}>${S.sound ? 'SND ON' : 'SND OFF'}</div>
          </div>
        </div>
        ${body}
        ${cf && html`<${Confirm} cf=${cf} onYes=${() => this.act()} onNo=${() => this.setState({ confirm: null })} />`}
      </div>`;
  }
}

render(html`<${App} />`, document.getElementById('app'));
