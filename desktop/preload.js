// جسر صغير بين صفحة التليفون وويندوز
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("NQDesk", {
  ring: (num) => ipcRenderer.send("nq:ring", String(num || "")),   // مكالمة واردة: افتح الشباك قدّام
  callState: (on) => ipcRenderer.send("nq:call", !!on),
  version: () => ipcRenderer.sendSync("nq:version"),
});
