import { html } from './vendor/htm-preact-standalone.mjs';
import { signed } from './focus.js';

export function Confirm({ cf, onYes, onNo }) {
  return html`
    <div style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; background:rgba(14,13,13,.82); font-family:'Press Start 2P',monospace;">
      <div style="width:600px; display:flex; flex-direction:column; gap:16px; padding:24px; background:#1a1817; border:2px solid var(--color-accent); box-shadow:var(--shadow-lg);">
        <div style="font-size:16px;">${cf.title}</div>
        <div style="font-family:var(--font-body); font-size:15px; line-height:1.5; color:var(--color-neutral-300);">${cf.body}</div>
        ${cf.hasFiles && html`
          <div style="display:flex; flex-direction:column; gap:6px; padding:12px 14px; background:#0e0d0d; border:2px solid #444141;">
            ${cf.files.map(fl => html`<div style="display:flex; gap:10px; font-family:var(--font-body); font-size:14px;"><span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${fl.n}</span><span>${signed('+', fl.a)}</span><span style="color:var(--color-accent);">${signed('−', fl.d)}</span></div>`)}
          </div>`}
        <div style="display:flex; gap:10px; margin-top:4px;">
          <div class="h-acc4" onClick=${onYes} style="font-size:10px; padding:11px 14px; background:var(--color-accent); border:2px solid var(--color-accent); cursor:pointer;">${cf.yes}</div>
          <div class="h-g8" onClick=${onNo} style="font-size:10px; padding:11px 14px; border:2px solid var(--color-bg); cursor:pointer;">CANCEL</div>
        </div>
      </div>
    </div>`;
}
