const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('velaDesktop', {
  request: (op, args = {}) => ipcRenderer.invoke('vela:request', op, args),
})
