import SwiftUI
import UIKit
import WebKit
import ObjectiveC

/// The OpenProgram UI for one computer at one address.
///
/// Recreated when the computer or its address changes, so the page always
/// starts with the right `window.openprogramMobile` (apps/web/lib/mobile/bridge.ts).
struct WebShell: UIViewRepresentable {
    let computer: Computer
    let origin: String
    let token: String
    let onAction: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onAction: onAction) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let root = Bundle.main.url(forResource: "web", withExtension: nil)!
        let handler = AppSchemeHandler(root: root) { [origin, token] in (origin, token) }
        context.coordinator.handler = handler
        config.setURLSchemeHandler(handler, forURLScheme: AppSchemeHandler.scheme)
        config.websiteDataStore = .default()
        config.allowsInlineMediaPlayback = true

        let content = WKUserContentController()
        content.add(context.coordinator, name: "openprogram")
        content.addUserScript(WKUserScript(source: bootstrap(), injectionTime: .atDocumentStart, forMainFrameOnly: true))
        // An app's screens do not zoom: two quick taps on two controls, or a
        // pinch, would otherwise magnify the whole UI.
        content.addUserScript(WKUserScript(source: """
        document.querySelector('meta[name="viewport"]')?.setAttribute("content",
          "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover, interactive-widget=resizes-content");
        """, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        config.userContentController = content

        let view = WKWebView(frame: .zero, configuration: config)
        view.isOpaque = false
        view.backgroundColor = .systemBackground
        view.scrollView.backgroundColor = .systemBackground
        // The page lays itself out to the safe area (viewport-fit=cover) and
        // owns its own scrolling.
        view.scrollView.contentInsetAdjustmentBehavior = .never
        view.scrollView.bounces = false
        // The swipe from the left edge back a page is the page's own
        // (components/phone/use-edge-swipe-back.ts): the back-forward
        // gesture does not take the history entries the pages push.
        view.hideFormAccessoryBar()
        #if DEBUG
        view.isInspectable = true
        #endif
        view.load(URLRequest(url: URL(string: AppSchemeHandler.origin + "/chat")!))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}

    static func dismantleUIView(_ view: WKWebView, coordinator: Coordinator) {
        view.configuration.userContentController.removeScriptMessageHandler(forName: "openprogram")
    }

    /// What the page reads before any of its own scripts run.
    private func bootstrap() -> String {
        var socket = URLComponents(string: origin)!
        socket.scheme = socket.scheme == "https" ? "wss" : "ws"
        socket.path = "/ws"
        let bridge: [String: Any] = [
            "socket": ["url": socket.string!, "protocols": ["openprogram.v1", "openprogram.bearer." + token]],
            "computer": ["name": computer.name, "via": ComputerStore.isLocal(origin) ? "lan" : "anywhere"],
        ]
        let json = String(data: try! JSONSerialization.data(withJSONObject: bridge), encoding: .utf8)!
        // The document element exists at document start: the screen's corner
        // radius is set before the first paint, so floating panels round
        // in parallel with the screen (app/styles/base/phone.css).
        return """
        window.openprogramMobile = Object.freeze(\(json));
        document.documentElement.style.setProperty("--phone-screen-radius", "\(Self.screenCornerRadius())px");
        """
    }

    /// The display's corner radius in points. UIKit only has it behind a
    /// private key, so it is looked up by the panel's size; a screen with a
    /// home button has square corners, and an unknown one gets the current
    /// iPhones' radius.
    static func screenCornerRadius() -> Int {
        let size = UIScreen.main.nativeBounds.size
        let long = Int(max(size.width, size.height))
        let short = Int(min(size.width, size.height))
        switch (short, long) {
        case (750, 1334), (1080, 1920), (640, 1136): return 0
        case (828, 1792): return 41
        case (1125, 2436), (1242, 2688): return 39
        case (1080, 2340): return 44
        case (1170, 2532): return 47
        case (1284, 2778): return 53
        case (1179, 2556), (1290, 2796): return 55
        case (1206, 2622), (1320, 2868): return 62
        default:
            return UIDevice.current.userInterfaceIdiom == .pad ? 18 : 55
        }
    }

    final class Coordinator: NSObject, WKScriptMessageHandler {
        var handler: AppSchemeHandler?
        let onAction: (String) -> Void

        init(onAction: @escaping (String) -> Void) { self.onAction = onAction }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
            if action == "haptic" {
                switch body["style"] as? String {
                case "medium": UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                case "selection": UISelectionFeedbackGenerator().selectionChanged()
                case "success": UINotificationFeedbackGenerator().notificationOccurred(.success)
                default: UIImpactFeedbackGenerator(style: .light).impactOccurred()
                }
            } else {
                onAction(action)
            }
        }
    }
}

extension WKWebView {
    /// Drops the bar a web form puts above the keyboard (previous, next,
    /// done): the composer is one field, and a native app's keyboard has no
    /// such bar. WebKit has no setting for it, so the view that takes the
    /// keyboard becomes a subclass whose accessory view is nil, the way
    /// Capacitor's and Cordova's keyboard plugins do it.
    func hideFormAccessoryBar() {
        guard let content = scrollView.subviews.first(where: {
            String(describing: type(of: $0)).hasPrefix("WKContent")
        }), let base = object_getClass(content) else { return }
        let name = "OpenProgram_" + NSStringFromClass(base)
        if let made = NSClassFromString(name) {
            object_setClass(content, made)
            return
        }
        guard let sub = objc_allocateClassPair(base, name, 0),
              let method = class_getInstanceMethod(UIResponder.self, #selector(getter: UIResponder.inputAccessoryView))
        else { return }
        let none: @convention(block) (AnyObject) -> AnyObject? = { _ in nil }
        class_addMethod(sub, #selector(getter: UIResponder.inputAccessoryView),
                        imp_implementationWithBlock(none), method_getTypeEncoding(method))
        objc_registerClassPair(sub)
        object_setClass(content, sub)
    }
}
