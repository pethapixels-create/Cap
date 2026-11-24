// winwait <pid> <timeout s>: prints the epoch time (s) when <pid> first has an on-screen window
// larger than 50x30 points, or -1 on timeout. Polls CGWindowListCopyWindowInfo every 4 ms.
import CoreGraphics
import Foundation

let pid = Int32(CommandLine.arguments[1])!
let timeout = Double(CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "60")!
let start = Date()
while Date().timeIntervalSince(start) < timeout {
    if let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]] {
        for w in list {
            guard (w[kCGWindowOwnerPID as String] as? Int32) == pid else { continue }
            let alpha = (w[kCGWindowAlpha as String] as? Double) ?? 1
            if let b = w[kCGWindowBounds as String] as? [String: Double],
               (b["Width"] ?? 0) > 50, (b["Height"] ?? 0) > 30, alpha > 0 {
                print(String(format: "%.4f", Date().timeIntervalSince1970))
                exit(0)
            }
        }
    }
    usleep(4000)
}
print("-1")
exit(1)
