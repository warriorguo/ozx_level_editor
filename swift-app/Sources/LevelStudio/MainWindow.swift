import AppKit
import WebKit

/// Native window hosting a WKWebView pointed at the bundled Python server. Kept
/// deliberately spartan — no custom title bar tricks or window-state
/// persistence; macOS handles all of that for free at .titled + .resizable.
final class MainWindowController: NSWindowController, WKUIDelegate, WKScriptMessageHandler {
    private var webView: WKWebView!

    init(initialURL: URL) {
        // Build the window first with an empty placeholder so we can call
        // super.init and gain `self`. The WebView is constructed *after*
        // super.init so we can register script message handlers (which need
        // `self`) before the WKWebView snapshots its configuration.
        let visibleFrame = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1600, height: 1000)
        let width = max(1280, visibleFrame.width * 0.9)
        let height = max(800, visibleFrame.height * 0.9)

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false)
        window.title = "OZX Level Studio"
        window.minSize = NSSize(width: 1024, height: 700)
        window.center()
        // Bumping the autosave-name suffix discards any saved frame from a
        // previous version so this enlarged default actually applies on the
        // next launch; user-initiated resizes from here on persist normally.
        window.setFrameAutosaveName("MainWindow.v2")

        super.init(window: window)

        // Now build the WebView. Script-message handlers must be on the
        // controller *before* WKWebView is constructed: the configuration is
        // copied at init time, so post-creation mutations don't reach the
        // running WebView.
        let config = WKWebViewConfiguration()
        let prefs = WKPreferences()
        prefs.javaScriptCanOpenWindowsAutomatically = true
        config.preferences = prefs
        config.websiteDataStore = .nonPersistent() // each launch starts clean

        let contentController = WKUserContentController()
        contentController.add(self, name: "pickFolder")
        config.userContentController = contentController

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsBackForwardNavigationGestures = false
        webView.uiDelegate = self
        self.webView = webView
        window.contentView = webView
        webView.load(URLRequest(url: initialURL))
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) not used")
    }

    deinit {
        let ucc = webView.configuration.userContentController
        ucc.removeScriptMessageHandler(forName: "pickFolder")
    }

    /// Replaces the current URL — handy if we ever add a "reload server" menu item.
    func load(_ url: URL) {
        webView.load(URLRequest(url: url))
    }

    // MARK: - WKScriptMessageHandler (clipboard bridge)

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        switch message.name {
        case "pickFolder":
            presentFolderPicker()
        default:
            NSLog("unknown script message: \(message.name)")
        }
    }

    /// Real folder chooser for the project root. The page falls back to a text
    /// field when this bridge is absent (i.e. opened in a plain browser), and
    /// both routes go through the same PUT /api/config — the path chosen here
    /// is handed straight back to the page rather than applied natively.
    private func presentFolderPicker() {
        let panel = NSOpenPanel()
        panel.title = "Choose your ozx_base checkout"
        panel.message = "Pick the folder containing Assets/StreamingAssets/GameData"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Use This Folder"

        panel.beginSheetModal(for: window ?? NSApp.keyWindow ?? NSWindow()) { [weak self] response in
            guard response == .OK, let path = panel.url?.path else { return }
            // Escape for embedding in a JS string literal.
            let escaped = path
                .replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "'", with: "\\'")
            self?.webView.evaluateJavaScript("window.onFolderPicked('\(escaped)')") { _, err in
                if let err = err { NSLog("pickFolder bridge: \(err.localizedDescription)") }
            }
        }
    }

    // MARK: - WKUIDelegate

    func webView(_ webView: WKWebView,
                 runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = "OZX Level Studio"
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window ?? NSApp.keyWindow ?? NSWindow()) { _ in
            completionHandler()
        }
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = "OZX Level Studio"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window ?? NSApp.keyWindow ?? NSWindow()) { response in
            completionHandler(response == .alertFirstButtonReturn)
        }
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = "OZX Level Studio"
        alert.informativeText = prompt
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        input.stringValue = defaultText ?? ""
        alert.accessoryView = input
        alert.beginSheetModal(for: window ?? NSApp.keyWindow ?? NSWindow()) { response in
            if response == .alertFirstButtonReturn {
                completionHandler(input.stringValue)
            } else {
                completionHandler(nil)
            }
        }
    }
}
