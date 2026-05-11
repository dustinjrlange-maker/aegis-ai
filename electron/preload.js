const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    windowMinimize: () => ipcRenderer.invoke('window-minimize'),
    windowMaximize: () => ipcRenderer.invoke('window-maximize'),
    windowClose: () => ipcRenderer.invoke('window-close'),
    windowIsMaximized: () => ipcRenderer.invoke('window-is-maximized'),
    openExternal: (url) => ipcRenderer.invoke('open-external', url),
    openLcarsBrowser: (url, browserPref) => ipcRenderer.invoke('open-lcars-browser', url, browserPref),
    openInSpecificBrowser: (url, browser) => ipcRenderer.invoke('open-in-specific-browser', url, browser),
});
