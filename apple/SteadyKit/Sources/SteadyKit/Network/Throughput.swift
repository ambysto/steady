import Darwin
import Foundation

/// How fast data moves over the interface the Internet traffic takes, sampled about once a second
/// (the floating monitor's download and upload). Read-only and sandbox-safe: the interface's own
/// byte counters from `getifaddrs`, no per-app figures (macOS does not show those to apps).
public struct Throughput: Sendable {
    public struct Sample: Equatable, Sendable {
        public let ts: Double
        public let downBps: Double
        public let upBps: Double
    }

    /// Seconds of rates kept: what the sweep chart shows.
    public static let historySeconds = 120.0
    /// Longer than this between two readings, no rate is worked out: the Mac was asleep or the
    /// app was not watching, and the bytes in between are not a rate.
    static let maxGapSeconds = 2.5
    /// Faster than this is a counter that went back (the interface changed), not traffic.
    static let maxBps = 1e10

    public private(set) var samples: [Sample] = []
    private var last: (ts: Double, interface: String, rx: UInt32, tx: UInt32)?

    public init() {}

    /// Takes one reading of `interface`'s counters at time `ts`.
    public mutating func add(ts: Double, interface: String, rx: UInt32, tx: UInt32) {
        defer { last = (ts, interface, rx, tx) }
        guard let previous = last, previous.interface == interface else {
            samples.removeAll()   // a new interface: its numbers start afresh
            return
        }
        let elapsed = ts - previous.ts
        if elapsed > 0, elapsed <= Self.maxGapSeconds {
            // 32-bit counters wrap; the subtraction wraps the same way.
            let down = Double(rx &- previous.rx) * 8 / elapsed
            let up = Double(tx &- previous.tx) * 8 / elapsed
            if down <= Self.maxBps, up <= Self.maxBps {
                samples.append(Sample(ts: ts, downBps: down, upBps: up))
            }
        }
        samples.removeAll { $0.ts < ts - Self.historySeconds }
    }

    /// The byte counters of an interface by name, or nil when it is not there.
    public static func counters(of interface: String) -> (rx: UInt32, tx: UInt32)? {
        var list: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&list) == 0, let first = list else { return nil }
        defer { freeifaddrs(list) }
        var cursor: UnsafeMutablePointer<ifaddrs>? = first
        while let entry = cursor {
            defer { cursor = entry.pointee.ifa_next }
            guard let address = entry.pointee.ifa_addr, address.pointee.sa_family == UInt8(AF_LINK),
                  let data = entry.pointee.ifa_data, String(cString: entry.pointee.ifa_name) == interface else { continue }
            let stats = data.assumingMemoryBound(to: if_data.self).pointee
            return (stats.ifi_ibytes, stats.ifi_obytes)
        }
        return nil
    }

    /// The first IPv4 address of an interface, as text, or nil.
    public static func ipv4(of interface: String) -> String? {
        var list: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&list) == 0, let first = list else { return nil }
        defer { freeifaddrs(list) }
        var cursor: UnsafeMutablePointer<ifaddrs>? = first
        while let entry = cursor {
            defer { cursor = entry.pointee.ifa_next }
            guard let address = entry.pointee.ifa_addr, address.pointee.sa_family == UInt8(AF_INET),
                  String(cString: entry.pointee.ifa_name) == interface else { continue }
            var host = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
            let status = getnameinfo(address, socklen_t(address.pointee.sa_len), &host, socklen_t(host.count),
                                     nil, 0, NI_NUMERICHOST)
            if status == 0 { return String(cString: host) }
        }
        return nil
    }
}
