#if os(macOS)
import AppKit
import SteadyKit
import SwiftUI

/// How see-through the floating monitor is: Glass (the default) is frosted glass, the screen behind
/// blurred by the system; Off is solid; Low and High fade it while the pointer is elsewhere.
enum FloatingLook: String, CaseIterable {
    case off, glass, low, high

    /// Opacity of the whole window while the pointer is not on it.
    var awayAlpha: CGFloat {
        switch self {
        case .off, .glass: 1
        case .low: 0.6
        case .high: 0.35
        }
    }
}

/// The floating monitor's appearance, as in the Windows version: follow the system, or light or dark.
enum FloatingTheme: String, CaseIterable {
    case system, light, dark

    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}

/// The floating monitor (ADR-0022): a small window of its own that stays on top of the other
/// windows. It is a non-activating panel, so working in another app keeps the keyboard there.
/// Hiding it releases its SwiftUI tree, which ends the measuring that tree started.
@MainActor
final class FloatingMonitorController: NSObject, NSWindowDelegate {
    static let barSize = NSSize(width: 300, height: 36)
    static let tableSize = NSSize(width: 340, height: 470)
    /// Where the panel opens the first time: this far from the screen's top-right corner.
    private static let margin: CGFloat = 16
    /// Within this distance of a screen edge, the panel grows away from that edge.
    private static let edgeDistance: CGFloat = 60
    private static let positionKey = "floating.topLeft"

    private weak var model: AppModel?
    private var panel: NSPanel?
    private var expanded = false
    private var look = FloatingLook.glass

    init(model: AppModel) {
        self.model = model
        super.init()
    }

    /// Opens the panel, or brings it forward if it is already open.
    func show(expanded: Bool, look: FloatingLook) {
        self.look = look
        if let panel {
            panel.orderFrontRegardless()
            return
        }
        guard let model else { return }
        self.expanded = expanded
        let size = expanded ? Self.tableSize : Self.barSize
        let panel = NSPanel(contentRect: NSRect(origin: .zero, size: size),
                            styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
                            backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.isMovableByWindowBackground = true
        panel.delegate = self
        panel.contentView = NSHostingView(rootView: FloatingMonitorView().environment(model))
        panel.setFrame(placed(size), display: false)
        panel.sharingType = look == .glass ? .none : .readOnly
        panel.alphaValue = look.awayAlpha
        self.panel = panel
        panel.orderFrontRegardless()
    }

    /// Ends the panel: its content goes too, so its measuring stops with it.
    func hide() {
        guard let panel else { return }
        self.panel = nil
        panel.delegate = nil
        panel.contentView = nil
        panel.orderOut(nil)
    }

    /// Switches between the bar and the table. The panel grows away from the nearest screen edge
    /// and keeps its top-left corner otherwise, so the numbers stay where the eye left them.
    func setExpanded(_ expanded: Bool) {
        guard let panel, self.expanded != expanded else { return }
        self.expanded = expanded
        let size = expanded ? Self.tableSize : Self.barSize
        let old = panel.frame
        guard let screen = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame else { return }
        let nearRight = screen.maxX - old.maxX < Self.edgeDistance
        let nearBottom = old.minY - screen.minY < Self.edgeDistance
        let x = nearRight ? old.maxX - size.width : old.minX
        let y = nearBottom ? old.minY : old.maxY - size.height
        panel.setFrame(NSRect(x: x, y: y, width: size.width, height: size.height), display: true, animate: true)
    }

    /// Glass keeps the window out of screenshots and screen sharing, as the Windows version does;
    /// the other levels can be captured as usual.
    func applyLook(_ look: FloatingLook) {
        self.look = look
        panel?.sharingType = look == .glass ? .none : .readOnly
        panel?.alphaValue = look.awayAlpha
    }

    /// Low and High make the window solid again while the pointer is on it.
    func pointerOver(_ over: Bool) {
        panel?.alphaValue = over ? 1 : look.awayAlpha
    }

    /// Where the panel's top-left corner goes: where it was left if that is still on a screen,
    /// otherwise the top-right corner of the main screen.
    private func placed(_ size: NSSize) -> NSRect {
        let screens = NSScreen.screens.map { $0.visibleFrame.insetBy(dx: 40, dy: 40) }
        let stored = UserDefaults.standard.string(forKey: Self.positionKey).map(NSPointFromString)
        let top: NSPoint
        if let stored, screens.contains(where: { $0.contains(stored) }) {
            top = stored
        } else {
            let visible = NSScreen.main?.visibleFrame ?? .zero
            top = NSPoint(x: visible.maxX - size.width - Self.margin, y: visible.maxY - Self.margin)
        }
        return NSRect(x: top.x, y: top.y - size.height, width: size.width, height: size.height)
    }

    func windowDidMove(_ notification: Notification) {
        guard let panel else { return }
        UserDefaults.standard.set(NSStringFromPoint(NSPoint(x: panel.frame.minX, y: panel.frame.maxY)),
                                  forKey: Self.positionKey)
    }
}
#endif
