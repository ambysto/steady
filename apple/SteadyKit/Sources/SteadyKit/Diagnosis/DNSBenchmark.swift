import Darwin

/// Check #6 "DNS benchmark" (docs/DIAGNOSTICS.md), a port of `evaluate_dns` in app/diagnostics.py.
/// spec/diagnosis/dns.json holds the cases both implementations must agree on.
public enum DNSBenchmark {
    /// What one server did over a few queries (app/dnsprobe.py ServerBenchmark).
    public struct Server: Equatable, Sendable, Decodable {
        public var server: String
        public var sent: Int
        /// Valid replies, including error codes.
        public var replies: Int
        /// Timeouts, socket errors, SERVFAIL/REFUSED...
        public var failures: Int
        /// Round-trip times of the answers that count (NOERROR and NXDOMAIN).
        public var rtts: [Double]

        public init(server: String, sent: Int = 0, replies: Int = 0, failures: Int = 0, rtts: [Double] = []) {
            self.server = server
            self.sent = sent
            self.replies = replies
            self.failures = failures
            self.rtts = rtts
        }

        enum CodingKeys: String, CodingKey {
            case server, sent, replies, failures, rtts = "rtts_ms"
        }

        /// Python's round(statistics.median(rtts), 1).
        public var median: Double? {
            guard !rtts.isEmpty else { return nil }
            let sorted = rtts.sorted()
            let middle = sorted.count / 2
            let value = sorted.count.isMultiple(of: 2) ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle]
            return MinuteAggregator.roundToTenth(value)
        }
    }

    /// Why a server is in the benchmark, as `labels` values in Python.
    public enum Role: String, Sendable, Decodable {
        case inUse = "in_use"
        case inUseRouter = "in_use_router"
        case router
        case `public`
    }

    public static let publicServers = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]

    /// - Parameters:
    ///   - bench: results in the order measured: servers in use first, then router and public ones.
    ///   - inUse: the system's DNS servers, primary first.
    ///   - roles: why each server was measured.
    public static func evaluate(_ bench: [Server], inUse: [String], roles: [String: Role]) -> CheckResult {
        guard !bench.isEmpty else {
            return CheckResult(id: 6, key: "dns", status: .info, summary: Message("diag.dns.no_servers"))
        }
        let details: [MessageValue] = bench.map { server in
            let median: MessageValue = server.median.map { .message(Message("diag.common.ms", ["value": .number($0)])) }
                ?? .message(Message("diag.dns.no_reply"))
            return .message(Message("diag.dns.line", [
                "label": label(server.server, roles), "server": .text(server.server), "median": median,
                "failures": .number(Double(server.failures)), "sent": .number(Double(server.sent)),
            ]))
        }
        let used = bench.filter { inUse.contains($0.server) }
        guard let primary = used.first else {
            return CheckResult(id: 6, key: "dns", status: .info, summary: Message("diag.dns.unknown_in_use"), details: details)
        }
        let broken = used.filter { $0.failures > 0 }
        if !broken.isEmpty {
            return CheckResult(id: 6, key: "dns", status: .warn,
                               summary: Message("diag.dns.broken", ["servers": .list(broken.map { .text($0.server) })]),
                               details: details, advice: .message(Message("diag.dns.advice_broken")))
        }
        // Python's min() keeps the first of equal medians; so does this.
        let fastest = bench.filter { $0.median != nil && $0.failures == 0 }
            .reduce(nil as Server?) { best, server in
                guard let best, let bestMedian = best.median else { return server }
                return server.median! < bestMedian ? server : best
            }
        if let fastest, let fastestMedian = fastest.median, let primaryMedian = primary.median,
           primaryMedian - fastestMedian > 20 {
            let advice: Message = if inUse.contains(fastest.server) {
                Message("diag.dns.advice_promote", ["server": .text(fastest.server)])
            } else if roles[fastest.server] == .router {
                Message("diag.dns.advice_router_cache")
            } else {
                Message("diag.dns.advice_consider", ["server": .text(fastest.server)])
            }
            return CheckResult(id: 6, key: "dns", status: .info,
                               summary: Message("diag.dns.slower", ["server": .text(fastest.server),
                                                                    "gap": .number(primaryMedian - fastestMedian)]),
                               details: details, advice: .message(advice))
        }
        return CheckResult(id: 6, key: "dns", status: .ok, summary: Message("diag.dns.ok"), details: details)
    }

    /// The servers to measure and why: the ones in use, then the router and the public resolvers
    /// (app/diagnostics.py Context._load_dns_bench).
    public static func plan(inUse: [String], router: String?) -> (servers: [String], roles: [String: Role]) {
        var servers: [String] = []
        var roles: [String: Role] = [:]
        for server in inUse where isUsable(server) && !servers.contains(server) {
            servers.append(server)
            roles[server] = .inUse
        }
        for (extra, role) in [(router, Role.router)] + publicServers.map({ ($0, Role.public) }) {
            if let extra, !servers.contains(extra), isUsable(extra) {
                servers.append(extra)
                roles[extra] = role
            }
        }
        if let router, roles[router] == .inUse {
            roles[router] = .inUseRouter
        }
        return (servers, roles)
    }

    /// False for unparseable, scoped or IPv6 link-local addresses (they need an interface), as
    /// dnsprobe.is_usable_server.
    public static func isUsable(_ address: String) -> Bool {
        var v4 = in_addr()
        if inet_pton(AF_INET, address, &v4) == 1 { return true }
        var v6 = in6_addr()
        guard !address.contains("%"), inet_pton(AF_INET6, address, &v6) == 1 else { return false }
        let first = v6.__u6_addr.__u6_addr8.0, second = v6.__u6_addr.__u6_addr8.1
        return !(first == 0xFE && second & 0xC0 == 0x80)   // fe80::/10
    }

    private static func label(_ server: String, _ roles: [String: Role]) -> MessageValue {
        guard let role = roles[server] else { return .text(server) }
        return .message(Message("diag.dns.role.\(role.rawValue)"))
    }
}
