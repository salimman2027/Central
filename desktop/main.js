// NQ Phone لويندوز: صفحة التليفون من السيرفر (WebRTC على السنترال) في شباك صغير + أيقونة جنب الساعة.
// قفل الشباك = يستخبى بس، فالاتصال بالسنترال يفضل شغّال والمكالمات الواردة توصل وتفتح الشباك.
const { app, BrowserWindow, Tray, Menu, shell, session, ipcMain, Notification, nativeImage } = require("electron");
const path = require("path");

const SERVER = process.env.NQ_SERVER || "https://3.89.245.93.sslip.io/phone/";
const HOST = new URL(SERVER).host;
let win = null, tray = null, quitting = false;

if (!app.requestSingleInstanceLock()) { app.quit(); }
app.on("second-instance", () => showWin());
app.setAppUserModelId("sa.nq.phone");
// الصوت يشتغل من غير ما المستخدم يدوس، والشباك المستخبي مايتبطّأش
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");
app.commandLine.appendSwitch("disable-background-timer-throttling");
app.commandLine.appendSwitch("disable-renderer-backgrounding");

function showWin() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show(); win.focus();
}

function createWin() {
  win = new BrowserWindow({
    width: 440, height: 820, minWidth: 360, minHeight: 600,
    title: "NQ Phone",
    icon: path.join(__dirname, "build", "icon.png"),
    autoHideMenuBar: true,
    backgroundColor: "#f4f7f9",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      backgroundThrottling: false,   // مهم: الاتصال بالسنترال يفضل شغّال والشباك مستخبي
      spellcheck: false,
    },
  });
  win.loadURL(SERVER);

  // قفل = استخبى في الأيقونة جنب الساعة (عشان المكالمات الواردة)
  win.on("close", (e) => {
    if (!quitting) {
      e.preventDefault(); win.hide();
      if (!app._hintShown && Notification.isSupported()) {
        app._hintShown = true;
        new Notification({ title: "NQ Phone لسه شغّال", body: "التطبيق في الأيقونة جنب الساعة وهيرن لما تيجي مكالمة. للخروج: كليك يمين على الأيقونة ← خروج." }).show();
      }
    }
  });

  // روابط بره السيرفر (واتساب، خرايط، مواقع) تتفتح في المتصفح
  win.webContents.setWindowOpenHandler(({ url }) => {
    try { if (new URL(url).host === HOST) { win.loadURL(url); return { action: "deny" }; } } catch (e) {}
    shell.openExternal(url); return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    try { const u = new URL(url); if (u.host !== HOST && /^https?:$/.test(u.protocol)) { e.preventDefault(); shell.openExternal(url); } } catch (x) {}
  });
  // مفيش نت: نحاول تاني كل 5 ثواني
  win.webContents.on("did-fail-load", (e, code, desc, url, isMain) => {
    if (!isMain || code === -3) return;
    win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(
      `<html dir="rtl"><body style="font-family:Segoe UI,Tahoma;text-align:center;padding:80px 20px;color:#334155;background:#f4f7f9">
       <div style="font-size:52px">📡</div><h2>مش قادر يوصل للسيرفر</h2><p style="opacity:.7">بيحاول تاني لوحده…</p></body></html>`));
    setTimeout(() => win && win.loadURL(SERVER), 5000);
  });
}

function createTray() {
  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, "build", "tray.png")));
  tray.setToolTip("NQ Phone — متصل بالسنترال");
  const menu = Menu.buildFromTemplate([
    { label: "افتح NQ Phone", click: showWin },
    { label: "إعادة تحميل", click: () => win && win.reload() },
    { type: "separator" },
    { label: "يشتغل مع تشغيل الويندوز", type: "checkbox", checked: app.getLoginItemSettings().openAtLogin,
      click: (m) => app.setLoginItemSettings({ openAtLogin: m.checked, args: ["--hidden"] }) },
    { type: "separator" },
    { label: "خروج", click: () => { quitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(menu);
  tray.on("click", showWin);
}

app.whenReady().then(() => {
  // المايك والإشعارات: مسموحين لصفحة السيرفر بتاعنا بس
  const ok = (wc, perm) => { try { return new URL(wc.getURL()).host === HOST && ["media", "notifications", "clipboard-read", "clipboard-sanitized-write", "fullscreen"].includes(perm); } catch (e) { return false; } };
  session.defaultSession.setPermissionRequestHandler((wc, perm, cb) => cb(ok(wc, perm)));
  session.defaultSession.setPermissionCheckHandler((wc, perm) => ok(wc, perm));

  // أول مرة: يشتغل مع الويندوز (مستخبي) عشان المكالمات توصل من غير ما حد يفتحه
  if (!app.getLoginItemSettings().wasOpenedAtLogin && process.argv.indexOf("--hidden") < 0 && !require("fs").existsSync(path.join(app.getPath("userData"), ".nq-login"))) {
    app.setLoginItemSettings({ openAtLogin: true, args: ["--hidden"] });
    try { require("fs").writeFileSync(path.join(app.getPath("userData"), ".nq-login"), "1"); } catch (e) {}
  }

  createWin();
  createTray();
  if (process.argv.includes("--hidden")) win.once("ready-to-show", () => win.hide());
});

ipcMain.on("nq:ring", () => { showWin(); if (win) { win.setAlwaysOnTop(true); win.flashFrame(true); setTimeout(() => win && win.setAlwaysOnTop(false), 3000); } });
ipcMain.on("nq:call", (e, on) => { if (win) { win.flashFrame(false); tray && tray.setToolTip(on ? "NQ Phone — في مكالمة" : "NQ Phone — متصل بالسنترال"); } });
ipcMain.on("nq:version", (e) => { e.returnValue = app.getVersion(); });

app.on("before-quit", () => { quitting = true; });
app.on("window-all-closed", (e) => { /* بيفضل شغّال في الأيقونة */ });
