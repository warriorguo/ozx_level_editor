import AppKit

/// Entry point. Uses programmatic AppKit instead of @main so we can keep the
/// app bundle minimal — no storyboard, no SceneDelegate.
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
