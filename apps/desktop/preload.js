const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('aula', {
  hid: req => ipcRenderer.invoke('hid', req),
  info: () => ipcRenderer.invoke('info'),
  boardImage: () => ipcRenderer.invoke('board-image'),
  pathForFile: file => webUtils.getPathForFile(file),
});
