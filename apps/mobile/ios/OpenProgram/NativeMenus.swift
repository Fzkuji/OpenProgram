import UIKit
import WebKit

/// The page's menus, drawn by iOS (UIMenu), so they look and move exactly
/// as the system's own do: the menu from a button (the computer's name, the
/// conversation's settings) and the menu of a long press (a chat, a
/// message), with the pressed element lifted over a blurred screen.
///
/// The page sends what to show (apps/web/lib/mobile/native-menu.ts): for a
/// button, `menu` when it is tapped; for a long press, `menuPress` as soon
/// as a finger lands on something that has one, so the menu is ready when
/// the press turns long. A pick comes back as `openprogram-mobile-menu`.
final class NativeMenus: NSObject, UIContextMenuInteractionDelegate {
    private weak var webView: WKWebView?
    private var button: MenuButton?
    /// What a finger is on now and its menu, for a long press.
    private var pressed: Target?
    /// The long press whose menu is showing.
    private var active: Target?

    private struct Target {
        let key: String
        let rect: CGRect
        let radius: CGFloat
        let items: [[String: Any]]
    }

    /// A button opens its menu from the iOS 17.4 call that runs its primary
    /// action; before that the page keeps its own menus.
    static var available: Bool {
        if #available(iOS 17.4, *) { return true }
        return false
    }

    func attach(_ view: WKWebView) {
        webView = view
        view.addInteraction(UIContextMenuInteraction(delegate: self))
    }

    /// A button's menu: a clear button over the page's own, which opens
    /// the menu the way a native button would and goes when it closes.
    func show(_ body: [String: Any]) {
        guard let view = webView, let target = Self.target(body) else { return }
        button?.removeFromSuperview()
        let menuButton = MenuButton(frame: target.rect)
        menuButton.showsMenuAsPrimaryAction = true
        menuButton.preferredMenuElementOrder = .fixed
        menuButton.menu = UIMenu(children: elements(target.items, key: target.key))
        menuButton.onEnd = { [weak self, weak menuButton] in
            menuButton?.removeFromSuperview()
            if self?.button === menuButton { self?.button = nil }
        }
        view.addSubview(menuButton)
        button = menuButton
        if #available(iOS 17.4, *) { menuButton.performPrimaryAction() }
    }

    /// What a finger landed on; nil when it lifts or scrolls away.
    func press(_ body: [String: Any]) {
        pressed = Self.target(body)
    }

    func contextMenuInteraction(_ interaction: UIContextMenuInteraction,
                                configurationForMenuAtLocation location: CGPoint) -> UIContextMenuConfiguration? {
        guard let target = pressed, target.rect.contains(location) else { return nil }
        active = target
        return UIContextMenuConfiguration(identifier: target.key as NSString, previewProvider: nil) { [weak self] _ in
            UIMenu(children: self?.elements(target.items, key: target.key) ?? [])
        }
    }

    func contextMenuInteraction(_ interaction: UIContextMenuInteraction, configuration: UIContextMenuConfiguration,
                                highlightPreviewForItemWithIdentifier identifier: any NSCopying) -> UITargetedPreview? {
        preview(configuration)
    }

    func contextMenuInteraction(_ interaction: UIContextMenuInteraction, configuration: UIContextMenuConfiguration,
                                dismissalPreviewForItemWithIdentifier identifier: any NSCopying) -> UITargetedPreview? {
        preview(configuration)
    }

    /// The pressed element itself, lifted: a snapshot of its part of the page.
    private func preview(_ configuration: UIContextMenuConfiguration) -> UITargetedPreview? {
        guard let view = webView, let target = active,
              (configuration.identifier as? String) == target.key,
              let snapshot = view.resizableSnapshotView(from: target.rect, afterScreenUpdates: false, withCapInsets: .zero)
        else { return nil }
        let parameters = UIPreviewParameters()
        parameters.visiblePath = UIBezierPath(roundedRect: CGRect(origin: .zero, size: target.rect.size), cornerRadius: target.radius)
        parameters.backgroundColor = view.backgroundColor
        snapshot.frame = CGRect(origin: .zero, size: target.rect.size)
        return UITargetedPreview(view: snapshot, parameters: parameters,
                                 target: UIPreviewTarget(container: view, center: CGPoint(x: target.rect.midX, y: target.rect.midY)))
    }

    /// The page's items as menu elements: a `children` list is a submenu,
    /// or a section when `inline`; anything else is an action.
    private func elements(_ items: [[String: Any]], key: String) -> [UIMenuElement] {
        items.map { item in
            let title = item["title"] as? String ?? ""
            let image = (item["icon"] as? String).flatMap { UIImage(systemName: $0) }
            if let children = item["children"] as? [[String: Any]] {
                if item["inline"] as? Bool == true {
                    return UIMenu(title: title, options: .displayInline, children: elements(children, key: key))
                }
                let menu = UIMenu(title: title, image: image, children: elements(children, key: key))
                menu.subtitle = item["subtitle"] as? String
                return menu
            }
            let id = item["id"] as? String
            let action = UIAction(title: title, image: image) { [weak self] _ in
                guard let id else { return }
                self?.picked(key: key, id: id)
            }
            action.subtitle = item["subtitle"] as? String
            if item["checked"] as? Bool == true { action.state = .on }
            if item["destructive"] as? Bool == true { action.attributes.insert(.destructive) }
            if item["disabled"] as? Bool == true { action.attributes.insert(.disabled) }
            return action
        }
    }

    private func picked(key: String, id: String) {
        let detail = String(data: try! JSONSerialization.data(withJSONObject: ["key": key, "id": id]), encoding: .utf8)!
        webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('openprogram-mobile-menu', { detail: \(detail) }))")
    }

    private static func target(_ body: [String: Any]) -> Target? {
        guard let key = body["key"] as? String,
              let rect = body["rect"] as? [String: Any],
              let items = body["items"] as? [[String: Any]] else { return nil }
        func number(_ name: String) -> CGFloat { CGFloat((rect[name] as? NSNumber)?.doubleValue ?? 0) }
        return Target(key: key, rect: CGRect(x: number("x"), y: number("y"), width: number("width"), height: number("height")),
                      radius: CGFloat((body["radius"] as? NSNumber)?.doubleValue ?? 12), items: items)
    }
}

/// A clear button that says when its menu has closed.
private final class MenuButton: UIButton {
    var onEnd: (() -> Void)?

    override func contextMenuInteraction(_ interaction: UIContextMenuInteraction,
                                         willEndFor configuration: UIContextMenuConfiguration,
                                         animator: (any UIContextMenuInteractionAnimating)?) {
        super.contextMenuInteraction(interaction, willEndFor: configuration, animator: animator)
        if let animator {
            animator.addCompletion { [weak self] in self?.onEnd?() }
        } else {
            onEnd?()
        }
    }
}
