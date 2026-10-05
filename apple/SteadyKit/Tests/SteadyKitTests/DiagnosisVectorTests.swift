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

    static let pingCases: [VectorFile<PingInput>.Case] = {
        let data = try! RepositoryData.data("spec/diagnosis/ping.json")
        return try! JSONDecoder().decode(VectorFile<PingInput>.self, from: data).cases
    }()

    @Test func pingVectorsAreLoaded() {
        #expect(Self.pingCases.count >= 10)
    }

    @Test(arguments: pingCases)
    func ping(_ vector: VectorFile<PingInput>.Case) {
        let result = PingQuality.evaluate(hour: vector.input.hour, recent: vector.input.recent)
        #expect(result.status == vector.expected.status)
        #expect(result.summary == vector.expected.summary)
        #expect(approximatelyEqual(result.advice, vector.expected.advice))
        #expect(result.details.count == vector.expected.details.count)
        for (actual, expected) in zip(result.details, vector.expected.details) {
            #expect(approximatelyEqual(actual, expected), "\(actual) != \(expected)")
        }
    }
}
