// Connects the UI to the keyboard through window.aula (preload.js).
// Only what services/hid supports is sent: key remaps on both layers (keys,
// combos, media, mouse, disable), settings and locks, the lighting effect/
// brightness/speed and an uploaded screen image with its frame delay.
// Everything else stays staged and is listed as "not connected yet".

// UI key label -> f75max_layout position name (others match case-insensitively).
const POS = { '`': 'Backtick', '-': 'Minus', '=': 'Equal', '[': 'LBracket', ']': 'RBracket', '\\': 'Backslash', ';': 'Semicolon', "'": 'Quote', ',': 'Comma', '.': 'Period', '/': 'Slash', Bksp: 'Backspace', Del: 'Delete', PgUp: 'PageUp', PgDn: 'PageDown', Caps: 'CapsLock', Shift: 'Shift_L', RShift: 'Shift_R', Ctrl: 'Ctrl_L', Win: 'Win_L', Alt: 'Alt_L', RAlt: 'Alt_R' };
const LAYOUT = 'Esc F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 F11 F12 Backtick 1 2 3 4 5 6 7 8 9 0 Minus Equal Tab Q W E R T Y U I O P LBracket RBracket CapsLock A S D F G H J K L Semicolon Quote Backslash Shift_L Z X C V B N M Comma Period Slash Shift_R Enter Ctrl_L Win_L Alt_L Space Alt_R Left Down Up Right Backspace PageUp Delete End PageDown'.split(' ');
// UI send value -> remap.py send name.
const SEND = { PrintScreen: 'PrintScreen', 'Right Ctrl': 'Ctrl_R', 'Right Alt': 'Alt_R', 'Right Shift': 'Shift_R', 'Keypad *': 'KeypadAsterisk', 'Keypad /': 'KeypadSlash', 'Keypad -': 'KeypadMinus', 'Keypad +': 'KeypadPlus', Insert: 'Insert', Home: 'Home', Pause: 'Pause', 'Scroll Lock': 'ScrollLock', 'Num Lock': 'NumLock', Menu: 'Menu' };
for (let i = 13; i <= 24; i++) SEND['F' + i] = '0x' + (0x68 + i - 13).toString(16).toUpperCase();

function layoutName(v) {
  if (!v) return null;
  const n = POS[v] || v;
  return LAYOUT.find(x => x.toLowerCase() === String(n).toLowerCase()) || null;
}
const posName = k => k === 'Fn' ? null : layoutName(k);
const hidSendName = v => SEND[v] || layoutName(v);
const lightMode = m => m === 'LED Off' ? 'Off' : String(m).replace(/\s+/g, '');

// UI media / mouse names -> remap.py typed send names.
const MEDIA_SEND = { Mute: 'Mute', 'Volume down': 'VolumeDown', 'Volume up': 'VolumeUp', 'Play/Pause': 'PlayPause', 'Next track': 'Next', 'Previous track': 'Prev', Stop: 'Stop' };
const MOUSE_SEND = { 'Left click': 'Left', 'Right click': 'Right', 'Middle click': 'Middle', 'Wheel up': 'ScrollUp', 'Wheel down': 'ScrollDown' };
const COMBO_MODS = ['ctrl', 'shift', 'alt', 'win'];
const SLEEP_BYTE = { Never: 0, '1 min': 1, '5 min': 2, '30 min': 3 };

function comboOk(v) {
  const t = v.split('+').map(x => x.trim()), keys = t.filter(x => !COMBO_MODS.includes(x.toLowerCase()));
  return t.every(Boolean) && keys.length <= 1 && keys.every(k => hidSendName(k));
}
// UI value -> send value the backend accepts, or null if the firmware cannot do it.
function sendFor(v) {
  if (v === undefined) return null;
  if (v === 'Disabled') return 'Disabled';
  if (MEDIA_SEND[v]) return { media: MEDIA_SEND[v] };
  if (MOUSE_SEND[v]) return { mouse: MOUSE_SEND[v] };
  const mm = /^Macro: (.*)$/.exec(v);
  if (mm) return mm[1] in macIdx ? { macro: macIdx[mm[1]], mode: 'once', count: 1 } : null;
  if (v === 'Action ring' || /^(AHK|Open): /.test(v) || SYSTEM.includes(v)) return null;
  const plain = hidSendName(v);
  if (plain) return plain;
  return v.includes('+') && comboOk(v) ? { combo: v.split('+').map(t => hidSendName(t.trim()) || t.trim()).join('+') } : null;
}
let macIdx = {}, macSending = false;   // macro name -> index in the list macro_apply sends
const whyNot = v => /^Macro: /.test(v) ? "record the macro's keys first" : 'the keyboard cannot do this';

function remapPlan(L, ops, skipped, onSent, warn) {
  const S = st.layers[L], W = wr.layers[L], name = L === 'fn' ? 'Fn layer: ' : '';
  if (JSON.stringify(S) === JSON.stringify(W) && !(macSending && Object.values(S).some(v => /^Macro: /.test(v)))) return;   // macro indexes may have moved
  const mappings = [], sent = {};
  new Set([...Object.keys(S), ...Object.keys(W)]).forEach(k => {
    const pos = posName(k);
    let v = S[k], send = pos ? sendFor(v) : null;
    if (v !== undefined && !send) {
      skipped.push(name + k + ' sends ' + v + ' (' + (pos ? whyNot(v) : 'this key cannot be remapped') + ')');
      v = pos ? W[k] : undefined; send = pos ? sendFor(v) : null;
    }
    if (v === undefined || !send) return;
    mappings.push({ position: pos, send });
    sent[k] = v;
  });
  if (!mappings.length) return;
  if (mappings.some(m => m.send === '0x6B' || m.send === '0x6C')) warn.push('F16 and F17 are used by the Cidoo knob, so a key sending them may clash with it.');
  ops.push({ op: 'remap_apply', layer: L, mappings });
  onSent.push(() => { wr.layers[L] = sent; });
}

function macroPlan(ops, warn, onSent) {
  const withEv = ms => ms.filter(m => m.events && m.events.length), list = withEv(st.macros);
  macIdx = {}; list.forEach((m, i) => { macIdx[m.name] = i; });
  const pack = ms => JSON.stringify(withEv(ms).map(m => ({ name: m.name, events: m.events })));
  macSending = pack(st.macros) !== pack(wr.macros);
  if (!macSending) return;
  const lost = wr.macros.filter(m => !(m.events && m.events.length)).map(m => m.name);
  if (lost.length) warn.push('Macros are written as one list, so macros with no recorded keys (' + lost.join(', ') + ') will be removed from the keyboard.');
  const stale = [];   // remaps on a dropped macro keep their old index, so they would fire the wrong macro
  ['normal', 'fn'].forEach(L => Object.keys(st.layers[L]).forEach(k => {
    const mm = /^Macro: (.+)$/.exec(st.layers[L][k] || '');
    if (mm && !(mm[1] in macIdx)) stale.push((L === 'fn' ? 'Fn layer: ' : '') + k + ' (' + mm[1] + ')');
  }));
  if (stale.length) warn.push('These remaps point at macros that will be removed, so they will not be updated and may fire the wrong macro: ' + stale.join(', ') + '. Record keys for those macros or change the remaps first.');
  ops.push({ op: 'macro_apply', macros: list.map(m => ({ name: m.name, events: clone(m.events) })) });
  onSent.push(() => { wr.macros = clone(list); });
}

function plan() {
  const ops = [], skipped = [], onSent = [], warn = [];
  macroPlan(ops, warn, onSent);
  remapPlan('normal', ops, skipped, onSent, warn);
  remapPlan('fn', ops, skipped, onSent, warn);
  if (st.keyResponse !== wr.keyResponse || st.sleep !== wr.sleep || st.fnMode !== wr.fnMode || Object.keys(st.locks).some(k => st.locks[k] !== wr.locks[k])) {
    const LOCKS = ['Alt+Tab', 'Alt+F4', 'Win key'];
    if (!LOCKS.every(k => k in st.locks) || !['Toggle', 'Momentary'].includes(st.fnMode)) skipped.push('Settings: lock or Fn mode value is missing, so nothing was sent');
    else {
      warn.push('Settings are sent as one packet and the keyboard cannot be read back, so locks and Fn mode are rewritten from what this screen shows.');
      ops.push({ op: 'settings_apply', settings: { disable_alttab: +!!st.locks['Alt+Tab'], disable_altf4: +!!st.locks['Alt+F4'], disable_win: +!!st.locks['Win key'],
        fn_switch: st.fnMode === 'Toggle' ? 1 : 0, sleep_time: SLEEP_BYTE[st.sleep], key_respondtime: st.keyResponse } });
      onSent.push(() => { Object.assign(wr, { keyResponse: st.keyResponse, sleep: st.sleep, fnMode: st.fnMode, locks: clone(st.locks) }); });
    }
  }
  const L = st.light, WL = wr.light;
  if (L.mode !== WL.mode || L.brightness !== WL.brightness || L.speed !== WL.speed) {
    ops.push({ op: 'light', mode: lightMode(L.mode), brightness: L.brightness, speed: L.speed, colorful: true });
    onSent.push(() => { Object.assign(wr.light, { mode: L.mode, brightness: L.brightness, speed: L.speed }); });
  }
  const shot = st.lcd.current !== wr.lcd.current && ui.file && window.aula;
  if (shot) {
    const op = { op: 'screen_upload', path: window.aula.pathForFile(ui.file) };
    if (st.lcd.delay !== wr.lcd.delay) op.delay_ms = st.lcd.delay;
    ops.push(op);
    onSent.push(() => { wr.lcd = clone(st.lcd); });
  }
  diffs().forEach(d => {
    if (d.tag === 'Normal layer' || d.tag === 'Fn layer' || d.tag === 'Settings' || d.tag === 'Locks') return;
    if (d.tag === 'Lighting' && /^(Effect|Brightness|Speed) /.test(d.text)) return;
    if (d.tag === 'Screen' && shot && /^(Screen image|Frame delay)/.test(d.text)) return;
    if (d.tag === 'Screen' && d.text.startsWith('Frame delay')) return skipped.push(d.text + ' (choose an image to send it with the delay)');
    if (d.tag === 'Macros') return d.ev || d.text.startsWith('Edit ') ? (macSending ? 0 : skipped.push(d.tag + ': ' + d.text)) : skipped.push(d.tag + ': ' + d.text + (d.text.startsWith('Add') ? " (record the macro's keys first)" : ' (its keys were never read from the board)'));
    skipped.push(d.tag + ': ' + d.text);
  });
  return { ops, skipped, onSent, warn };
}

async function sendNow(live) {
  if (!window.aula) { ui.plan = { err: 'Open this in the desktop app to talk to the keyboard.', out: [], skipped: [] }; return render(); }
  const p = plan();
  ui.plan = { busy: true, live, out: [], skipped: p.skipped, warn: p.warn };
  render();
  for (const op of p.ops) {
    const r = await window.aula.hid(Object.assign({ live }, op));
    ui.plan.out.push(r);
    if (!r.ok) break;
  }
  ui.plan.busy = false;
  if (live) {
    const ok = p.ops.length && ui.plan.out.length === p.ops.length && ui.plan.out.every(r => r.ok);
    const dry = ui.plan.out.some(r => r.dry_run);
    if (ok && !dry) {
      p.onSent.forEach(f => f());
      at = new Date().toLocaleString('en-AU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
      hist = []; fut = []; ui.confirm = false; save(); sweep();
      toast('Sent to keyboard');
    } else if (!p.ops.length) toast('Nothing here can be sent yet');
    else toast(dry ? 'Dry run only: live writes are off' : 'Send failed. See Changes.');
  }
  render();
}

function planHtml() {
  const p = ui.plan;
  if (!p) return '';
  if (p.err) return '<p class="note">' + esc(p.err) + '</p>';
  let h = '<div class="plan">';
  (p.warn || []).forEach(w => { h += '<p class="note">' + esc(w) + '</p>'; });
  if (p.busy) h += '<p class="small muted">Talking to the keyboard…</p>';
  p.out.forEach(r => {
    const what = r.argv.filter(a => a !== '--dry-run').slice(0, 2).join(' ');
    h += '<p class="small">' + (r.dry_run ? 'Dry run' : 'Sent') + (r.ok ? '' : ' (failed)') + ': <span class="mono">' + esc(what) + '</span></p>'
      + '<pre class="mono pout">' + esc(r.output.trim()) + '</pre>';
  });
  if (!p.busy && !p.out.length && !p.skipped.length) h += '<p class="small muted">Nothing to send.</p>';
  if (p.skipped.length) h += '<p class="small muted">Not connected yet, so these stay staged:</p><ul class="small muted skl">' + p.skipped.map(s => '<li>' + esc(s) + '</li>').join('') + '</ul>';
  return h + '</div>';
}

let liveWrites = false;
async function pollStatus() {
  const el = document.getElementById('conn');
  if (!el) return;
  if (!window.aula) { el.dataset.state = 'off'; el.textContent = 'Preview only: not connected to a keyboard'; return; }
  const r = await window.aula.hid({ op: 'status' });
  const wired = /^wired config .*exact=yes/m.test(r.output);
  const dongle = /^dongle config .*\b05AC:024F\b/m.test(r.output);
  el.dataset.state = wired ? 'on' : dongle ? 'part' : 'off';
  el.textContent = wired ? 'Keyboard connected by cable' + (liveWrites ? '' : ' · sends are dry runs')
    : dongle ? 'Only the 2.4G dongle found. Plug in the cable to send changes.'
    : r.code === 1 ? 'Keyboard not found. Plug in the cable.'
    : 'Keyboard check failed: ' + (r.output.trim().split('\n').pop() || 'no reply');
}

async function loadBoardImage() {
  const img = document.querySelector('.bimg');
  if (!img || !window.aula) return;
  const src = await window.aula.boardImage();
  if (src) img.src = src;
  else img.closest('.board').classList.add('noimg');
}

document.addEventListener('DOMContentLoaded', async () => {
  if (window.aula) liveWrites = (await window.aula.info()).live;
  loadBoardImage();
  pollStatus();
  setInterval(() => { if (!document.hidden) pollStatus(); }, 8000);
});
