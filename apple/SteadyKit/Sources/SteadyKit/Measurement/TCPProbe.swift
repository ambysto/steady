import Foundation
import Network
import Synchronization

/// Time to complete a TCP handshake, the probe that does not depend on ICMP (app/probe.py
/// tcp_connect): networks often deprioritise ping while real traffic works.
public enum TCPProbe {
    /// Milliseconds until the connection is ready, or nil on failure or after `timeout`.
    public static func connect(host: String, port: UInt16, timeout: Duration = .seconds(3)) async -> Double? {
        guard let endpointPort = NWEndpoint.Port(rawValue: port) else { return nil }
        let parameters = NWParameters.tcp
        parameters.preferNoProxies = true
        let connection = NWConnection(host: NWEndpoint.Host(host), port: endpointPort, using: parameters)
        let queue = DispatchQueue(label: "com.ambysto.steady.tcp-probe")
        let done = Mutex(false)
        let start = ContinuousClock.now

        return await withCheckedContinuation { continuation in
            @Sendable func finish(_ result: Double?) {
                guard done.withLock({ wasDone in defer { wasDone = true }; return !wasDone }) else { return }
                connection.cancel()
                continuation.resume(returning: result)
            }
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    finish((ContinuousClock.now - start).inMilliseconds)
                case .failed, .waiting, .cancelled:   // "waiting" = no route right now: a failed probe
                    finish(nil)
                default:
                    break
                }
            }
            connection.start(queue: queue)
            queue.asyncAfter(deadline: .now() + timeout.inMilliseconds / 1000) {
                finish(nil)
            }
        }
    }
}
