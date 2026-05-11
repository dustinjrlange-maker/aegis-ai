const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage, dialog, session } = require('electron');
const { spawn } = require('child_process');
const net = require('net');
const path = require('path');
const fs = require('fs');
const http = require('http');

const PROJECT_ROOT = path.resolve(__dirname, '..');
const isDev = process.argv.includes('--dev');
const WINDOW_STATE_FILE = path.join(app.getPath('userData'), 'window-state.json');

// Enable camera/microphone access in Chromium
app.commandLine.appendSwitch('enable-features', 'WebRTCPipeWireCapturer');
app.commandLine.appendSwitch('enable-media-stream');

// Note: do NOT use app.disableHardwareAcceleration() or --disable-gpu-compositing
// as they break <webview> rendering (causes narrow-bar display bug).
// Video black-frame fix is handled in frontend with autoplay/playsinline attributes.

// Suppress JS error dialogs — log to console instead
dialog.showErrorBox = (title, content) => {
    console.error(`[electron] ${title}: ${content}`);
};

let mainWindow = null;
let tray = null;
let pythonProcess = null;
let serverPort = 8484;

// ── Port finder ──────────────────────────────────────────────────────────────
// Always use port 8484 so localStorage (origin-scoped) persists across restarts.
// Retry with delay if the port is briefly held by a previous instance shutting down.

function waitForPort(port, maxRetries = 15) {
    return new Promise((resolve, reject) => {
        let attempt = 0;
        function tryOnce() {
            const srv = net.createServer();
            srv.listen(port, '0.0.0.0', () => {
                srv.close(() => resolve(port));
            });
            srv.on('error', () => {
                attempt++;
                if (attempt >= maxRetries) {
                    return reject(new Error(`Port ${port} unavailable after ${maxRetries} retries`));
                }
                console.log(`[electron] Port ${port} busy, retrying (${attempt}/${maxRetries})...`);
                setTimeout(tryOnce, 1000);
            });
        }
        tryOnce();
    });
}

// ── Window state persistence ────────────────────────────────────────────────

function loadWindowState() {
    try {
        return JSON.parse(fs.readFileSync(WINDOW_STATE_FILE, 'utf8'));
    } catch (_) {
        return null;
    }
}

function saveWindowState() {
    if (!mainWindow) return;
    try {
        const maximized = mainWindow.isMaximized();
        // Save normal (non-maximized) bounds so restore works correctly
        const bounds = maximized ? mainWindow.getNormalBounds() : mainWindow.getBounds();
        fs.writeFileSync(WINDOW_STATE_FILE, JSON.stringify({ bounds, maximized }));
    } catch (_) { /* best effort */ }
}

// ── Python subprocess ────────────────────────────────────────────────────────

function startPython(port) {
    const args = ['start.py', '--port', String(port)];
    pythonProcess = spawn('python', args, {
        cwd: PROJECT_ROOT,
        stdio: ['ignore', 'pipe', 'pipe'],
    });

    pythonProcess.stdout.on('data', (data) => {
        if (isDev) process.stdout.write(`[py] ${data}`);
    });
    pythonProcess.stderr.on('data', (data) => {
        if (isDev) process.stderr.write(`[py] ${data}`);
    });
    pythonProcess.on('exit', (code) => {
        if (isDev) console.log(`[py] exited with code ${code}`);
        pythonProcess = null;
    });
}

function killPython() {
    // Send graceful shutdown request regardless of whether we spawned the process
    try {
        const req = http.request(
            { hostname: '127.0.0.1', port: serverPort, path: '/api/shutdown', method: 'POST', timeout: 2000 },
            () => {},
        );
        req.on('error', () => {});
        req.end();
    } catch (_) { /* best effort */ }

    if (!pythonProcess) return;
    // Force kill our child process tree immediately
    try {
        if (process.platform === 'win32') {
            require('child_process').execSync(
                `taskkill /pid ${pythonProcess.pid} /T /F`,
                { stdio: 'ignore', timeout: 5000 }
            );
        } else {
            pythonProcess.kill('SIGKILL');
        }
    } catch (_) { /* already dead */ }
    pythonProcess = null;
}

// Kill any orphaned Python server holding our port (from a previous crashed session)
function killOrphanedServer(port) {
    if (process.platform !== 'win32') return Promise.resolve();
    return new Promise((resolve) => {
        const { exec } = require('child_process');
        exec(`netstat -ano | findstr :${port} | findstr LISTENING`, { timeout: 5000 }, (err, stdout) => {
            if (err || !stdout.trim()) return resolve();
            // Extract PIDs from netstat output
            const pids = new Set();
            stdout.trim().split('\n').forEach(line => {
                const parts = line.trim().split(/\s+/);
                const pid = parts[parts.length - 1];
                if (pid && pid !== '0') pids.add(pid);
            });
            if (pids.size === 0) return resolve();
            console.log(`[electron] Killing orphaned process(es) on port ${port}: PIDs ${[...pids].join(', ')}`);
            let killed = 0;
            pids.forEach(pid => {
                exec(`taskkill /pid ${pid} /T /F`, { timeout: 5000 }, () => {
                    killed++;
                    if (killed === pids.size) {
                        // Wait a moment for the port to free
                        setTimeout(resolve, 1000);
                    }
                });
            });
        });
    });
}

// ── Ollama health check ──────────────────────────────────────────────────────
// Pike's brain (qwen3:8b) lives in Ollama. The Ollama tray app sometimes
// doesn't bind the API daemon to port 11434 cleanly, leaving the FastAPI
// server unable to reach it. We probe before starting Python; if Ollama is
// down, we spawn `ollama serve` detached so it outlives Electron.

const OLLAMA_PORT = 11434;
let ollamaProcess = null;

function probeOllama() {
    return new Promise((resolve) => {
        const req = http.get(`http://127.0.0.1:${OLLAMA_PORT}/`, (res) => {
            res.resume();
            resolve(true);
        });
        req.on('error', () => resolve(false));
        req.setTimeout(1500, () => { req.destroy(); resolve(false); });
    });
}

function startOllama() {
    try {
        ollamaProcess = spawn('ollama', ['serve'], {
            detached: true,
            stdio: 'ignore',
            windowsHide: true,
            shell: process.platform === 'win32',
        });
        ollamaProcess.on('error', (err) => {
            console.error(`[electron] Ollama spawn error: ${err.message}`);
        });
        ollamaProcess.unref();
        return true;
    } catch (err) {
        console.error(`[electron] Failed to spawn ollama: ${err.message}`);
        return false;
    }
}

async function ensureOllama(timeoutMs = 30000) {
    if (await probeOllama()) {
        console.log('[electron] Ollama already running');
        return true;
    }
    console.log('[electron] Ollama not responding — starting `ollama serve`');
    if (!startOllama()) return false;
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        await new Promise(r => setTimeout(r, 1000));
        if (await probeOllama()) {
            console.log(`[electron] Ollama is up (${Date.now() - start}ms)`);
            return true;
        }
    }
    console.error(`[electron] Ollama failed to start within ${timeoutMs}ms — Pike will be unavailable`);
    return false;
}

// ── Health check ─────────────────────────────────────────────────────────────

function waitForServer(port, timeoutMs = 30000) {
    const start = Date.now();
    return new Promise((resolve, reject) => {
        function poll() {
            if (Date.now() - start > timeoutMs) {
                return reject(new Error('Server start timeout'));
            }
            const req = http.get(`http://127.0.0.1:${port}/api/status`, (res) => {
                // Any HTTP response means the server is up (401 = auth required, still alive)
                res.resume(); // drain the response
                return resolve();
            });
            req.on('error', () => setTimeout(poll, 500));
            req.setTimeout(2000, () => { req.destroy(); setTimeout(poll, 500); });
        }
        poll();
    });
}

// ── Window ───────────────────────────────────────────────────────────────────

function createWindow(port) {
    const saved = loadWindowState();
    const defaults = { width: 1400, height: 900 };
    const bounds = saved?.bounds || defaults;

    mainWindow = new BrowserWindow({
        x: bounds.x,
        y: bounds.y,
        width: bounds.width || defaults.width,
        height: bounds.height || defaults.height,
        minWidth: 800,
        minHeight: 600,
        frame: false,
        backgroundColor: '#000000',
        icon: path.join(__dirname, 'icon.ico'),
        show: false,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            webviewTag: true,
        },
    });

    if (saved?.maximized) mainWindow.maximize();

    mainWindow.loadURL(`http://127.0.0.1:${port}`);

    // Grant camera, microphone, and media permissions automatically
    session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
        console.log(`[electron] Permission request: ${permission}`, details?.mediaTypes || '');
        const allowed = ['media', 'mediaKeySystem', 'notifications', 'fullscreen', 'clipboard-read', 'clipboard-sanitized-write'];
        callback(allowed.includes(permission));
    });
    session.defaultSession.setPermissionCheckHandler((webContents, permission, requestingOrigin, details) => {
        const allowed = ['media', 'mediaKeySystem', 'notifications', 'fullscreen', 'clipboard-read', 'clipboard-sanitized-write'];
        return allowed.includes(permission);
    });

    // Register keyboard shortcuts (frameless windows don't get default ones)
    mainWindow.webContents.on('before-input-event', (event, input) => {
        // Ctrl+Shift+R or F5 = hard reload
        if ((input.control && input.shift && input.key.toLowerCase() === 'r') || input.key === 'F5') {
            mainWindow.webContents.reloadIgnoringCache();
            event.preventDefault();
        }
        // Ctrl+R = reload
        if (input.control && !input.shift && input.key.toLowerCase() === 'r') {
            mainWindow.webContents.reload();
            event.preventDefault();
        }
        // F12 or Ctrl+Shift+I = DevTools
        if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
            mainWindow.webContents.toggleDevTools();
            event.preventDefault();
        }
    });

    mainWindow.once('ready-to-show', () => mainWindow.show());

    // Suppress renderer JS error dialogs — log to console only
    mainWindow.webContents.on('render-process-gone', (_e, details) => {
        console.error('[electron] Renderer crashed:', details.reason);
    });
    mainWindow.webContents.on('console-message', (_e, level, message) => {
        if (level >= 1) console.log(`[renderer:${level}] ${message}`);
    });
    mainWindow.webContents.on('unresponsive', () => {
        console.error('[electron] Window unresponsive');
    });

    // Inject error suppressor before page scripts run — prevents alert/dialog popups
    mainWindow.webContents.on('did-finish-load', () => {
        mainWindow.webContents.executeJavaScript(`
            window.onerror = function(msg, url, line, col, err) {
                console.error('[JS Error]', msg, url + ':' + line);
                return true; // suppress default dialog
            };
            window.onunhandledrejection = function(e) {
                console.error('[Unhandled Promise]', e.reason);
                e.preventDefault();
            };
        `).catch(() => {});
    });

    // Save window position/size on move and resize
    mainWindow.on('move', saveWindowState);
    mainWindow.on('resize', saveWindowState);

    mainWindow.on('close', (e) => {
        saveWindowState();
        if (!app.isQuitting) {
            e.preventDefault();
            mainWindow.hide();
        }
    });
}

// ── Tray ─────────────────────────────────────────────────────────────────────

function createTray() {
    const iconPath = path.join(__dirname, 'tray-icon.png');
    const icon = nativeImage.createFromPath(iconPath);
    tray = new Tray(icon);
    tray.setToolTip('Aegis AI');

    const contextMenu = Menu.buildFromTemplate([
        { label: 'Show', click: () => { if (mainWindow) mainWindow.show(); } },
        { type: 'separator' },
        { label: 'Quit', click: () => { app.isQuitting = true; app.quit(); } },
    ]);
    tray.setContextMenu(contextMenu);

    tray.on('double-click', () => {
        if (mainWindow) mainWindow.show();
    });
}

// ── IPC handlers ─────────────────────────────────────────────────────────────

ipcMain.handle('window-minimize', () => { if (mainWindow) mainWindow.minimize(); });
ipcMain.handle('window-maximize', () => {
    if (!mainWindow) return;
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    else mainWindow.maximize();
});
ipcMain.handle('window-close', () => {
    if (mainWindow) mainWindow.close(); // triggers hide-to-tray
});
ipcMain.handle('window-is-maximized', () => {
    return mainWindow ? mainWindow.isMaximized() : false;
});
ipcMain.handle('open-external', (_event, url) => {
    const { shell } = require('electron');
    if (url && typeof url === 'string') shell.openExternal(url);
});

// ── LCARS Browser Window ─────────────────────────────────────────────────────

let browserWindows = [];

ipcMain.handle('open-lcars-browser', (_event, url, browserPref) => {
    if (!url || typeof url !== 'string') return;
    const browserWin = new BrowserWindow({
        width: 1100,
        height: 800,
        minWidth: 700,
        minHeight: 500,
        frame: false,
        parent: mainWindow,
        backgroundColor: '#000000',
        icon: path.join(__dirname, 'icon.ico'),
        webPreferences: {
            preload: path.join(__dirname, 'browser-preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            webviewTag: true,
        },
    });
    browserWin.loadFile(path.join(__dirname, 'browser.html'));
    browserWin.setMenuBarVisibility(false);
    browserWin.webContents.on('did-finish-load', () => {
        browserWin.webContents.send('navigate-to', url);
        if (browserPref) browserWin.webContents.send('browser-pref', browserPref);
    });
    browserWin.on('closed', () => {
        browserWindows = browserWindows.filter(w => w !== browserWin);
    });
    browserWindows.push(browserWin);
});

ipcMain.handle('browser-close', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) win.close();
});

ipcMain.handle('browser-open-external', (_event, url) => {
    if (!url || typeof url !== 'string') return;
    // Check for user-preferred browser (sent from renderer localStorage via IPC)
    const { shell } = require('electron');
    shell.openExternal(url);
});

ipcMain.handle('open-in-specific-browser', (_event, url, browser) => {
    if (!url || typeof url !== 'string') return;
    const { exec } = require('child_process');
    const browsers = {
        chrome: 'start chrome',
        firefox: 'start firefox',
        edge: 'start msedge',
        brave: 'start brave',
    };
    const cmd = browsers[browser];
    if (cmd) {
        exec(`${cmd} "${url}"`, (err) => {
            if (err) {
                // Fallback to system default if specific browser not found
                const { shell } = require('electron');
                shell.openExternal(url);
            }
        });
    } else {
        const { shell } = require('electron');
        shell.openExternal(url);
    }
});

// ── Single instance lock ─────────────────────────────────────────────────────

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
    app.quit();
} else {
    app.on('second-instance', () => {
        if (mainWindow) {
            if (mainWindow.isMinimized()) mainWindow.restore();
            mainWindow.show();
            mainWindow.focus();
        }
    });
}

// ── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
    try {
        // Kill any orphaned server from a previous crashed session
        await killOrphanedServer(8484);

        // Make sure Ollama (Pike's brain) is reachable before starting Python
        const ollamaOk = await ensureOllama();
        if (!ollamaOk) {
            console.warn('[electron] Continuing without Ollama — chat will fail until it comes up');
        }

        serverPort = await waitForPort(8484);
        console.log(`[electron] Using port ${serverPort}`);

        startPython(serverPort);
        console.log('[electron] Waiting for server...');
        await waitForServer(serverPort);
        console.log('[electron] Server ready');

        createWindow(serverPort);
        createTray();
    } catch (err) {
        console.error('[electron] Startup failed:', err);
        killPython();
        app.quit();
    }
});

app.on('before-quit', () => {
    app.isQuitting = true;
    saveWindowState();
    killPython();
    // Close all child browser windows
    browserWindows.forEach(w => { try { w.destroy(); } catch (_) {} });
    browserWindows = [];
});

app.on('window-all-closed', () => {
    // On macOS apps typically stay open; on Windows/Linux we rely on tray
    // Do nothing — tray keeps the app alive
});

app.on('activate', () => {
    if (mainWindow) mainWindow.show();
});
