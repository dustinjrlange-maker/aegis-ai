const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('browserAPI', {
    openExternal: (url) => ipcRenderer.invoke('browser-open-external', url),
    openInSpecificBrowser: (url, browser) => ipcRenderer.invoke('open-in-specific-browser', url, browser),
    closeWindow: () => ipcRenderer.invoke('browser-close'),
    onNavigate: (callback) => ipcRenderer.on('navigate-to', (_event, url) => callback(url)),
    onBrowserPref: (callback) => ipcRenderer.on('browser-pref', (_event, pref) => callback(pref)),
});
