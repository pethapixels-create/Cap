// cdax: what VoiceOver can see of one process (macOS Accessibility), and real key presses.
//   cdax trusted                 whether this process may use Accessibility
//   cdax tree <pid>              every element: depth role | title | description | value | flags
//                                (F focusable or pressable, * focused), then a count line
//   cdax focus <pid>             the focused element of that app
//   cdax key <tab|shift-tab|escape|return|space>   one real key press (CGEvent, HID tap)
//   cdax windows <pid>           the app's windows: title | x,y,w,h
import ApplicationServices
import AppKit
import Foundation

func attr(_ e: AXUIElement, _ name: String) -> AnyObject? {
    var value: AnyObject?
    guard AXUIElementCopyAttributeValue(e, name as CFString, &value) == .success else { return nil }
    return value
}
func str(_ e: AXUIElement, _ name: String) -> String {
    guard let v = attr(e, name) else { return "" }
    if let s = v as? String { return s.replacingOccurrences(of: "\n", with: " ") }
    if CFGetTypeID(v) == AXValueGetTypeID() { return "" }
    return "\(v)"
}
func children(_ e: AXUIElement) -> [AXUIElement] { (attr(e, kAXChildrenAttribute) as? [AXUIElement]) ?? [] }
func actions(_ e: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(e, &names) == .success, let list = names as? [String] else { return [] }
    return list
}
func frame(_ e: AXUIElement) -> String {
    guard let p = attr(e, kAXPositionAttribute), let s = attr(e, kAXSizeAttribute) else { return "" }
    var point = CGPoint.zero, size = CGSize.zero
    AXValueGetValue(p as! AXValue, .cgPoint, &point)
    AXValueGetValue(s as! AXValue, .cgSize, &size)
    return "\(Int(point.x)),\(Int(point.y)),\(Int(size.width)),\(Int(size.height))"
}
func describe(_ e: AXUIElement) -> String {
    var flags = ""
    if actions(e).contains(kAXPressAction as String) { flags += "F" }
    if (attr(e, kAXFocusedAttribute) as? Bool) == true { flags += "*" }
    let value = String(str(e, kAXValueAttribute).prefix(80))
    return "\(str(e, kAXRoleAttribute)) | \(String(str(e, kAXTitleAttribute).prefix(100))) | \(String(str(e, kAXDescriptionAttribute).prefix(100))) | \(value) | \(flags) | \(frame(e))"
}

let args = CommandLine.arguments
switch args.count > 1 ? args[1] : "" {
case "trusted":
    print(AXIsProcessTrusted())
case "tree":
    let app = AXUIElementCreateApplication(pid_t(args[2])!)
    var nodes = 0, named = 0, pressable = 0
    func walk(_ e: AXUIElement, _ depth: Int) {
        guard depth < 60 else { return }
        nodes += 1
        let d = describe(e)
        if !(str(e, kAXTitleAttribute) + str(e, kAXDescriptionAttribute) + str(e, kAXValueAttribute)).isEmpty { named += 1 }
        if actions(e).contains(kAXPressAction as String) { pressable += 1 }
        print(String(repeating: "  ", count: depth) + d)
        for c in children(e) { walk(c, depth + 1) }
    }
    walk(app, 0)
    print("# nodes=\(nodes) named=\(named) pressable=\(pressable)")
case "focus":
    let app = AXUIElementCreateApplication(pid_t(args[2])!)
    if let f = attr(app, kAXFocusedUIElementAttribute) { print(describe(f as! AXUIElement)) } else { print("(nothing focused)") }
case "key":
    let codes: [String: (CGKeyCode, CGEventFlags)] = [
        "tab": (48, []), "shift-tab": (48, .maskShift), "escape": (53, []), "return": (36, []), "space": (49, []),
    ]
    guard let (code, flags) = codes[args[2]] else { print("unknown key"); exit(2) }
    let src = CGEventSource(stateID: .hidSystemState)
    for down in [true, false] {
        let ev = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: down)!
        ev.flags = flags
        ev.post(tap: .cghidEventTap)
        usleep(30_000)
    }
case "front":
    // Activates the app, then prints the frontmost app's pid (callers press keys only when it matches).
    if let app = NSRunningApplication(processIdentifier: pid_t(args[2])!) {
        app.activate(options: [.activateAllWindows])
    }
    usleep(500_000)
    print(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1)
case "windows":
    let app = AXUIElementCreateApplication(pid_t(args[2])!)
    for w in (attr(app, kAXWindowsAttribute) as? [AXUIElement]) ?? [] { print("\(str(w, kAXTitleAttribute)) | \(frame(w))") }
default:
    print("usage: cdax trusted|tree|focus|key|windows")
}
