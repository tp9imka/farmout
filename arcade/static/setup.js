import { html } from './vendor/htm-preact-standalone.mjs';

const SELECT = 'min-width:0; font-family:var(--font-body); font-size:14px; padding:7px 8px; background:#0e0d0d; color:var(--color-bg); border:2px solid #605d5d; border-radius:0;';
const STEP = 'font-size:10px; padding:6px 9px; border:2px solid #605d5d; cursor:pointer;';
const WORKER_COLS = 'grid-template-columns:80px 110px 150px 100px minmax(0,1fr) 200px 170px;';
const RULE_COLS = 'grid-template-columns:44px minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) 150px;';

const Select = ({ value, opts, onChange }) => html`
  <select value=${value} onChange=${e => onChange(e.target.value)} style=${SELECT}>
    ${opts.map(o => html`<option value=${o.v}>${o.l}</option>`)}
  </select>`;

function Workers({ sv, ops, doctorNote, doctorLabel, runDoctor }) {
  return html`
    <div style="display:flex; align-items:center; gap:16px;">
      <div style="font-size:14px;">WORKERS</div>
      <div style="flex:1;"></div>
      <div style="font-size:8px; color:var(--color-neutral-500);">${doctorNote}</div>
      <div class="h-g8" onClick=${runDoctor} style="font-size:9px; padding:8px 10px; border:2px solid var(--color-bg); cursor:pointer;">${doctorLabel}</div>
    </div>
    <div style="display:flex; flex-direction:column;">
      <div style="display:grid; ${WORKER_COLS} gap:14px; padding:0 0 10px; font-size:7px; color:var(--color-neutral-500); border-bottom:2px solid #444141;">
        <div>ENABLED</div><div>CLI</div><div>DOCTOR</div><div>VERSION</div><div>DEFAULT MODEL</div><div>EFFORT</div><div>TIMEOUT</div>
      </div>
      ${sv.workers.map(w => html`
        <div style="display:grid; ${WORKER_COLS} gap:14px; align-items:center; padding:12px 0; border-bottom:2px solid #2d2b2b; opacity:${w.rowOp};">
          <div onClick=${() => ops.toggle(w.key)} style="justify-self:start; font-size:9px; padding:6px 8px; background:${w.onBg}; border:2px solid ${w.onBd}; cursor:pointer;">${w.onLabel}</div>
          <div style="font-size:11px;">${w.cli}</div>
          <div style="justify-self:start; font-size:7px; line-height:1; padding:4px 5px; background:${w.hBg}; color:${w.hFg}; border:2px ${w.hBs} ${w.hBd};">${w.health}</div>
          <div style="font-family:var(--font-body); font-size:14px; color:var(--color-neutral-300);">${w.version}</div>
          <${Select} value=${w.model} opts=${w.models.map(v => ({ v, l: v }))} onChange=${v => ops.setModel(w.key, v)} />
          <div style="display:flex; border:2px solid #605d5d;">
            ${w.efforts.map(e => html`<div onClick=${() => ops.setEffort(w.key, e.v)} style="flex:1; font-size:8px; padding:8px 0; text-align:center; background:${e.bg}; cursor:pointer;">${e.v}</div>`)}
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <div class="h-bdacc" onClick=${() => ops.bumpTimeout(w.key, -1)} style=${STEP}>−</div>
            <div style="font-size:10px; width:40px; text-align:center;">${w.timeout}</div>
            <div class="h-bdacc" onClick=${() => ops.bumpTimeout(w.key, 1)} style=${STEP}>+</div>
            <div style="font-size:7px; color:var(--color-neutral-500);">MIN</div>
          </div>
        </div>`)}
    </div>
    <div style="display:flex; gap:24px; font-size:7px; color:var(--color-neutral-500); line-height:1.8;">
      <div style="display:flex; align-items:center; gap:8px;"><div style="padding:3px 4px; background:var(--color-bg); color:#0e0d0d;">OK</div>LOGGED IN, CLI RESPONDS</div>
      <div style="display:flex; align-items:center; gap:8px;"><div style="padding:3px 4px; border:2px solid #f0a830; color:#f0a830;">NOT VERIFIED</div>INSTALLED, AUTH UNCHECKED</div>
      <div style="display:flex; align-items:center; gap:8px;"><div style="padding:3px 4px; border:2px solid var(--color-accent); color:var(--color-accent);">LOGGED OUT</div>RUN ITS LOGIN</div>
      <div style="display:flex; align-items:center; gap:8px;"><div style="padding:3px 4px; border:2px dashed #9b9797; color:#9b9797;">MISSING</div>NOT ON PATH</div>
    </div>`;
}

function Routing({ sv, ops }) {
  return html`
    <div style="display:flex; align-items:center; gap:16px;">
      <div style="font-size:14px;">ROUTING</div>
      <div style="font-family:var(--font-body); font-size:14px; color:var(--color-neutral-400);">First matching rule wins. Fallback runs when the preferred CLI is off, logged out or missing.</div>
    </div>
    <div style="display:flex; flex-direction:column;">
      <div style="display:grid; ${RULE_COLS} gap:14px; padding:0 0 10px; font-size:7px; color:var(--color-neutral-500); border-bottom:2px solid #444141;">
        <div>#</div><div>TASK KIND</div><div>PREFERRED</div><div>FALLBACK</div><div>ORDER</div>
      </div>
      ${sv.rules.map((r, i) => html`
        <div style="display:grid; ${RULE_COLS} gap:14px; align-items:center; padding:10px 0; border-bottom:2px solid #2d2b2b;">
          <div style="font-size:10px; color:var(--color-accent);">${r.n}</div>
          <${Select} value=${r.kind} opts=${sv.kinds.map(v => ({ v, l: v }))} onChange=${v => ops.setRule(i, 'kind', v)} />
          <${Select} value=${r.pref} opts=${sv.cliOpts} onChange=${v => ops.setRule(i, 'prefer', v)} />
          <${Select} value=${r.fb} opts=${sv.fbOpts} onChange=${v => ops.setRule(i, 'fallback', v)} />
          <div style="display:flex; gap:6px;">
            <div class="h-bdacc" onClick=${() => ops.moveRule(i, -1)} style="font-size:9px; padding:7px 9px; border:2px solid #605d5d; cursor:pointer; opacity:${r.upOp};">▲</div>
            <div class="h-bdacc" onClick=${() => ops.moveRule(i, 1)} style="font-size:9px; padding:7px 9px; border:2px solid #605d5d; cursor:pointer; opacity:${r.downOp};">▼</div>
            <div class="h-bdacc" onClick=${() => ops.delRule(i)} style="font-size:9px; padding:7px 9px; border:2px solid #605d5d; color:var(--color-accent); cursor:pointer;">✕</div>
          </div>
        </div>`)}
    </div>
    <div class="h-add" onClick=${() => ops.addRule()} style="align-self:flex-start; font-size:9px; padding:9px 12px; border:2px dashed #9b9797; cursor:pointer;">+ ADD RULE</div>`;
}

const LimitRow = ({ title, note, value, unit, onDown, onUp, top }) => html`
  <div style="display:grid; grid-template-columns:minmax(0,1fr) 240px; gap:20px; align-items:center; padding:16px 0; border-top:2px solid ${top};">
    <div style="display:flex; flex-direction:column; gap:8px;"><div style="font-size:11px;">${title}</div><div style="font-family:var(--font-body); font-size:14px; color:var(--color-neutral-400);">${note}</div></div>
    <div style="display:flex; align-items:center; gap:8px;">
      <div class="h-bdacc" onClick=${onDown} style=${STEP}>−</div>
      <div style="font-size:12px; width:44px; text-align:center;">${value}</div>
      <div class="h-bdacc" onClick=${onUp} style=${STEP}>+</div>
      <div style="font-size:7px; color:var(--color-neutral-500);">${unit}</div>
    </div>
  </div>`;

function Limits({ sv, ops }) {
  return html`
    <div style="font-size:14px;">LIMITS</div>
    <div style="display:flex; flex-direction:column;">
      <${LimitRow} title="STALL THRESHOLD" note="A running worker with a quiet log for this long shows PAUSE." value=${sv.lim.stall} unit="MIN" top="#444141"
        onDown=${() => ops.bumpLimit('stall_min', -1)} onUp=${() => ops.bumpLimit('stall_min', 1)} />
      <${LimitRow} title="MAX CONCURRENT JOBS" note="Across all Claude sessions on this machine." value=${sv.lim.maxJobs} unit="JOBS" top="#2d2b2b"
        onDown=${() => ops.bumpLimit('max_jobs', -1)} onUp=${() => ops.bumpLimit('max_jobs', 1)} />
      <div style="display:grid; grid-template-columns:minmax(0,1fr) 240px; gap:20px; align-items:center; padding:16px 0; border-top:2px solid #2d2b2b; border-bottom:2px solid #2d2b2b;">
        <div style="display:flex; flex-direction:column; gap:8px;"><div style="font-size:11px;">KEEP UNLANDED WRITE JOBS ON CLEAN</div><div style="font-family:var(--font-body); font-size:14px; color:var(--color-neutral-400);">Clean skips worktrees whose patch was never landed or discarded. Only clean --all removes them.</div></div>
        <div style="display:flex; align-items:center; gap:10px;">
          <div style="justify-self:start; font-size:9px; padding:6px 8px; background:#ec3013; border:2px solid #ec3013;">ALWAYS ON</div>
          <div style="font-size:7px; padding:3px 4px; border:2px solid #9b9797; color:#9b9797;">READ-ONLY</div>
        </div>
      </div>
    </div>`;
}

const Machine = ({ machine }) => html`
  <div style="font-size:14px;">MACHINE</div>
  <div style="display:flex; flex-direction:column; gap:14px; padding:20px 0; border-top:2px solid #444141; border-bottom:2px solid #2d2b2b;">
    <div style="display:flex; align-items:center; gap:12px;"><div style="font-size:8px; color:var(--color-neutral-500);">MACHINE KEY</div><div style="font-size:7px; padding:3px 4px; border:2px solid #9b9797; color:#9b9797;">READ-ONLY</div></div>
    <div style="font-size:36px;">${machine}</div>
  </div>`;

export function Setup({ v }) {
  const { sv, tab } = v;
  return html`
    <div style="flex:1; min-height:0; display:flex; flex-direction:column; gap:10px; padding:10px; font-family:'Press Start 2P',monospace;">
      <div style="flex:1; min-height:0; display:grid; grid-template-columns:320px minmax(0,1fr); gap:10px;">
        <div style="position:relative; display:flex; flex-direction:column; gap:6px; padding:22px 20px; background:#1a1817; border:2px solid var(--color-accent);">
          <div style="font-size:16px; line-height:1.4;">OPERATOR<br />MENU</div>
          <div style="font-size:8px; color:var(--color-neutral-500); margin-bottom:22px;">SERVICE MODE</div>
          ${v.menu.map(m => html`
            <div class="h-g8" onClick=${() => v.onTab(m.k)} style="display:flex; align-items:center; gap:12px; padding:12px 10px; background:${m.bg}; cursor:pointer;">
              <div style="width:12px; font-size:10px; color:var(--color-accent); opacity:${m.cur};">▶</div>
              <div style="flex:1; font-size:12px;">${m.label}</div>
              <div style="font-size:7px; color:var(--color-neutral-500);">${m.hint}</div>
            </div>`)}
          <div style="flex:1;"></div>
          <div style="font-size:7px; line-height:1.8; color:var(--color-neutral-600);">▲▼ SELECT · ESC EXIT</div>
          <div style="position:absolute; inset:0; pointer-events:none; background:repeating-linear-gradient(0deg, rgba(0,0,0,.28) 0 1px, transparent 1px 3px);"></div>
        </div>
        <div style="display:flex; flex-direction:column; gap:18px; min-width:0; min-height:0; padding:22px 24px; background:#1a1817; border:2px solid #444141; overflow:auto;">
          ${!sv && tab !== 'machine' && html`<div style="font-family:var(--font-body); font-size:15px; color:var(--color-neutral-300);">${v.cfgMissing}</div>`}
          ${sv && tab === 'workers' && html`<${Workers} sv=${sv} ops=${v.ops} doctorNote=${v.doctorNote} doctorLabel=${v.doctorLabel} runDoctor=${v.runDoctor} />`}
          ${sv && tab === 'routing' && html`<${Routing} sv=${sv} ops=${v.ops} />`}
          ${sv && tab === 'limits' && html`<${Limits} sv=${sv} ops=${v.ops} />`}
          ${tab === 'machine' && html`<${Machine} machine=${v.machine} />`}
        </div>
      </div>
      <div style="flex:none; display:flex; align-items:center; gap:12px; padding:10px 14px; background:#1a1817; border:2px solid #444141;">
        ${v.dirty && html`<div style="font-size:9px; color:#f0a830; opacity:${v.blink};">● UNSAVED CHANGES</div>`}
        ${!v.dirty && html`<div style="font-size:9px; color:var(--color-neutral-500);">${v.savedNote}</div>`}
        ${v.cfgErr && html`<div style="min-width:0; font-size:8px; line-height:1.6; color:var(--color-accent); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${v.cfgErr}</div>`}
        <div style="flex:1;"></div>
        <div class="h-g8" onClick=${v.back} style="font-size:9px; padding:9px 12px; border:2px solid #605d5d; cursor:pointer;">◀ EXIT</div>
        <div class="h-g8" onClick=${v.revert} style="font-size:9px; padding:9px 12px; border:2px solid var(--color-bg); cursor:pointer; opacity:${v.revertOp};">REVERT</div>
        <div class="h-acc4" onClick=${v.save} style="font-size:9px; padding:9px 12px; background:var(--color-accent); border:2px solid var(--color-accent); cursor:pointer; opacity:${v.dirtyOp};">SAVE</div>
      </div>
    </div>`;
}
