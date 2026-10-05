import CResolver
import Darwin
import Foundation

/// DNS over raw UDP, a port of app/dnsprobe.py: the query is built by hand so the system
/// resolver's cache never answers, and every server is timed on its own.
public enum DNSProbe {
    public static let names = ["google.com", "cloudflare.com", "microsoft.com", "youtube.com", "facebook.com"]

    /// One answer: the round-trip time and the response code; nil when nothing valid came back.
    public struct Reply: Equatable, Sendable {
        public let rttMs: Double
        public let rcode: Int
    }

    /// The system's DNS servers, primary first (numeric addresses).
    public static func systemServers() -> [String] {
        let stride = Int(INET6_ADDRSTRLEN), maximum = 8
        var buffer = [CChar](repeating: 0, count: stride * maximum)
        let count = Int(steady_dns_servers(&buffer, Int32(stride), Int32(maximum)))
        guard count > 0 else { return [] }
        var servers: [String] = []
        for index in 0..<count {
            let slot = buffer[(index * stride)..<((index + 1) * stride)].prefix { $0 != 0 }
            let text = String(decoding: slot.map { UInt8(bitPattern: $0) }, as: UTF8.self)
            if !servers.contains(text) {
                servers.append(text)
            }
        }
        return servers
    }

    /// Queries every name on `server` once, in turn (dnsprobe.benchmark). NXDOMAIN counts as an
    /// answer for the timing; other error codes and timeouts count as failures.
    public static func benchmark(_ server: String, names: [String] = names,
                                 timeout: Duration = .milliseconds(1500)) async -> DNSBenchmark.Server {
        var result = DNSBenchmark.Server(server: server)
        for name in names {
            let reply = await query(server, name: name, timeout: timeout)
            result.sent += 1
            if let reply {
                result.replies += 1
                if reply.rcode == 0 || reply.rcode == 3 {
                    result.rtts.append(reply.rttMs)
                    continue
                }
            }
            result.failures += 1
        }
        return result
    }

    public static func query(_ server: String, name: String, timeout: Duration) async -> Reply? {
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .utility).async {
                continuation.resume(returning: queryBlocking(server, name: name, timeout: timeout))
            }
        }
    }

    static func queryBlocking(_ server: String, name: String, timeout: Duration) -> Reply? {
        let identifier = UInt16.random(in: 0...UInt16.max)
        guard DNSBenchmark.isUsable(server), let packet = buildQuery(name, identifier: identifier),
              let socketHandle = connectedUDPSocket(server, port: 53) else { return nil }
        defer { close(socketHandle) }

        let clock = ContinuousClock()
        let start = clock.now
        guard send(socketHandle, packet, packet.count, 0) == packet.count else { return nil }
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let remaining = timeout - (clock.now - start)
            guard remaining > .zero else { return nil }
            var descriptor = pollfd(fd: socketHandle, events: Int16(POLLIN), revents: 0)
            guard poll(&descriptor, 1, max(1, Int32(remaining.inMilliseconds.rounded(.up)))) > 0 else { return nil }
            let received = recv(socketHandle, &buffer, buffer.count, 0)
            guard received > 0 else { return nil }
            if let rcode = responseCode(buffer[..<received], identifier: identifier) {
                return Reply(rttMs: (clock.now - start).inMilliseconds, rcode: rcode)
            }
            // A stray or late packet: keep waiting until the deadline.
        }
    }

    /// A recursive A query for `name`, or nil for a name that cannot be encoded.
    static func buildQuery(_ name: String, identifier: UInt16) -> [UInt8]? {
        let labels = name.split(separator: ".").map { Array($0.utf8) }
        guard !labels.isEmpty, labels.allSatisfy({ (1...63).contains($0.count) }) else { return nil }
        var packet: [UInt8] = [UInt8(identifier >> 8), UInt8(identifier & 0xFF),
                               0x01, 0x00,   // flags: recursion desired
                               0x00, 0x01,   // one question
                               0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        for label in labels {
            packet.append(UInt8(label.count))
            packet += label
        }
        packet += [0x00, 0x00, 0x01, 0x00, 0x01]   // root, type A, class IN
        return packet
    }

    /// The response code when `data` is a reply to `identifier`; nil otherwise.
    static func responseCode(_ data: ArraySlice<UInt8>, identifier: UInt16) -> Int? {
        let bytes = Array(data)
        guard bytes.count >= 12, UInt16(bytes[0]) << 8 | UInt16(bytes[1]) == identifier,
              bytes[2] & 0x80 != 0 else { return nil }   // QR bit: a response
        return Int(bytes[3] & 0x0F)
    }

    /// Connected, so only that server's datagrams arrive and the macOS sandbox lets a
    /// client-only app read them.
    private static func connectedUDPSocket(_ address: String, port: UInt16) -> Int32? {
        var v4 = sockaddr_in()
        if inet_pton(AF_INET, address, &v4.sin_addr) == 1 {
            v4.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            v4.sin_family = sa_family_t(AF_INET)
            v4.sin_port = port.bigEndian
            return connectSocket(family: AF_INET, &v4)
        }
        var v6 = sockaddr_in6()
        if inet_pton(AF_INET6, address, &v6.sin6_addr) == 1 {
            v6.sin6_len = UInt8(MemoryLayout<sockaddr_in6>.size)
            v6.sin6_family = sa_family_t(AF_INET6)
            v6.sin6_port = port.bigEndian
            return connectSocket(family: AF_INET6, &v6)
        }
        return nil
    }

    private static func connectSocket<Address>(family: Int32, _ address: inout Address) -> Int32? {
        let handle = socket(family, SOCK_DGRAM, IPPROTO_UDP)
        guard handle >= 0 else { return nil }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(handle, $0, socklen_t(MemoryLayout<Address>.size))
            }
        }
        guard connected == 0 else {
            close(handle)
            return nil
        }
        return handle
    }
}

/// Runs check #6 end to end: which servers to measure, the measurements, the verdict.
public enum DNSCheck {
    public static func run(router: String?) async -> CheckResult {
        let inUse = DNSProbe.systemServers().filter(DNSBenchmark.isUsable)
        let plan = DNSBenchmark.plan(inUse: inUse, router: router)
        let bench = await withTaskGroup(of: DNSBenchmark.Server.self) { group in
            for server in plan.servers {
                group.addTask { await DNSProbe.benchmark(server) }
            }
            var results: [DNSBenchmark.Server] = []
            for await result in group {
                results.append(result)
            }
            return plan.servers.compactMap { server in results.first { $0.server == server } }
        }
        return DNSBenchmark.evaluate(bench, inUse: inUse, roles: plan.roles)
    }
}
