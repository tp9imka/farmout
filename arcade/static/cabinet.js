import { h, html } from './vendor/htm-preact-standalone.mjs';
import { sprite } from './sprite.js';

const SCAN = html`<div style="position:absolute; inset:0; pointer-events:none; background:repeating-linear-gradient(0deg, rgba(0,0,0,.28) 0 1px, transparent 1px 3px);"></div>`;

function Tile({ item, blink, px, stageMin, onOpen, onBench, benchLabel }) {
  return html`
    <div class="h-tile" onClick=${() => onOpen(item.id)} style="position:relative; display:flex; flex-direction:column; gap:7px; min-width:0; min-height:0; padding:12px 14px; background:#1a1817; border:2px ${item.bStyle} ${item.bColor}; cursor:pointer; overflow:hidden; font-family:'Press Start 2P',monospace; opacity:${item.dim};">
      <div style="position:absolute; left:0; right:0; top:0; height:4px; background:${item.topBar};"></div>
      <div style="flex:none; display:flex; align-items:center; gap:8px; white-space:nowrap;">
        <div style="font-size:7px; line-height:1; padding:3px 4px; background:${item.badgeBg}; border:2px solid ${item.badgeBd};">${item.badge}</div>
        <div style="font-size:10px; line-height:1;">${item.name}</div>
        <div style="flex:1;"></div>
        <div style="font-size:7px; color:var(--color-neutral-500);">SCORE</div>
        <div style="font-size:9px;">${item.score}</div>
        <div class="h-bdacc" onClick=${e => { e.stopPropagation(); onBench(item.id); }} style="font-size:7px; line-height:1; padding:4px 5px; border:2px solid #605d5d; color:var(--color-neutral-400); cursor:pointer;">${benchLabel}</div>
      </div>
      <div style="flex:none; font-family:var(--font-body); font-size:17px; font-weight:800; line-height:1.2; color:var(--color-bg); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">${item.mission}</div>
      <div style="display:flex; align-items:flex-end; justify-content:space-between; gap:12px; flex:1 1 0; min-height:${stageMin}; border-bottom:2px solid #605d5d; padding:0 4px 4px;">
        <div style="display:flex; flex-direction:column; gap:8px; font-size:7px; color:var(--color-neutral-500); padding-bottom:6px; min-width:0;">
          <div>${item.sub}</div>
          <div style="font-size:10px; color:var(--color-accent-400);">> ${item.tool}</div>
          ${item.isTurn && html`<div style="font-size:8px; color:var(--color-bg); opacity:${blink};">▶ AWAITS YOUR COMMAND</div>`}
        </div>
        ${sprite(h, item.kind, item.pose, item.f, px)}
      </div>
      <div style="flex:none; display:flex; align-items:baseline; gap:8px; min-width:0;">
        <div style="flex:none; font-size:7px; color:var(--color-neutral-500);">MODEL</div>
        <div style="min-width:0; font-family:var(--font-body); font-size:13px; font-weight:600; color:#d7d3d3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${item.model}</div>
      </div>
      <div style="flex:none; height:16px; font-family:var(--font-body); font-size:12px; line-height:16px; color:var(--color-neutral-500); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${item.lastMsg}</div>
      ${item.isWorker && html`
        <div style="flex:none; display:flex; align-items:center; gap:8px;">
          <div style="font-size:7px; color:var(--color-neutral-500);">STAGE</div>
          <div style="flex:1; display:flex; gap:2px;">
            ${item.bar.map(b => html`<div style="flex:1; height:8px; background:${b.c};"></div>`)}
          </div>
          <div style="font-size:7px;">${item.barVal}</div>
        </div>`}
      ${item.isClaude && html`
        <div style="flex:none; display:flex; align-items:center; gap:8px;">
          <div style="font-size:7px; color:var(--color-neutral-500);">CREW</div>
          <div style="display:flex; gap:4px;">
            ${item.crew.map(c => html`<div style="width:14px; height:8px; background:${c.c}; border:2px solid ${c.bd};"></div>`)}
          </div>
          <div style="flex:1;"></div>
          <div style="font-size:7px;">${item.crewVal}</div>
        </div>`}
      <div style="flex:none; display:flex; align-items:center; gap:10px; font-size:7px; color:var(--color-neutral-400); white-space:nowrap;">
        <div style="flex:1; min-width:0; display:flex; gap:10px; overflow:hidden;"><div style="flex:none;">${item.timeStr}</div><div style="flex:none;">${item.coins}</div><div style="min-width:0; overflow:hidden; text-overflow:ellipsis;">${item.items}</div></div>
        <div style="flex:none; font-size:7px; line-height:1; padding:4px 5px; background:${item.tag.bg}; color:${item.tag.fg}; border:2px ${item.tag.bs} ${item.tag.bd}; opacity:${item.tag.op};">${item.tag.label}</div>
      </div>
      ${SCAN}
      ${item.clearOv && html`
        <div style="position:absolute; inset:0; display:flex; flex-direction:column; justify-content:center; gap:14px; padding:18px; background:rgba(14,13,13,.88);">
          <div style="font-size:9px; color:var(--color-neutral-400);">${item.name} · ${item.sub}</div>
          <div style="font-size:20px; color:var(--color-bg);">STAGE CLEAR</div>
          <div style="align-self:flex-start; font-size:9px; padding:5px 6px; background:${item.tag.bg}; color:${item.tag.fg}; border:2px ${item.tag.bs} ${item.tag.bd}; opacity:${item.tag.op};">${item.tag.label}</div>
          <div style="font-family:var(--font-body); font-size:13px; line-height:1.35; color:var(--color-neutral-300);">${item.mission}</div>
          <div style="font-size:8px; color:var(--color-neutral-500);">${item.items} · TIME ${item.timeStr}</div>
        </div>`}
      ${item.overOv && html`
        <div style="position:absolute; inset:0; display:flex; flex-direction:column; justify-content:center; gap:14px; padding:18px; background:rgba(14,13,13,.9);">
          <div style="font-size:9px; color:var(--color-neutral-400);">${item.name} · ${item.sub}</div>
          <div style="font-size:20px; color:var(--color-accent);">GAME OVER</div>
          <div style="font-size:10px; color:var(--color-bg);">${item.reason}</div>
          <div style="font-family:var(--font-body); font-size:13px; line-height:1.35; color:var(--color-neutral-300);">${item.mission}</div>
          <div style="font-size:8px; color:var(--color-neutral-500);">TIME ${item.timeStr} · COIN ${item.coins}</div>
        </div>`}
    </div>`;
}

const EmptyTile = ({ blink }) => html`
  <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px; background:#141211; border:2px solid #2d2b2b; font-family:'Press Start 2P',monospace;">
    <div style="font-size:12px; color:var(--color-accent); opacity:${blink};">INSERT COIN</div>
    <div style="font-size:7px; color:var(--color-neutral-600);">NO PLAYER</div>
  </div>`;

function ViewEmpty({ v }) {
  return html`
    <div style="flex:1; min-height:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:18px; background:#141211; border:2px solid #2d2b2b;">
      <div style="font-family:'Press Start 2P',monospace; font-size:14px; color:var(--color-neutral-400);">${v.viewEmptyTitle}</div>
      <div style="font-family:var(--font-body); font-size:15px; color:var(--color-neutral-500);">${v.viewEmptyText}</div>
    </div>`;
}

function Tabs({ v }) {
  const tab = (key, label) => html`<div onClick=${() => v.onView(key)} style="padding:7px 10px; cursor:pointer; background:${v.view === key ? 'var(--color-accent)' : 'transparent'}; color:${v.view === key ? 'var(--color-bg)' : 'var(--color-neutral-400)'};">${label}</div>`;
  return html`<div style="display:flex; border:2px solid #605d5d;">${tab('play', `IN PLAY ${v.nPlay}`)}${tab('bench', `BENCH ${v.nBench}`)}</div>`;
}

function Live({ v }) {
  return html`
    ${v.viewEmpty && html`<${ViewEmpty} v=${v} />`}
    <div style="flex:1; min-height:0; display:${v.viewEmpty ? 'none' : 'flex'}; flex-direction:column; gap:10px;">
      ${v.pageRows.map(row => html`
        <div style="flex:1 1 0; min-height:0; display:flex; flex-direction:column; gap:6px;">
          ${row.real && html`
            <div style="flex:1; min-height:0; display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px;">
              ${row.tiles.map(item => item.real
                ? html`<${Tile} key=${item.id + ':' + item.statusKey} item=${item} blink=${v.blink} px=${v.px} stageMin=${v.stageMin} onOpen=${v.onOpen} onBench=${v.onBench} benchLabel=${v.benchLabel} />`
                : html`<${EmptyTile} blink=${v.blink} />`)}
            </div>`}
        </div>`)}
    </div>
    <div style="flex:none; height:30px; display:flex; align-items:center; gap:18px; font-family:'Press Start 2P',monospace; font-size:8px; color:var(--color-neutral-500);">
      <${Tabs} v=${v} />
      <div style="width:2px; height:18px; background:#605d5d;"></div>
      <div style="display:flex; align-items:center; gap:8px;"><div style="font-size:7px; padding:3px 4px; background:var(--color-accent); border:2px solid var(--color-accent); color:var(--color-bg);">CONTROL</div>CLAUDE SESSION</div>
      <div style="display:flex; align-items:center; gap:8px;"><div style="font-size:7px; padding:3px 4px; border:2px solid #9b9797; color:var(--color-bg);">PLAYER</div>WORKER JOB</div>
      ${v.errors.length > 0 && html`<div title=${v.errors.join('\n')} style="color:#f0a830; cursor:help;">${v.errors.length} SOURCE ERROR${v.errors.length === 1 ? '' : 'S'}</div>`}
      <div style="flex:1;"></div>
      <div style="display:flex; gap:6px;">
        ${v.pageDots.map((c, i) => html`<div onClick=${() => v.onGo(i)} style="width:10px; height:10px; background:${c}; cursor:pointer;"></div>`)}
      </div>
      <div style="color:var(--color-bg);">${v.pageLabel}</div>
      <div class="h-bdacc" onClick=${() => v.onNav(-1)} style="padding:6px 8px; border:2px solid #605d5d; color:var(--color-bg); cursor:pointer; opacity:${v.pagerOp};">◀</div>
      <div class="h-bdacc" onClick=${() => v.onNav(1)} style="padding:6px 8px; border:2px solid #605d5d; color:var(--color-bg); cursor:pointer; opacity:${v.pagerOp};">▶</div>
    </div>`;
}

function Empty({ v }) {
  const walkX = (v.frame * 18) % 1000 - 80;
  return html`
    <div style="position:relative; flex:1; min-height:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:22px; background:#141211; border:2px solid #2d2b2b; overflow:hidden; font-family:'Press Start 2P',monospace;">
      <div style="font-size:10px; color:var(--color-neutral-500);">NO PLAYERS IN GAME</div>
      <div style="font-size:40px; color:var(--color-accent); opacity:${v.blink};">INSERT COIN</div>
      <div style="font-family:var(--font-body); font-size:15px; color:var(--color-neutral-400);">Start a Claude session in any repo and it joins as CONTROL.</div>
      <div style="position:relative; width:900px; height:120px; border-bottom:2px solid #605d5d;">
        <div style="position:absolute; bottom:4px; left:${walkX}px;">${sprite(h, 'claude', v.frame % 2 ? 'clear' : 'play', v.frame, 6)}</div>
      </div>
      <div style="font-size:9px; color:var(--color-neutral-500);">HI-SCORE TODAY ${v.hud.score}</div>
      ${SCAN}
    </div>`;
}

function NoSignal({ v }) {
  return html`
    <div style="position:relative; flex:1; min-height:0; display:flex; align-items:center; justify-content:center; background:repeating-linear-gradient(${v.staticAngle}, #1a1817 0 2px, #2d2b2b 2px 3px, #0e0d0d 3px 5px, #444141 5px 6px); border:2px solid #2d2b2b; overflow:hidden; font-family:'Press Start 2P',monospace;">
      <div style="display:flex; flex-direction:column; gap:18px; padding:32px 36px; background:#0e0d0d; border:2px solid var(--color-accent);">
        <div style="font-size:44px; color:var(--color-bg);">NO SIGNAL</div>
        <div style="font-family:var(--font-body); font-size:15px; color:var(--color-neutral-300);">${v.noSignalMsg}</div>
        <div style="font-size:9px; color:var(--color-accent); opacity:${v.blink};">RETRYING IN ${v.retry}</div>
      </div>
      <div style="position:absolute; inset:0; pointer-events:none; background:repeating-linear-gradient(0deg, rgba(0,0,0,.35) 0 1px, transparent 1px 3px);"></div>
    </div>`;
}

function Hof({ v }) {
  return html`
    <div style="flex:none; display:flex; flex-direction:column; gap:8px; padding:12px 14px 10px; background:#1a1817; border:2px solid #444141;">
      <div style="display:flex; align-items:center; gap:14px; font-family:'Press Start 2P',monospace; font-size:10px;">
        <div style="color:var(--color-accent);">HALL OF FAME</div>
        <div style="font-size:8px; color:var(--color-neutral-500);">LAST 10 FINISHED</div>
      </div>
      <div style="display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); column-gap:28px;">
        ${v.hof.map(x => html`
          <div class="h-row" onClick=${() => v.onOpen(x.id)} style="display:grid; grid-template-columns:24px 76px minmax(0,1fr) 170px 150px 52px; align-items:center; gap:10px; height:28px; border-top:2px solid #262322; cursor:pointer; font-family:'Press Start 2P',monospace; font-size:8px;">
            <div style="color:var(--color-neutral-600);">${x.rank}</div>
            <div>${x.name}</div>
            <div style="font-family:var(--font-body); font-size:13px; color:var(--color-neutral-200); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${x.mission}</div>
            <div style="font-family:var(--font-body); font-size:12px; color:var(--color-neutral-500); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${x.repo}</div>
            <div style="justify-self:start; font-size:7px; line-height:1; padding:4px 5px; background:${x.tag.bg}; color:${x.tag.fg}; border:2px ${x.tag.bs} ${x.tag.bd};">${x.tag.label}</div>
            <div style="text-align:right; color:var(--color-neutral-400);">${x.time}</div>
          </div>`)}
      </div>
    </div>`;
}

export function Cabinet({ v }) {
  return html`
    <div style="flex:1; min-height:0; display:flex; flex-direction:column; gap:10px; padding:10px;">
      ${v.dataLive && html`<${Live} v=${v} />`}
      ${v.dataEmpty && html`<${Empty} v=${v} />`}
      ${v.noSignal && html`<${NoSignal} v=${v} />`}
      ${!v.noSignal && html`<${Hof} v=${v} />`}
    </div>`;
}
