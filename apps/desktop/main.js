// Electron main process: owns the window and the only path to the keyboard
// (services/hid/bridge.py). Live writes stay off unless AULA_LIVE_WRITES=1.
const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn, execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..');
const LIVE = process.env.AULA_LIVE_WRITES === '1';
const OPS = new Set(['status', 'light', 'remap_show', 'remap_apply', 'clock_sync', 'screen_inspect', 'screen_upload', 'settings_show', 'settings_apply', 'macro_show', 'macro_apply']);
const BOARD_IMAGE = path.join(ROOT, '.local-assets', 'f75max-black.png');

function python() {
  if (process.env.AULA_PYTHON) return process.env.AULA_PYTHON;
  const candidates = [path.join(ROOT, '.venv', 'Scripts', 'python.exe'), path.join(ROOT, '.venv', 'bin', 'python')];
  return candidates.find(p => fs.existsSync(p)) || 'python';
}

const fail = output => ({ ok: false, code: 2, dry_run: true, argv: [], output });

function runBridge(req) {
  return new Promise(resolve => {
    const child = spawn(python(), ['-m', 'services.hid.bridge'], {
      cwd: ROOT,
      env: { ...process.env, AULA_LIVE_WRITES: LIVE ? '1' : '0' },
      windowsHide: true,
    });
    let out = '', err = '';
    child.stdout.on('data', d => { out += d; });
    child.stderr.on('data', d => { err += d; });
    child.on('error', e => resolve(fail('error: cannot start Python: ' + e.message)));
    child.on('close', () => {
      try { resolve(JSON.parse(out)); } catch { resolve(fail((err || out || 'no reply from the HID bridge').trim())); }
    });
    child.stdin.end(JSON.stringify(req));
  });
}

// One device transaction at a time.
let queue = Promise.resolve();
ipcMain.handle('hid', (_e, req) => {
  if (!req || typeof req !== 'object' || !OPS.has(req.op)) return fail('error: op not allowed');
  const run = queue.then(() => runBridge(req)).then(reply => {
    if (req.op !== 'status') {
      const line = JSON.stringify({ at: new Date().toISOString(), live: LIVE, req: { ...req, mappings: req.mappings && req.mappings.length }, reply }) + '\n';
      fs.appendFile(path.join(app.getPath('userData'), 'bridge.log'), line, () => {});
    }
    return reply;
  });
  queue = run.catch(() => {});
  return run;
});
// Host layer (services/hostlayer, AutoHotkey v2): this app only writes its config and starts engine.ahk.
// It never stops or touches any other AutoHotkey process (the Cidoo runtime must keep running).
const ENGINE = path.join(ROOT, 'services', 'hostlayer', 'engine.ahk');
const HOST_CFG = path.join(process.env.APPDATA || app.getPath('appData'), 'aula-desktop', 'hostlayer.json');
const HOST_MAX = 256 * 1024;

function ahkExe() {
  const c = [process.env.AHK_EXE, path.join(process.env.LOCALAPPDATA || '', 'Programs', 'AutoHotkey', 'v2', 'AutoHotkey64.exe'),
    path.join(process.env.ProgramFiles || 'C:\\Program Files', 'AutoHotkey', 'v2', 'AutoHotkey64.exe')];
  return c.find(p => p && fs.existsSync(p)) || null;
}
function engineRunning() {
  return new Promise(resolve => {
    const ps = "Get-CimInstance Win32_Process -Filter \"Name like 'AutoHotkey%'\" | ForEach-Object { $_.CommandLine }";
    execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], { windowsHide: true, timeout: 15000 }, (err, out) => {
      const norm = x => x.replace(/\//g, '\\').toLowerCase();
      resolve(!err && out.split(/\r?\n/).some(l => norm(l).includes('services\\hostlayer\\engine.ahk')));
    });
  });
}
function hostShape(c) {
  if (!c || typeof c !== 'object' || Array.isArray(c) || c.schemaVersion !== 1 || !Number.isInteger(c.tapHoldMs)
    || !c.bindings || typeof c.bindings !== 'object' || !Object.keys(c.bindings).length) return 'config must have schemaVersion 1, tapHoldMs and at least one binding';
  if (Object.keys(c.bindings).some(k => /^f1[67]$/i.test(k))) return 'F16 and F17 belong to the Cidoo knob and cannot be bound';
  if (JSON.stringify(c).length > HOST_MAX) return 'config is too large';
  return null;
}
ipcMain.handle('hostlayer', async (_e, req) => {
  const base = (ok, output, extra) => Object.assign({ ok, code: ok ? 0 : 2, dry_run: false, argv: ['hostlayer', String(req && req.op)], output }, extra);
  if (!req || typeof req !== 'object') return base(false, 'error: bad request');
  if (req.op === 'status') {
    const ahk = ahkExe(), running = await engineRunning();
    return base(true, 'AutoHotkey v2: ' + (ahk || 'not found') + '\nengine: ' + (running ? 'running' : 'not running'), { ahk, running, config: HOST_CFG, live: LIVE });
  }
  if (req.op === 'write') {
    const bad = hostShape(req.config);
    if (bad) return base(false, 'error: ' + bad);
    const text = JSON.stringify(req.config, null, 2) + '\n';
    if (!LIVE) return base(true, text, { dry_run: true, path: HOST_CFG });
    try {
      fs.mkdirSync(path.dirname(HOST_CFG), { recursive: true });
      const tmp = HOST_CFG + '.tmp';
      fs.writeFileSync(tmp, text);
      const ahk = ahkExe();
      if (ahk) {   // never install a config the engine would refuse; /validate registers nothing
        const bad = await new Promise(resolve => execFile(ahk, ['/ErrorStdOut', ENGINE, '/validate', tmp], { cwd: path.dirname(ENGINE), windowsHide: true, timeout: 15000 },
          (err, out, errOut) => resolve(err ? String(out || errOut || err.message).trim() : null)));
        if (bad) { try { fs.unlinkSync(tmp); } catch {} return base(false, 'error: engine rejected the config: ' + bad); }
      }
      fs.renameSync(tmp, HOST_CFG);   // atomic, so the engine's poller never sees a half file
      return base(true, 'wrote ' + HOST_CFG, { path: HOST_CFG });
    } catch (e) { return base(false, 'error: ' + e.message); }
  }
  if (req.op === 'start') {
    const ahk = ahkExe();
    if (!ahk) return base(false, 'error: AutoHotkey v2 not found (set AHK_EXE)');
    if (await engineRunning()) return base(true, 'engine already running', { running: true });
    if (!LIVE) return base(true, 'would start: ' + ahk + ' ' + ENGINE, { dry_run: true });
    try {
      const c = spawn(ahk, [ENGINE], { cwd: path.dirname(ENGINE), detached: true, stdio: 'ignore', windowsHide: true });
      c.on('error', () => {});
      c.unref();
      return base(true, 'started ' + ENGINE, { running: true });
    } catch (e) { return base(false, 'error: ' + e.message); }
  }
  return base(false, 'error: op not allowed');
});
ipcMain.handle('info', () => ({ live: LIVE }));
ipcMain.handle('board-image', () => {
  try { return 'data:image/png;base64,' + fs.readFileSync(BOARD_IMAGE).toString('base64'); } catch { return null; }
});

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 900,
    minWidth: 380,
    backgroundColor: '#0e0f11',
    title: 'AULA F75 Max',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', e => e.preventDefault());
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());
