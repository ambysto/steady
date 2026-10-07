import Foundation
import Testing
@testable import SteadyKit

/// The load side of check #14 (SIC-95): the byte ceiling and the server's refusal, as
/// tests/test_bufferbloat.py checks them for app/bufferbloat.py.
struct BufferbloatLoadTests {
    let rtts = [Bufferbloat.Samples(label: "internet", samples: [20, 21, 22])]

    @Test func retryAfterIsReadInSeconds() {
        #expect(BufferbloatTest.retryAfter("120") == 120)
        #expect(BufferbloatTest.retryAfter(" 7.5 ") == 7.5)
        #expect(BufferbloatTest.retryAfter("-3") == 0)
        #expect(BufferbloatTest.retryAfter(nil) == nil)
        #expect(BufferbloatTest.retryAfter("") == nil)
        #expect(BufferbloatTest.retryAfter("Wed, 21 Oct 2026 07:28:00 GMT") == nil)
    }

    @Test func aPhaseThatReachesTheLimitIsCapped() {
        let full = BufferbloatTest.phase(rtts: rtts, outcome: .init(bytes: 1_000_000_000, error: nil), elapsed: 10,
                                         maxBytes: BufferbloatTest.maxBytes)
        #expect(full.capped == true)
        #expect(full.mbps == 800)
        #expect(full.refused == nil)
        let below = BufferbloatTest.phase(rtts: rtts, outcome: .init(bytes: 286_000_000, error: nil), elapsed: 10,
                                          maxBytes: BufferbloatTest.maxBytes)
        #expect(below.capped == false)
    }

    @Test func aRefusalAfterSomeDataStillMarksThePhaseRefused() {
        let outcome = LoadGenerator.Outcome(bytes: 40_000_000, error: nil,
                                            refusal: .init(status: 429, retryAfterS: 90))
        let phase = BufferbloatTest.phase(rtts: rtts, outcome: outcome, elapsed: 10, maxBytes: BufferbloatTest.maxBytes)
        #expect(phase.refused == 429)
        #expect(phase.retryAfterS == 90)
        #expect(phase.error == "HTTP 429")
        #expect(phase.capped == false)
    }

    @Test func theRampIsDroppedByTimeNotByRounds() {
        let samples = [Bufferbloat.Samples(label: "internet", samples: [900, 900, 850, 800, 790])]
        // A bloated line: each round waits for slow pings, so only the first two began within 2 s.
        let kept = BufferbloatTest.droppingRamp(samples, starts: [0, 0.95, 2.1, 3.0, 3.9])
        #expect(kept == [Bufferbloat.Samples(label: "internet", samples: [850, 800, 790])])
        #expect(BufferbloatTest.droppingRamp(samples, starts: [0, 0.2, 0.4, 0.6, 0.8])[0].samples.isEmpty)
    }

    @Test func aMeteredPathGetsALowerLimitAndLowDataModeNone() {
        #expect(BufferbloatTest.byteLimit(expensive: false, constrained: false) == 1_000_000_000)
        #expect(BufferbloatTest.byteLimit(expensive: true, constrained: false) == 250_000_000)
        #expect(BufferbloatTest.byteLimit(expensive: true, constrained: true) == nil)
        #expect(BufferbloatTest.byteLimit(expensive: false, constrained: true) == nil)
    }

    @Test func theCeilingMatchesPython() {
        #expect(BufferbloatTest.maxBytes == 1_000_000_000)   // MAX_BYTES in app/bufferbloat.py
    }

    @Test func aServer429StopsTheLoadAndKeepsRetryAfter() async {
        let load = LoadGenerator(upload: false, downloadURL: URL(string: "https://refused.test/__down")!,
                                 configuration: StubServer.configuration)
        let outcome = await load.run(seconds: 5)
        #expect(outcome.refusal == .init(status: 429, retryAfterS: 90))
    }

    @Test func a403WithoutRetryAfterIsARefusalToo() async {
        let load = LoadGenerator(upload: false, downloadURL: URL(string: "https://forbidden.test/__down")!,
                                 configuration: StubServer.configuration)
        let outcome = await load.run(seconds: 5)
        #expect(outcome.refusal == .init(status: 403, retryAfterS: nil))
    }

    @Test func otherHTTPErrorsAreOrdinaryErrors() async {
        let load = LoadGenerator(upload: false, downloadURL: URL(string: "https://broken.test/__down")!,
                                 configuration: StubServer.configuration)
        let outcome = await load.run(seconds: 5)
        #expect(outcome.refusal == nil)
        #expect(outcome.error == "HTTP 500")
    }

    @Test func theLoadStopsAtItsByteLimit() async {
        let clock = ContinuousClock()
        let started = clock.now
        let load = LoadGenerator(upload: false, maxBytes: 300_000, downloadURL: URL(string: "https://fast.test/__down")!,
                                 configuration: StubServer.configuration)
        let outcome = await load.run(seconds: 5)
        #expect(outcome.bytes >= 300_000)
        #expect(outcome.refusal == nil)
        #expect(clock.now - started < .seconds(4))   // stopped early, not at the deadline
        let phase = BufferbloatTest.phase(rtts: rtts, outcome: outcome, elapsed: 1, maxBytes: 300_000)
        #expect(phase.capped == true)
    }
}

/// Answers by host: refused.test → 429 with Retry-After, forbidden.test → 403, broken.test → 500,
/// anything else → 200 with 100 kB.
final class StubServer: URLProtocol {
    static var configuration: URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubServer.self]
        return configuration
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let url = request.url!
        let (status, headers): (Int, [String: String]) = switch url.host() {
        case "refused.test": (429, ["Retry-After": "90"])
        case "forbidden.test": (403, [:])
        case "broken.test": (500, [:])
        default: (200, [:])
        }
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        if status == 200 {
            client?.urlProtocol(self, didLoad: Data(count: 100_000))
        }
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
