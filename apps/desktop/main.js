// Electron main process: owns the window and the only path to the keyboard
// (services/hid/bridge.py). Live writes stay off unless AULA_LIVE_WRITES=1.
const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
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
