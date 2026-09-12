import Foundation

/// Spawns and supervises `serve.py` as a child process.
///
/// Unlike the Go-backed sibling app this launches an interpreter against the
/// plain Python sources copied into the bundle's Resources. The server is
/// standard-library only and runs on macOS's stock `/usr/bin/python3` (3.9),
/// so there is nothing to compile and nothing to sign — the trade is that the
/// interpreter has to exist, which on a Mac without Xcode Command Line Tools
/// means `/usr/bin/python3` is a stub that offers to install them.
///
/// If that ever becomes a problem, drop a self-contained binary into Resources
/// and `locateInterpreter()` picks it up instead — that is the only place that
/// needs to change.
///
/// A free port is allocated per launch so the app never collides with a
/// `python3 serve.py` already running in a terminal.
///
/// stdout/stderr are appended to `~/Library/Logs/ozx-level-studio.log` rather
/// than NSLog, to keep the macOS Console clean.
final class PythonServer {
    enum StartError: Error, LocalizedError {
        case sourcesNotFound
        case interpreterNotFound
        case portAllocationFailed
        case healthCheckTimeout
        case process(Error)

        var errorDescription: String? {
            switch self {
            case .sourcesNotFound:
                return "Bundled serve.py not found in app resources."
            case .interpreterNotFound:
                return "No usable python3 found. On a fresh Mac, run "
                     + "`xcode-select --install` once to install it."
            case .portAllocationFailed:
                return "Could not allocate a local port for the editor."
            case .healthCheckTimeout:
                return "Editor server did not become ready in time."
            case .process(let err):
                return "Failed to launch editor server: \(err.localizedDescription)"
            }
        }
    }

    /// URL the WebView should load once `start()` resolves successfully.
    private(set) var url: URL?

    private var process: Process?
    private var logHandle: FileHandle?

    func start() throws {
        guard let script = Self.locateScript() else { throw StartError.sourcesNotFound }
        guard let interpreter = Self.locateInterpreter() else {
            throw StartError.interpreterNotFound
        }
        let port = try Self.allocateFreePort()

        let handle = Self.openLog(port: port, interpreter: interpreter)
        self.logHandle = handle

        let p = Process()
        p.executableURL = interpreter
        // --no-open stops the server opening Safari alongside the WKWebView
        // we are about to point at it.
        p.arguments = [script.path, "--port", String(port), "--no-open"]
        p.currentDirectoryURL = script.deletingLastPathComponent()
        if let handle = handle {
            p.standardOutput = handle
            p.standardError = handle
        }
        // Unbuffered, or the log stays empty until the process exits — which
        // is exactly when you most want to read it.
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env

        do {
            try p.run()
        } catch {
            throw StartError.process(error)
        }
        self.process = p

        let candidate = URL(string: "http://127.0.0.1:\(port)/")!
        guard Self.waitForHealth(at: candidate) else {
            p.terminate()
            throw StartError.healthCheckTimeout
        }
        self.url = candidate
    }

    func stop() {
        process?.terminate()
        process = nil
        try? logHandle?.close()
        logHandle = nil
    }

    // MARK: - Locating things

    /// `serve.py` inside the bundle, falling back to the repo checkout so the
    /// app can be run from a development build without bundling first.
    private static func locateScript() -> URL? {
        if let bundled = Bundle.main.url(forResource: "serve", withExtension: "py") {
            return bundled
        }
        let devPath = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // LevelStudio
            .deletingLastPathComponent()   // Sources
            .deletingLastPathComponent()   // swift-app
            .appendingPathComponent("serve.py")
        return FileManager.default.fileExists(atPath: devPath.path) ? devPath : nil
    }

    /// A bundled interpreter wins if one was shipped; otherwise the system one.
    /// This is the single place to change if we ever bundle a runtime.
    private static func locateInterpreter() -> URL? {
        if let bundled = Bundle.main.url(forResource: "level-studio", withExtension: nil),
           FileManager.default.isExecutableFile(atPath: bundled.path) {
            return bundled
        }
        for candidate in ["/usr/bin/python3", "/usr/local/bin/python3",
                          "/opt/homebrew/bin/python3"] {
            if FileManager.default.isExecutableFile(atPath: candidate) {
                return URL(fileURLWithPath: candidate)
            }
        }
        return nil
    }

    /// Bind port 0, read back what the kernel assigned, release it. Racy in
    /// principle; in practice the window is microseconds and the alternative
    /// is colliding with the user's own terminal session.
    private static func allocateFreePort() throws -> UInt16 {
        let sock = socket(AF_INET, SOCK_STREAM, 0)
        guard sock >= 0 else { throw StartError.portAllocationFailed }
        defer { close(sock) }

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = 0
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0 else { throw StartError.portAllocationFailed }

        var actual = sockaddr_in()
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let got = withUnsafeMutablePointer(to: &actual) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getsockname(sock, $0, &len)
            }
        }
        guard got == 0 else { throw StartError.portAllocationFailed }
        return UInt16(bigEndian: actual.sin_port)
    }

    // MARK: - Readiness

    /// Poll `/health` until it answers. That endpoint is deliberately trivial
    /// on the server side so readiness does not wait for the data index.
    private static func waitForHealth(at base: URL, timeout: TimeInterval = 20) -> Bool {
        let health = base.appendingPathComponent("health")
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            var request = URLRequest(url: health)
            request.timeoutInterval = 1
            let semaphore = DispatchSemaphore(value: 0)
            var ok = false
            URLSession.shared.dataTask(with: request) { _, response, _ in
                if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                    ok = true
                }
                semaphore.signal()
            }.resume()
            _ = semaphore.wait(timeout: .now() + 2)
            if ok { return true }
            Thread.sleep(forTimeInterval: 0.15)
        }
        return false
    }

    // MARK: - Logging

    private static func logURL() -> URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/ozx-level-studio.log")
    }

    private static func openLog(port: UInt16, interpreter: URL) -> FileHandle? {
        let url = logURL()
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: url) else { return nil }
        handle.seekToEndOfFile()
        let header = "\n--- level-studio launch \(Date()) "
                   + "port=\(port) python=\(interpreter.path) ---\n"
        if let data = header.data(using: .utf8) { handle.write(data) }
        return handle
    }
}
