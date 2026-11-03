const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('aula', {
  hid: req => ipcRenderer.invoke('hid', req),
  hostlayer: req => ipcRenderer.invoke('hostlayer', req),
  info: () => ipcRenderer.invoke('info'),
  programs: () => ipcRenderer.invoke('program-list'),
  boardImage: () => ipcRenderer.invoke('board-image'),
  pathForFile: file => webUtils.getPathForFile(file),
});
