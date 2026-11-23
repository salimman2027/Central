import UIKit
import WebKit
import AVFoundation
import UserNotifications

/// التطبيق = صفحة تليفون NQ من السيرفر جوه WKWebView (المكالمات بـ WebRTC)
/// + جسر "nq" للحاجات اللي محتاجة iOS: السبيكر، جلسة الصوت، الإشعارات، الروابط، التنزيلات.
final class WebViewController: UIViewController, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate {
    private var web: WKWebView!
    private var serverURL: URL {
        let s = (Bundle.main.object(forInfoDictionaryKey: "NQServerURL") as? String) ?? "https://3.89.245.93.sslip.io/phone/"
        return URL(string: s)!
    }
    private var inCall = false
    private var downloadURL: URL?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.96, green: 0.97, blue: 0.98, alpha: 1)

        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.websiteDataStore = .default()                  // localStorage بيفضل (تسجيل الدخول والإعدادات)
        let ver = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? ""
        let js = """
        window.NQiOS = { version: 'iOS \(ver)', post: function(t, d) { try { window.webkit.messageHandlers.nq.postMessage({ t: t, d: d === undefined ? null : d }); } catch (e) {} } };
        """
        cfg.userContentController.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        cfg.userContentController.add(WeakHandler(self), name: "nq")

        web = WKWebView(frame: .zero, configuration: cfg)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = false
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.isOpaque = false
        web.backgroundColor = view.backgroundColor
        if #available(iOS 16.4, *) { web.isInspectable = true }
        web.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(web)
        NSLayoutConstraint.activate([
            web.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            web.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            web.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
        let rc = UIRefreshControl()
        rc.addTarget(self, action: #selector(pullRefresh(_:)), for: .valueChanged)
        web.scrollView.refreshControl = rc

        AVAudioSession.sharedInstance().requestRecordPermission { _ in }
        load()
    }

    private func load() { web.load(URLRequest(url: serverURL, cachePolicy: .reloadRevalidatingCacheData, timeoutInterval: 20)) }

    @objc private func pullRefresh(_ rc: UIRefreshControl) {
        if inCall { rc.endRefreshing(); return }      // مانعملش ريفريش وسط مكالمة
        web.reload(); DispatchQueue.main.asyncAfter(deadline: .now() + 1) { rc.endRefreshing() }
    }

    func appBecameActive() {
        // رجع من الخلفية: نوصل بالسنترال تاني لو الاتصال وقع
        web?.evaluateJavaScript("try{ if (window.NQPhone && NQPhone.ua && !NQPhone.ua.isConnected()) NQPhone.ua.start(); }catch(e){}")
    }

    // MARK: - الجسر من الصفحة
    func userContentController(_ uc: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let t = body["t"] as? String else { return }
        let d = body["d"]
        switch t {
        case "call": setCall((d as? Bool) ?? false)
        case "speaker": setSpeaker((d as? Bool) ?? false)
        case "notify":
            let m = d as? [String: Any] ?? [:]
            notify(title: m["title"] as? String ?? "NQ Phone", body: m["body"] as? String ?? "")
        case "open":
            if let s = d as? String, let u = URL(string: s) { UIApplication.shared.open(u) }
        case "share":
            if let s = d as? String { present(UIActivityViewController(activityItems: [s], applicationActivities: nil), animated: true) }
        case "haptic": UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        default: break
        }
    }

    private func setCall(_ on: Bool) {
        let s = AVAudioSession.sharedInstance()
        inCall = on
        UIDevice.current.isProximityMonitoringEnabled = on     // الشاشة تطفي لما تقرّب الموبايل من ودنك
        if on {
            try? s.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth, .allowBluetoothA2DP])
            try? s.setActive(true)
        } else {
            try? s.overrideOutputAudioPort(.none)
            try? s.setActive(false, options: .notifyOthersOnDeactivation)
        }
    }

    private func setSpeaker(_ on: Bool) {
        let s = AVAudioSession.sharedInstance()
        if s.category != .playAndRecord { try? s.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth, .allowBluetoothA2DP]) }
        try? s.setActive(true)
        try? s.overrideOutputAudioPort(on ? .speaker : .none)
    }

    private func notify(title: String, body: String) {
        let c = UNMutableNotificationContent()
        c.title = title; c.body = body; c.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil))
    }

    // MARK: - المايك (iOS 15+): نسمح لصفحة السيرفر بتاعنا بس
    @available(iOS 15.0, *)
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(origin.host == serverURL.host ? .grant : .prompt)
    }

    // MARK: - الروابط: صفحات السيرفر جوه التطبيق، والباقي (واتساب/تليفون/خرايط/مواقع) بره
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        if action.shouldPerformDownload { return decisionHandler(.download) }
        let scheme = url.scheme?.lowercased() ?? ""
        if scheme == "blob" || scheme == "data" || scheme == "about" { return decisionHandler(.allow) }
        if scheme != "http" && scheme != "https" {
            UIApplication.shared.open(url); return decisionHandler(.cancel)
        }
        if url.host != serverURL.host && action.targetFrame?.isMainFrame != false {
            UIApplication.shared.open(url); return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disp = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        if !response.canShowMIMEType || disp.lowercased().hasPrefix("attachment") { return decisionHandler(.download) }
        decisionHandler(.allow)
    }

    // target=_blank
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = action.request.url {
            if u.host == serverURL.host { webView.load(action.request) } else { UIApplication.shared.open(u) }
        }
        return nil
    }

    // MARK: - التنزيلات (Excel / PDF / ملفات) → مشاركة/حفظ
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let u = FileManager.default.temporaryDirectory.appendingPathComponent(suggestedFilename)
        try? FileManager.default.removeItem(at: u)
        downloadURL = u
        completionHandler(u)
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard let u = downloadURL else { return }
        DispatchQueue.main.async {
            let vc = UIActivityViewController(activityItems: [u], applicationActivities: nil)
            vc.popoverPresentationController?.sourceView = self.view
            self.present(vc, animated: true)
        }
    }

    // MARK: - alert / confirm / prompt من الصفحة
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "تمام", style: .default) { _ in completionHandler() })
        present(a, animated: true)
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "إلغاء", style: .cancel) { _ in completionHandler(false) })
        a.addAction(UIAlertAction(title: "تمام", style: .default) { _ in completionHandler(true) })
        present(a, animated: true)
    }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let a = UIAlertController(title: nil, message: prompt, preferredStyle: .alert)
        a.addTextField { $0.text = defaultText }
        a.addAction(UIAlertAction(title: "إلغاء", style: .cancel) { _ in completionHandler(nil) })
        a.addAction(UIAlertAction(title: "تمام", style: .default) { _ in completionHandler(a.textFields?.first?.text) })
        present(a, animated: true)
    }

    // MARK: - مفيش نت / السيرفر واقع
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { showOffline(error) }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { showOffline(error) }
    }
    private func showOffline(_ error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        let html = """
        <html dir="rtl"><meta name="viewport" content="width=device-width,initial-scale=1">
        <body style="font-family:-apple-system;text-align:center;padding:60px 24px;color:#334155;background:#f4f7f9">
        <div style="font-size:52px">📡</div><h2>مش قادر يوصل للسيرفر</h2>
        <p style="opacity:.7">اتأكد إن النت شغال وجرّب تاني.</p>
        <a href="\(serverURL.absoluteString)" style="display:inline-block;margin-top:16px;padding:12px 28px;border-radius:12px;background:#14b8a6;color:#fff;text-decoration:none;font-weight:600">حاول تاني</a>
        </body></html>
        """
        web.loadHTMLString(html, baseURL: nil)
    }
}

/// عشان WKUserContentController مايمسكش الكنترولر (memory leak)
private final class WeakHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ t: WKScriptMessageHandler) { target = t }
    func userContentController(_ uc: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(uc, didReceive: message)
    }
}
