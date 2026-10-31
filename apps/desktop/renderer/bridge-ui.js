// Connects the UI to the keyboard through window.aula (preload.js).
// Only what services/hid supports is sent: normal-layer key remaps, the
// lighting effect/brightness/speed and an uploaded screen image. Everything
// else stays staged and is listed as "not connected yet".

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

function plan() {
  const ops = [], skipped = [], onSent = [], warn = [];
  const S = st.layers.normal, W = wr.layers.normal;
  if (JSON.stringify(S) !== JSON.stringify(W)) {
    const mappings = [], sent = {};
    new Set([...Object.keys(S), ...Object.keys(W)]).forEach(k => {
      const pos = posName(k);
      let v = S[k], send = hidSendName(v);
      if (v !== undefined && (!pos || !send)) {
        skipped.push(k + ' sends ' + v);
        v = pos ? W[k] : undefined; send = hidSendName(v);
      }
      if (v === undefined || !send) return;
      mappings.push({ position: pos, send });
      sent[k] = v;
    });
    if (mappings.some(m => m.send === '0x6B' || m.send === '0x6C')) warn.push('F16 and F17 are used by the Cidoo knob, so a key sending them may clash with it.');
    ops.push({ op: 'remap_apply', mappings });
    onSent.push(() => { wr.layers.normal = sent; });
  }
  const L = st.light, WL = wr.light;
  if (L.mode !== WL.mode || L.brightness !== WL.brightness || L.speed !== WL.speed) {
    ops.push({ op: 'light', mode: lightMode(L.mode), brightness: L.brightness, speed: L.speed, colorful: true });
    onSent.push(() => { Object.assign(wr.light, { mode: L.mode, brightness: L.brightness, speed: L.speed }); });
  }
  const shot = st.lcd.current !== wr.lcd.current && ui.file && window.aula;
  if (shot) {
    ops.push({ op: 'screen_upload', path: window.aula.pathForFile(ui.file) });
    onSent.push(() => { wr.lcd = clone(st.lcd); });
  }
  diffs().forEach(d => {
    if (d.tag === 'Normal layer') return;
    if (d.tag === 'Lighting' && /^(Effect|Brightness|Speed) /.test(d.text)) return;
    if (d.tag === 'Screen' && d.text.startsWith('Screen image') && shot) return;
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
