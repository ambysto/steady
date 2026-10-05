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

        enum CodingKeys: String, CodingKey {
            case hour = "rows_1h", recent = "rows_5m"
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

    @Test func vectorsAreLoaded() {
        #expect(Self.pingCases.count >= 10)
        #expect(Self.dnsCases.count >= 10)
        #expect(Self.bufferbloatCases.count >= 10)
    }

    @Test(arguments: bufferbloatCases)
    func bufferbloat(_ vector: VectorFile<Bufferbloat.Measurement>.Case) {
        check(Bufferbloat.evaluate(vector.input), vector.expected)
    }

    @Test(arguments: pingCases)
    func ping(_ vector: VectorFile<PingInput>.Case) {
        check(PingQuality.evaluate(hour: vector.input.hour, recent: vector.input.recent), vector.expected)
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
