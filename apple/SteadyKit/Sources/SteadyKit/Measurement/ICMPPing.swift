import Darwin
import Foundation
import Synchronization

/// ICMP echo over an unprivileged datagram socket, as in Apple's SimplePing sample: no root, no
/// special entitlement. IPv4 only, like the Windows monitor (app/icmp.py).
///
/// The socket is connected to the target first: the macOS App Sandbox lets a client-only app
/// (network.client) read replies on a connected socket but refuses `recvfrom` on an unconnected
/// one with EPERM, and a connected socket also only receives that target's packets.
public enum ICMPPing {
    public enum Outcome: Equatable, Sendable {
        /// Round-trip time in milliseconds.
        case reply(Double)
        case noReply
        /// The system would not let the packet out: on iOS and macOS 15+ that is what a declined
        /// local network permission looks like for an address on the LAN.
        case refused

        public var rttMs: Double? {
            if case .reply(let rtt) = self { rtt } else { nil }
        }
    }

    /// Round-trip time in milliseconds, or nil when no reply came within `timeout`.
    public static func ping(_ address: String, timeout: Duration = .milliseconds(900)) async -> Double? {
        await outcome(address, timeout: timeout).rttMs
    }

    public static func outcome(_ address: String, timeout: Duration = .milliseconds(900)) async -> Outcome {
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .utility).async {
                continuation.resume(returning: pingBlocking(address, timeout: timeout))
            }
        }
    }

    /// errno values meaning "not allowed to send there" rather than "no answer".
    static func isRefusal(_ code: Int32) -> Bool {
        [EHOSTUNREACH, EPERM, EACCES].contains(code)
    }

    private static let identifier = UInt16.random(in: 1...UInt16.max)
    private static let sequence = Mutex<UInt16>(0)
    private static let payload = Array("Ambysto Steady ping 0123456789ab".utf8)   // 32 bytes, as on Windows

    static func pingBlocking(_ address: String, timeout: Duration) -> Outcome {
        var destination = sockaddr_in()
        destination.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        destination.sin_family = sa_family_t(AF_INET)
        guard inet_pton(AF_INET, address, &destination.sin_addr) == 1 else { return .noReply }

        let socketHandle = socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)
        guard socketHandle >= 0 else { return .noReply }
        defer { close(socketHandle) }

        let number = sequence.withLock { value in
            value &+= 1
            return value
        }
        let request = echoRequest(identifier: identifier, sequence: number)
        let clock = ContinuousClock()
        let start = clock.now
        let connected = withUnsafePointer(to: &destination) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(socketHandle, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard connected == 0, send(socketHandle, request, request.count, 0) == request.count else {
            return isRefusal(errno) ? .refused : .noReply
        }

        var buffer = [UInt8](repeating: 0, count: 2048)
        while true {
            let remaining = timeout - (clock.now - start)
            guard remaining > .zero else { return .noReply }
            var descriptor = pollfd(fd: socketHandle, events: Int16(POLLIN), revents: 0)
            guard poll(&descriptor, 1, max(1, Int32(remaining.inMilliseconds.rounded(.up)))) > 0 else { return .noReply }

            let received = recv(socketHandle, &buffer, buffer.count, 0)
            guard received > 0 else { return isRefusal(errno) ? .refused : .noReply }
            guard isEchoReply(buffer[..<received], sequence: number) else {
                continue   // an older reply or an ICMP error: keep waiting
            }
            return .reply((clock.now - start).inMilliseconds)
        }
    }

    static func echoRequest(identifier: UInt16, sequence: UInt16) -> [UInt8] {
        var packet: [UInt8] = [8, 0, 0, 0,   // type 8 = echo request, code, checksum
                               UInt8(identifier >> 8), UInt8(identifier & 0xFF),
                               UInt8(sequence >> 8), UInt8(sequence & 0xFF)] + payload
        let sum = checksum(packet)
        packet[2] = UInt8(sum >> 8)
        packet[3] = UInt8(sum & 0xFF)
        return packet
    }

    /// Darwin delivers datagram ICMP with the IPv4 header in front; skip it when present. The
    /// kernel may rewrite the identifier, so the reply is matched on the sequence number.
    static func isEchoReply(_ data: ArraySlice<UInt8>, sequence: UInt16) -> Bool {
        var bytes = Array(data)
        if bytes.count >= 20, bytes[0] >> 4 == 4 {
            let header = Int(bytes[0] & 0x0F) * 4
            guard header >= 20, header <= bytes.count else { return false }
            bytes.removeFirst(header)
        }
        guard bytes.count >= 8, bytes[0] == 0 else { return false }   // type 0 = echo reply
        return UInt16(bytes[6]) << 8 | UInt16(bytes[7]) == sequence
    }

    /// RFC 1071 Internet checksum.
    static func checksum(_ bytes: [UInt8]) -> UInt16 {
        var sum: UInt32 = 0
        var index = 0
        while index + 1 < bytes.count {
            sum += UInt32(bytes[index]) << 8 | UInt32(bytes[index + 1])
            index += 2
        }
        if index < bytes.count {
            sum += UInt32(bytes[index]) << 8
        }
        while sum >> 16 != 0 {
            sum = (sum & 0xFFFF) + (sum >> 16)
        }
        return ~UInt16(sum)
    }
}
