// 14x14 pixel sprites, ported verbatim from the design's poseGrid/sprite.
import { C } from './vm.js';

const CL = ['..............', '...r.r..r.r...', '...rrrrrrrr...', '..kkkkkkkkkk..', '..kwwwwwwwwk..', '..kwwewwewwk..', '..kwwwwwwwwk..', '...kkkkkkkk...', '..kbbbbbbbbk..', '.kkbbbbbbbbkk.', '.k.bbbbbbbb.k.', '...kkkkkkkk...', '...kk....kk...', '..kkk....kkk..'];
const WK = ['......a.......', '......k.......', '....kkkkkk....', '...kwwwwwwk...', '...kwewwewk...', '...kwwwwwwk...', '....kkkkkk....', '...kbbbbbbk...', '..kkbbbbbbkk..', '..k.bbbbbb.k..', '....kkkkkk....', '....kk..kk....', '....kk..kk....', '...kkk..kkk...'];
const GH = ['..............', '....hhhhhh....', '...hhhhhhhh...', '..hhhhhhhhhh..', '..hhwwhhwwhh..', '..hhwdhhwdhh..', '..hhhhhhhhhh..', '..hhhhhhhhhh..', '..hhhhhhhhhh..', '..hhhhhhhhhh..', '..hhhhhhhhhh..', '..h.hh..hh.h..', '..............', '..............'];
const PAL = { k: C.W, w: C.G8, r: C.R, g: C.G6, y: C.AMB, h: C.G4, d: C.INK };

export function poseGrid(kind, pose, f) {
  if (pose === 'lost') {
    const g = GH.map(r => r.split(''));
    if (f % 2) g[11] = '..hh.hh..hh.h.'.split('').map((c, i) => i < 12 ? c : '.');
    return g;
  }
  const g = (kind === 'claude' ? CL : WK).map(r => r.split(''));
  const put = (x, y, c) => { if (x >= 0 && x < 14 && y >= 0 && y < 14) g[y][x] = c; };
  const body = { pause: 'y', conflict: 'y', ended: 'g', discarded: 'g', over: f % 2 ? 'g' : 'r' }[pose] || 'r';
  const eyes = [];
  for (let y = 0; y < 14; y++) for (let x = 0; x < 14; x++) {
    const c = g[y][x];
    if (c === 'b') g[y][x] = body;
    if (c === 'a') g[y][x] = pose === 'play' && f % 2 ? 'r' : 'k';
    if (c === 'e') eyes.push([x, y]);
  }
  const closed = pose === 'idle' || pose === 'ended' || pose === 'discarded' || (pose === 'turn' && f % 5 === 0);
  eyes.forEach(([x, y]) => {
    if (closed) { g[y][x] = 'k'; put(x + 1, y, 'k'); }
    else if (pose === 'think') { g[y][x] = 'w'; put(x, y - 1, 'k'); }
    else if (pose === 'over') g[y][x] = 'r';
    else g[y][x] = 'k';
  });
  if (pose === 'think') [[11, 2], [12, 1], [13, 0]].slice(0, f % 4).forEach(([x, y]) => put(x, y, 'h'));
  if (pose === 'turn' && f % 2) [[12, 0], [12, 1], [12, 2], [12, 4]].forEach(([x, y]) => put(x, y, 'r'));
  if (pose === 'pause' && f % 2) [[11, 1], [12, 1], [13, 1]].forEach(([x, y]) => put(x, y, 'y'));
  if (pose === 'idle') { const o = f % 2; [[11, 0], [12, 0], [13, 0], [12, 1], [11, 2], [12, 2], [13, 2]].forEach(([x, y]) => put(x, y + o, 'h')); }
  if (pose === 'play') {
    g[11] = '.gggggggggggg.'.split(''); g[12] = '.gggggggggggg.'.split(''); g[13] = '..g........g..'.split('');
    put(2 + (f * 3) % 10, 11, 'k'); put(3 + (f * 7) % 9, 12, 'k');
  }
  if (pose === 'clear') {
    if (kind === 'claude') { put(1, 10, '.'); put(12, 10, '.'); [[0, 8], [0, 7], [13, 8], [13, 7]].forEach(([x, y]) => put(x, y, 'k')); }
    else { put(2, 9, '.'); put(11, 9, '.'); [[1, 7], [1, 6], [12, 7], [12, 6]].forEach(([x, y]) => put(x, y, 'k')); }
    if (f % 2) { put(0, 1, 'y'); put(13, 3, 'y'); } else { put(1, 3, 'y'); put(12, 0, 'y'); }
  }
  return g;
}

export function shadowOf(kind, pose, f, p) {
  const g = poseGrid(kind, pose, f), sh = [];
  for (let y = 0; y < 14; y++) for (let x = 0; x < 14; x++) { const c = g[y][x]; if (c !== '.') sh.push(`${(x + 1) * p}px ${y * p}px 0 0 ${PAL[c]}`); }
  return sh.join(',');
}

// `h` is injected so this module stays importable from node tests.
export function sprite(h, kind, pose, f, p) {
  const bob = (pose === 'clear' || pose === 'lost') && f % 2 ? -p : 0;
  return h('div', { style: { position: 'relative', width: 14 * p + 'px', height: 14 * p + 'px', flex: 'none', transform: `translateY(${bob}px)`, opacity: pose === 'lost' ? 0.75 : 1 } },
    h('div', { style: { position: 'absolute', left: -p + 'px', top: 0, width: p + 'px', height: p + 'px', boxShadow: shadowOf(kind, pose, f, p) } }));
}
