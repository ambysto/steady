import Foundation
import Testing
@testable import SteadyKit

/// spec/diagnosis/*.json: the cases every platform's rules must agree on (ADR-0010).
struct DiagnosisVectorTests {
    struct VectorFile<Input: Decodable & Sendable>: Decodable, Sendable {
        var rule: String
        var cases: [Case]

        struct Case: Decodable, Sendable, CustomTestStringConvertible {
            var name: String
            var input: Input
            var expected: Expected
            var testDescription: String { name }
        }
    }

    struct Expected: Decodable, Sendable {
        var status: CheckStatus
        var summary: Message
        var details: [MessageValue]
        var advice: MessageValue
    }

    struct PingInput: Decodable, Sendable {
        var hour: [PingQuality.Row]
        var recent: [PingQuality.Row]
        var changes: [Int]?

        enum CodingKeys: String, CodingKey {
            case hour = "rows_1h", recent = "rows_5m", changes
        }
    }

    struct DNSInput: Decodable, Sendable {
        var bench: [DNSBenchmark.Server]
        var inUse: [String]
        var roles: [String: DNSBenchmark.Role]

        enum CodingKeys: String, CodingKey {
            case bench, inUse = "in_use", roles = "labels"
        }
    }

    static func cases<Input>(_ file: String, as: Input.Type) -> [VectorFile<Input>.Case] {
        let data = try! RepositoryData.data("spec/diagnosis/\(file)")
        return try! JSONDecoder().decode(VectorFile<Input>.self, from: data).cases
    }

    static let pingCases = cases("ping.json", as: PingInput.self)
    static let dnsCases = cases("dns.json", as: DNSInput.self)
    static let bufferbloatCases = cases("bufferbloat.json", as: Bufferbloat.Measurement.self)

    struct SignalInput: Decodable, Sendable {
        var wifi: WiFiSignal.State?
    }

    static let signalCases = cases("signal.json", as: SignalInput.self)

    struct VPNInput: Decodable, Sendable {
        var adapters: [VPNCheck.Adapter]
    }

    static let vpnCases = cases("vpn.json", as: VPNInput.self)

    struct InterferenceInput: Decodable, Sendable {
        var wifi: Interference.Connection?
        var scan: [Interference.Network]
    }

    static let interferenceCases = cases("interference.json", as: InterferenceInput.self)

    /// Runs of identical minutes, 60 s apart (the vector file says so).
    struct LinkInput: Decodable, Sendable {
        struct Run: Decodable, Sendable {
            var from: Int
            var count: Int
            var rssi: Int
            var rxMbps: Double
            var routerLossPct: Double

            enum CodingKeys: String, CodingKey {
                case from, count, rssi, rxMbps = "rx_mbps", routerLossPct = "router_loss_pct"
            }
        }

        var runs: [Run]
        var now: Int?

        var minutes: [PhysicalLink.Minute] {
            runs.flatMap { run in
                (0..<run.count).map {
                    PhysicalLink.Minute(ts: run.from + 60 * $0, rssi: run.rssi, rateMbps: run.rxMbps,
                                        routerLossPercent: run.routerLossPct)
                }
            }
        }
    }

    static let linkCases = cases("link.json", as: LinkInput.self)

    @Test func vectorsAreLoaded() {
        #expect(Self.pingCases.count >= 10)
        #expect(Self.dnsCases.count >= 10)
        #expect(Self.bufferbloatCases.count >= 10)
        #expect(Self.signalCases.count >= 8)
        #expect(Self.vpnCases.count >= 8)
        #expect(Self.interferenceCases.count >= 10)
        #expect(Self.linkCases.count >= 10)
    }

    @Test(arguments: interferenceCases)
    func interference(_ vector: VectorFile<InterferenceInput>.Case) {
        check(Interference.evaluate(vector.input.wifi, scan: vector.input.scan), vector.expected)
    }

    @Test(arguments: linkCases)
    func link(_ vector: VectorFile<LinkInput>.Case) {
        check(PhysicalLink.evaluate(vector.input.minutes, now: vector.input.now), vector.expected)
    }

    @Test(arguments: vpnCases)
    func vpn(_ vector: VectorFile<VPNInput>.Case) {
        check(VPNCheck.evaluate(vector.input.adapters), vector.expected)
    }

    @Test(arguments: signalCases)
    func signal(_ vector: VectorFile<SignalInput>.Case) {
        check(WiFiSignal.evaluate(vector.input.wifi), vector.expected)
    }

    @Test(arguments: bufferbloatCases)
    func bufferbloat(_ vector: VectorFile<Bufferbloat.Measurement>.Case) {
        check(Bufferbloat.evaluate(vector.input), vector.expected)
    }

    @Test(arguments: pingCases)
    func ping(_ vector: VectorFile<PingInput>.Case) {
        check(PingQuality.evaluate(hour: vector.input.hour, recent: vector.input.recent,
                                  changes: vector.input.changes ?? []), vector.expected)
    }

    @Test(arguments: dnsCases)
    func dns(_ vector: VectorFile<DNSInput>.Case) {
        check(DNSBenchmark.evaluate(vector.input.bench, inUse: vector.input.inUse, roles: vector.input.roles), vector.expected)
    }

    private func check(_ result: CheckResult, _ expected: Expected) {
        #expect(result.status == expected.status)
        #expect(approximatelyEqual(result.summary, expected.summary), "\(result.summary) != \(expected.summary)")
        #expect(approximatelyEqual(result.advice, expected.advice), "\(result.advice) != \(expected.advice)")
        #expect(result.details.count == expected.details.count)
        for (actual, wanted) in zip(result.details, expected.details) {
            #expect(approximatelyEqual(actual, wanted), "\(actual) != \(wanted)")
        }
    }
}
