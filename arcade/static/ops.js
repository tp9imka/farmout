import { html } from './vendor/htm-preact-standalone.mjs';

const COLS = 'grid-template-columns:minmax(0,1.4fr) 150px 170px 120px 130px 130px 120px;';

// AUTOPILOTS: every Multica autopilot with a RUN NOW button.
export function Autopilots({ v }) {
  return html`
    <div style="flex:1; min-height:0; display:flex; flex-direction:column; gap:10px; padding:10px; font-family:'Press Start 2P',monospace;">
      <div style="flex:1; min-height:0; display:flex; flex-direction:column; gap:14px; padding:20px 22px; background:#1a1817; border:2px solid #f0a830; overflow:auto;">
        <div style="display:flex; align-items:center; gap:16px;">
          <div class="h-g8" onClick=${v.back} style="font-size:9px; cursor:pointer; padding:7px 9px; border:2px solid var(--color-bg);">◀ BACK · ESC</div>
          <div style="font-size:14px;">AUTOPILOTS</div>
          <div style="font-size:8px; color:var(--color-neutral-500);">${v.subtitle}</div>
          <div style="flex:1;"></div>
          ${v.appUrl && html`<a href=${v.appUrl} target="_blank" rel="noopener noreferrer" style="font-size:8px; text-decoration:none;">MANAGE IN MULTICA ↗</a>`}
        </div>
        ${v.actionMsg && html`<div style="font-size:8px; line-height:1.6; color:#f0a830;">${v.actionMsg}</div>`}
        ${v.rows.length === 0 && html`
          <div style="padding:30px 0; font-family:var(--font-body); font-size:15px; color:var(--color-neutral-400);">
            ${v.emptyText}
          </div>`}
        ${v.rows.length > 0 && html`
          <div style="display:grid; ${COLS} gap:14px; padding:0 0 10px; font-size:7px; color:var(--color-neutral-500); border-bottom:2px solid #444141;">
            <div>AUTOPILOT</div><div>AGENT</div><div>TRIGGERS</div><div>STATUS</div><div>NEXT RUN</div><div>LAST RUN</div><div></div>
          </div>`}
        ${v.rows.map(a => html`
          <div style="display:grid; ${COLS} gap:14px; align-items:center; padding:12px 0; border-bottom:2px solid #2d2b2b; opacity:${a.active ? '1' : '0.55'};">
            <div style="display:flex; flex-direction:column; gap:6px; min-width:0;">
              <div style="font-family:var(--font-body); font-size:16px; font-weight:800; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${a.title}</div>
              ${a.desc && html`<div style="font-family:var(--font-body); font-size:13px; color:var(--color-neutral-400); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${a.desc}</div>`}
            </div>
            <div style="font-size:9px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${a.agent}</div>
            <div style="font-size:8px; color:var(--color-neutral-300);">${a.triggers}</div>
            <div style="justify-self:start; font-size:7px; line-height:1; padding:4px 5px; background:${a.tag.bg}; color:${a.tag.fg}; border:2px ${a.tag.bs} ${a.tag.bd};">${a.tag.label}</div>
            <div style="font-size:8px; color:var(--color-neutral-300);">${a.next}</div>
            <div style="font-size:8px; color:var(--color-neutral-400);">${a.last}</div>
            <div class="h-land" onClick=${() => a.active && !v.busy && v.onRun(a.id)} style="justify-self:end; font-size:9px; padding:9px 11px; background:var(--color-accent); border:2px solid var(--color-accent); cursor:${a.active ? 'pointer' : 'not-allowed'}; opacity:${a.active && !v.busy ? '1' : '0.35'};">RUN NOW</div>
          </div>`)}
      </div>
    </div>`;
}
