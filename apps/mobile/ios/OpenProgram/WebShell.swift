import SwiftUI
import UIKit
import WebKit
import ObjectiveC

/// The OpenProgram UI for one computer.
///
/// Recreated only when the computer changes. When its address changes
/// (Wi-Fi to cellular, or the probe finds another one) the page stays:
/// requests read the address each time, and the page is told the new
/// socket address (`openprogram-mobile-address`, apps/web/lib/mobile/bridge.ts).
struct WebShell: UIViewRepresentable {
    let computer: Computer
    /// Every computer paired with this phone, for the page's switcher.
    let computers: [Computer]
    let origin: String
    let token: String
    let onAction: (String, [String: Any]) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onAction: onAction, origin: origin) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let root = Bundle.main.url(forResource: "web", withExtension: nil)!
        let coordinator = context.coordinator
        coordinator.listed = computers.map { $0.id + "\u{1}" + $0.name }
        let handler = AppSchemeHandler(root: root) { [token] in (coordinator.origin, token) }
        context.coordinator.handler = handler
        config.setURLSchemeHandler(handler, forURLScheme: AppSchemeHandler.scheme)
        config.websiteDataStore = .default()
        config.allowsInlineMediaPlayback = true

        let content = WKUserContentController()
        content.add(context.coordinator, name: "openprogram")
        content.addUserScript(WKUserScript(source: bootstrap(origin), injectionTime: .atDocumentStart, forMainFrameOnly: true))
        // An app's screens do not zoom: two quick taps on two controls, or a
        // pinch, would otherwise magnify the whole UI.
        content.addUserScript(WKUserScript(source: """
        document.querySelector('meta[name="viewport"]')?.setAttribute("content",
          "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover, interactive-widget=resizes-content");
        """, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        config.userContentController = content

        let view = WKWebView(frame: .zero, configuration: config)
        view.isOpaque = false
        // The page's own background (--bg-primary of the light and dark
        // themes) shows until its first paint, so the start is not a white flash.
        let page = UIColor { $0.userInterfaceStyle == .dark
            ? UIColor(red: 0x1e / 255, green: 0x1e / 255, blue: 0x20 / 255, alpha: 1)
            : UIColor.white }
        view.backgroundColor = page
        view.scrollView.backgroundColor = page
        // The page lays itself out to the safe area (viewport-fit=cover) and
        // owns its own scrolling.
        view.scrollView.contentInsetAdjustmentBehavior = .never
        view.scrollView.bounces = false
        // The swipe from the left edge back a page is the page's own
        // (components/phone/use-edge-swipe-back.ts): the back-forward
        // gesture does not take the history entries the pages push.
        view.hideFormAccessoryBar()
        coordinator.menus.attach(view)
        #if DEBUG
        view.isInspectable = true
        #endif
        view.load(URLRequest(url: URL(string: AppSchemeHandler.origin + "/chat")!))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        let listed = computers.map { $0.id + "\u{1}" + $0.name }
        guard context.coordinator.origin != origin || context.coordinator.listed != listed else { return }
        let moved = context.coordinator.origin != origin
        context.coordinator.origin = origin
        context.coordinator.listed = listed
        let bridge = String(data: try! JSONSerialization.data(withJSONObject: bridgeValue(origin)), encoding: .utf8)!
        view.evaluateJavaScript("""
        Object.assign(window.openprogramMobile, \(bridge));
        window.dispatchEvent(new Event("\(moved ? "openprogram-mobile-address" : "openprogram-mobile-computers")"));
        """)
    }

    static func dismantleUIView(_ view: WKWebView, coordinator: Coordinator) {
        view.configuration.userContentController.removeScriptMessageHandler(forName: "openprogram")
    }

    /// What the page reads before any of its own scripts run.
    private func bridgeValue(_ origin: String) -> [String: Any] {
        var socket = URLComponents(string: origin)!
        socket.scheme = socket.scheme == "https" ? "wss" : "ws"
        socket.path = "/ws"
        return [
            "socket": ["url": socket.string!, "protocols": ["openprogram.v1", "openprogram.bearer." + token]],
            "computer": ["id": computer.id, "name": computer.name, "via": ComputerStore.isLocal(origin) ? "lan" : "anywhere"],
            "computers": computers.map { ["id": $0.id, "name": $0.name] },
            // Menus are drawn by iOS (NativeMenus.swift).
            "menus": NativeMenus.available,
        ]
    }

    private func bootstrap(_ origin: String) -> String {
        let json = String(data: try! JSONSerialization.data(withJSONObject: bridgeValue(origin)), encoding: .utf8)!
        // The document element exists at document start: the screen's corner
        // radius is set before the first paint, so floating panels round
        // in parallel with the screen (app/styles/base/phone.css).
        return """
        window.openprogramMobile = \(json);
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
        let onAction: (String, [String: Any]) -> Void
        /// Where requests go now; read by the scheme handler on each request.
        var origin: String
        /// The paired computers the page was last told about.
        var listed: [String] = []
        let menus = NativeMenus()

        init(onAction: @escaping (String, [String: Any]) -> Void, origin: String) {
            self.onAction = onAction
            self.origin = origin
        }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
            if action == "menu" {
                menus.show(body)
            } else if action == "menuPress" {
                menus.press(body)
            } else if action == "haptic" {
                switch body["style"] as? String {
                case "medium": UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                case "selection": UISelectionFeedbackGenerator().selectionChanged()
                case "success": UINotificationFeedbackGenerator().notificationOccurred(.success)
                default: UIImpactFeedbackGenerator(style: .light).impactOccurred()
                }
            } else {
                onAction(action, body)
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
