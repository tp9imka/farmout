import { h, html } from './vendor/htm-preact-standalone.mjs';
import { sprite } from './sprite.js';

const BIG_PX = 18;
export const signed = (sign, v) => v === '--' ? v : sign + v;
const CREW_PX = 3;
const SCAN = html`<div style="position:absolute; inset:0; pointer-events:none; background:repeating-linear-gradient(0deg, rgba(0,0,0,.28) 0 1px, transparent 1px 3px);"></div>`;

const Tag = ({ t, size = 9, pad = '5px 6px' }) => html`
  <div style="font-size:${size}px; line-height:1; padding:${pad}; background:${t.bg}; color:${t.fg}; border:2px ${t.bs} ${t.bd}; opacity:${t.op};">${t.label}</div>`;

const Back = ({ onBack }) => html`
  <div class="h-g8" onClick=${onBack} style="font-size:9px; cursor:pointer; padding:7px 9px; border:2px solid var(--color-bg);">◀ BACK · ESC</div>`;

const Replay = ({ title, lines }) => html`
  <div style="display:flex; flex-direction:column; gap:7px; padding:16px 18px; background:#1a1817; border:2px solid #444141; overflow:hidden;">
    <div style="font-size:8px; color:var(--color-neutral-500); margin-bottom:4px;">${title}</div>
    ${lines.map(l => html`<div style="display:flex; gap:12px; font-family:var(--font-body); font-size:13px; line-height:1.35; white-space:nowrap; overflow:hidden;"><span style="color:var(--color-neutral-600); flex:none;">${l.t}</span><span style="overflow:hidden; text-overflow:ellipsis;">${l.m}</span></div>`)}
  </div>`;

const FileRow = ({ fl }) => html`
  <div style="display:flex; gap:10px; font-family:var(--font-body); font-size:13px; line-height:1.35;">
    <span style="flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${fl.n}</span>
    <span style="color:var(--color-bg);">${signed('+', fl.a)}</span><span style="color:var(--color-accent);">${signed('−', fl.d)}</span>
  </div>`;

export function FocusJob({ fw, onBack, onAsk, actionMsg, busy }) {
  return html`
    <div style="flex:1; min-height:0; display:grid; grid-template-columns:480px minmax(0,1fr); gap:10px; padding:10px; font-family:'Press Start 2P',monospace;">
      <div style="position:relative; display:flex; flex-direction:column; gap:16px; padding:20px; background:#1a1817; border:2px solid var(--color-accent); overflow:hidden;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
          <${Back} onBack=${onBack} />
          <div style="display:flex; flex-direction:column; align-items:flex-end; gap:8px;"><div style="font-size:8px; color:var(--color-neutral-500);">SCORE</div><div style="font-size:22px;">${fw.score}</div></div>
        </div>
        <div style="flex:1; min-height:0; display:flex; align-items:flex-end; justify-content:center; border-bottom:2px solid #605d5d; padding-bottom:6px;">${sprite(h, 'worker', fw.pose, fw.f, BIG_PX)}</div>
        <div style="display:flex; align-items:center; gap:12px;">
          <div style="font-size:8px; padding:4px 5px; border:2px solid #9b9797;">PLAYER</div>
          <div style="font-size:22px;">${fw.name}</div>
          <div style="flex:1;"></div>
          <${Tag} t=${fw.tag} />
        </div>
        <div style="font-family:var(--font-body); font-size:14px; color:var(--color-neutral-400);">${fw.sub} · ${fw.repo} / ${fw.branch}</div>
        <div style="display:flex; align-items:center; gap:10px;">
          <div style="font-size:8px; color:var(--color-neutral-500);">STAGE</div>
          <div style="flex:1; display:flex; gap:3px;">
            ${fw.bar.map(b => html`<div style="flex:1; height:12px; background:${b.c};"></div>`)}
          </div>
          <div style="font-size:9px;">${fw.barVal}</div>
        </div>
        ${fw.showConflict && html`
          <div style="display:flex; flex-direction:column; gap:8px; padding:12px 14px; border:2px solid #f0a830; color:#f0a830;">
            <div style="font-size:10px;">CONFLICT</div>
            <div style="font-family:var(--font-body); font-size:14px; color:var(--color-bg);">Patch no longer applies. Resolve in Claude.</div>
          </div>`}
        ${fw.error && html`
          <div style="display:flex; flex-direction:column; gap:8px; padding:12px 14px; border:2px solid var(--color-accent); color:var(--color-accent);">
            <div style="font-size:10px;">ERROR</div>
            <div style="font-family:var(--font-body); font-size:14px; color:var(--color-bg);">${fw.error}</div>
          </div>`}
        ${fw.showLanded && html`
          <div style="display:flex; flex-direction:column; gap:8px; padding:12px 14px; background:var(--color-bg); color:#0e0d0d;">
            <div style="font-size:10px;">LANDED</div>
            <div style="font-family:var(--font-body); font-size:14px;">${fw.landedMsg}</div>
          </div>`}
        <div style="display:flex; align-items:center; gap:10px; min-height:36px;">
          ${fw.canLand && !busy && html`<div class="h-land" onClick=${() => onAsk('land')} style="font-size:10px; padding:11px 14px; background:var(--color-accent); border:2px solid var(--color-accent); cursor:pointer;">LAND</div>`}
          ${fw.canDiscard && !busy && html`<div class="h-g8" onClick=${() => onAsk('discard')} style="font-size:10px; padding:11px 14px; border:2px solid var(--color-bg); cursor:pointer;">DISCARD</div>`}
          ${fw.canKill && !busy && html`<div class="h-g8" onClick=${() => onAsk('kill')} style="font-size:10px; padding:11px 14px; border:2px solid var(--color-accent); color:var(--color-accent); cursor:pointer;">KILL</div>`}
          ${fw.noActions && html`<div style="font-size:8px; color:var(--color-neutral-500);">${fw.actionNote}</div>`}
        </div>
        ${actionMsg && html`<div style="font-size:8px; line-height:1.6; color:#f0a830;">${actionMsg}</div>`}
        ${SCAN}
      </div>
      <div style="display:flex; flex-direction:column; gap:10px; min-width:0; min-height:0;">
        <div style="display:flex; flex-direction:column; gap:12px; padding:18px 20px; background:#1a1817; border:2px solid #444141;">
          <div style="font-size:8px; color:var(--color-neutral-500);">MISSION</div>
          <div style="font-family:var(--font-body); font-size:22px; font-weight:800; line-height:1.2;">${fw.mission}</div>
          <div style="font-family:var(--font-body); font-size:15px; line-height:1.5; color:var(--color-neutral-300); text-wrap:pretty;">${fw.brief}</div>
        </div>
        <div style="display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); background:#1a1817; border:2px solid #444141;">
          ${fw.meta.map(m => html`
            <div style="display:flex; flex-direction:column; gap:8px; padding:12px 16px; min-width:0; border-right:2px solid #2d2b2b; border-bottom:2px solid #2d2b2b; grid-column:${m.span};">
              <div style="font-size:7px; color:var(--color-neutral-500);">${m.k}</div>
              <div style="font-family:var(--font-body); font-size:15px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${m.v}</div>
            </div>`)}
        </div>
        <div style="flex:1; min-height:0; display:grid; grid-template-columns:minmax(0,1.5fr) minmax(0,1fr); gap:10px;">
          <${Replay} title="REPLAY" lines=${fw.replay} />
          <div style="display:flex; flex-direction:column; gap:8px; padding:16px 18px; background:#1a1817; border:2px solid #444141; overflow:hidden;">
            <div style="font-size:8px; color:var(--color-neutral-500); margin-bottom:4px;">${fw.items}</div>
            ${fw.files.map(fl => html`<${FileRow} fl=${fl} />`)}
            ${fw.readOnly && html`
              <div style="font-size:9px; color:var(--color-neutral-400); line-height:1.6;">READ ONLY</div>
              <div style="font-family:var(--font-body); font-size:13px; color:var(--color-neutral-500);">This job cannot change files.</div>`}
            ${fw.out.length > 0 && html`
              <div style="font-size:8px; color:var(--color-neutral-500); margin-top:8px;">OUT FILES · ${fw.out.length}</div>
              ${fw.out.map(p => html`<div style="font-family:var(--font-body); font-size:12px; color:var(--color-neutral-200); word-break:break-all; user-select:all;">${p}</div>`)}`}
          </div>
        </div>
      </div>
    </div>`;
}

export function FocusSession({ fc, onBack, onOpen, onAsk, busy, actionMsg }) {
  return html`
    <div style="flex:1; min-height:0; display:grid; grid-template-columns:480px minmax(0,1fr); gap:10px; padding:10px; font-family:'Press Start 2P',monospace;">
      <div style="position:relative; display:flex; flex-direction:column; gap:16px; padding:20px; background:#1a1817; border:2px solid var(--color-accent); overflow:hidden;">
        <div style="position:absolute; left:0; right:0; top:0; height:4px; background:var(--color-accent);"></div>
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
          <${Back} onBack=${onBack} />
          <div style="display:flex; flex-direction:column; align-items:flex-end; gap:8px;"><div style="font-size:8px; color:var(--color-neutral-500);">SCORE</div><div style="font-size:22px;">${fc.score}</div></div>
        </div>
        <div style="flex:1; min-height:0; display:flex; align-items:flex-end; justify-content:center; border-bottom:2px solid #605d5d; padding-bottom:6px;">${sprite(h, 'claude', fc.pose, fc.f, BIG_PX)}</div>
        <div style="display:flex; align-items:center; gap:12px;">
          <div style="font-size:8px; padding:4px 5px; background:var(--color-accent); border:2px solid var(--color-accent);">CONTROL</div>
          <div style="font-size:22px;">${fc.name}</div>
          <div style="flex:1;"></div>
          <${Tag} t=${fc.tag} />
        </div>
        <div style="font-family:var(--font-body); font-size:14px; color:var(--color-neutral-400);">${fc.repo} / ${fc.branch} · ${fc.model} · ${fc.timeStr}</div>
        <div style="display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); border:2px solid #444141;">
          <div style="display:flex; flex-direction:column; gap:8px; padding:12px 14px;"><div style="font-size:7px; color:var(--color-neutral-500);">INPUT</div><div style="font-size:12px;">${fc.tIn}</div></div>
          <div style="display:flex; flex-direction:column; gap:8px; padding:12px 14px; border-left:2px solid #444141;"><div style="font-size:7px; color:var(--color-neutral-500);">OUTPUT</div><div style="font-size:12px;">${fc.tOut}</div></div>
          <div style="display:flex; flex-direction:column; gap:8px; padding:12px 14px; border-left:2px solid #444141;"><div style="font-size:7px; color:var(--color-neutral-500);">CACHE</div><div style="font-size:12px;">${fc.tCache}</div></div>
        </div>
        ${fc.canEnd && !busy
          ? html`<div class="h-g8" onClick=${() => onAsk('end')} style="align-self:flex-start; font-size:10px; padding:11px 14px; border:2px solid var(--color-accent); color:var(--color-accent); cursor:pointer;">END SESSION</div>`
          : html`<div style="font-size:8px; color:var(--color-neutral-500);">${busy ? 'WORKING…' : 'WATCH ONLY · NO ACTIONS'}</div>`}
        ${actionMsg && html`<div style="font-size:8px; line-height:1.6; color:#f0a830;">${actionMsg}</div>`}
        ${SCAN}
      </div>
      <div style="display:flex; flex-direction:column; gap:10px; min-width:0; min-height:0;">
        <div style="display:flex; flex-direction:column; gap:12px; padding:18px 20px; background:#1a1817; border:2px solid #444141;">
          <div style="font-size:8px; color:var(--color-neutral-500);">MISSION</div>
          <div style="font-family:var(--font-body); font-size:22px; font-weight:800; line-height:1.2;">${fc.mission}</div>
          <div style="font-size:7px; color:var(--color-accent-400); margin-top:4px;">LAST PROMPT</div>
          <div style="font-family:var(--font-body); font-size:15px; line-height:1.5; color:var(--color-neutral-300); text-wrap:pretty;">“${fc.prompt}”</div>
        </div>
        <div style="display:flex; flex-direction:column; gap:8px; padding:14px 18px; background:#1a1817; border:2px solid #444141;">
          <div style="font-size:8px; color:var(--color-neutral-500);">CREW OF THIS SESSION · ${fc.crewCount}</div>
          ${fc.crewList.map(w => html`
            <div class="h-row" onClick=${() => onOpen(w.id)} style="display:grid; grid-template-columns:48px 90px minmax(0,1fr) 190px 110px; align-items:center; gap:12px; padding:6px 0; border-top:2px solid #262322; cursor:pointer; font-size:9px;">
              <div style="display:flex; justify-content:center;">${sprite(h, 'worker', w.pose, w.f, CREW_PX)}</div>
              <div>${w.name}</div>
              <div style="font-family:var(--font-body); font-size:13px; color:var(--color-neutral-300); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${w.mission}</div>
              <div style="justify-self:start;"><${Tag} t=${w.tag} size=${7} pad="4px 5px" /></div>
              <div style="text-align:right; font-size:8px; color:var(--color-neutral-400);">${w.barVal}</div>
            </div>`)}
          ${fc.noCrew && html`<div style="font-family:var(--font-body); font-size:13px; color:var(--color-neutral-500);">No workers launched from this session.</div>`}
        </div>
        <div style="flex:1; min-height:0; display:grid; grid-template-columns:minmax(0,1.5fr) minmax(0,1fr); gap:10px;">
          <${Replay} title="REPLAY · TOOL CALLS" lines=${fc.replay} />
          <div style="display:flex; flex-direction:column; gap:8px; padding:16px 18px; background:#1a1817; border:2px solid #444141; overflow:hidden;">
            <div style="font-size:8px; color:var(--color-neutral-500); margin-bottom:4px;">${fc.items}</div>
            ${fc.files.map(fl => html`<${FileRow} fl=${fl} />`)}
          </div>
        </div>
      </div>
    </div>`;
}
