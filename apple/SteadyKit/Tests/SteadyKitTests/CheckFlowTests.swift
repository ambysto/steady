import Testing
@testable import SteadyKit

/// What the Overview's "Check my connection" counts and says (ADR-0020).
struct CheckFlowTests {
    static func result(_ id: Int, _ key: String, _ status: CheckStatus, _ summary: String = "s") -> CheckResult {
        CheckResult(id: id, key: key, status: status, summary: Message(summary))
    }

    @Test func onlyWarnAndBadCountWorstFirst() {
        let results = [Self.result(2, "signal", .warn), Self.result(3, "interference", .ok),
                       Self.result(5, "ping", .bad), Self.result(6, "dns", .warn), Self.result(8, "vpn", .info),
                       Self.result(13, "physical_link", .info)]
        #expect(CheckFlow.problems(results).map(\.result.id) == [5, 2, 6])
        #expect(CheckFlow.okCount(results) == 1)
    }

    @Test func nothingToFixWhenEveryResultIsOkOrInfo() {
        let results = [Self.result(5, "ping", .info, "diag.ping.no_data"), Self.result(6, "dns", .ok), Self.result(8, "vpn", .ok)]
        #expect(CheckFlow.problems(results).isEmpty)
        #expect(CheckFlow.okCount(results) == 2)   // the headline then says "2 of 3 are fine", not "all"
    }

    @Test func remedyFollowsTheWindowsManualSteps() {
        #expect(CheckFlow.remedy(for: Self.result(2, "signal", .warn)) == .you)
        #expect(CheckFlow.remedy(for: Self.result(2, "signal", .bad)) == .you)
        #expect(CheckFlow.remedy(for: Self.result(3, "interference", .warn)) == .you)
        #expect(CheckFlow.remedy(for: Self.result(6, "dns", .warn)) == .you)
        #expect(CheckFlow.remedy(for: Self.result(13, "physical_link", .warn)) == .you)
        // No step on this device helps: loss beyond the router, for instance.
        #expect(CheckFlow.remedy(for: Self.result(5, "ping", .bad, "diag.ping.wan_loss")) == .none)
        #expect(CheckFlow.remedy(for: Self.result(5, "ping", .warn)) == .none)
    }

    @Test func aResultGoesStaleAfterThreeHours() {
        #expect(!CheckFlow.isStale(checkedAt: 1000, now: 1000 + 3 * 3600))
        #expect(CheckFlow.isStale(checkedAt: 1000, now: 1001 + 3 * 3600))
    }

    @Test func medianOfTheRepliesOnly() {
        #expect(LiveStats(samples: [5, nil, 1, 3]).medianRTT == 3)
        #expect(LiveStats(samples: [4, 2, nil, 8, 6]).medianRTT == 5)
        #expect(LiveStats(samples: [nil, nil]).medianRTT == nil)
        #expect(LiveStats(samples: []).medianRTT == nil)
    }
}

/// Message.duration follows format_duration in app/i18n.py.
struct DurationTests {
    static func count(_ key: String, _ value: Double) -> MessageValue { .message(Message(key, ["count": .number(value)])) }

    @Test func wordsTheLargestUnitsLikePython() {
        #expect(Message.duration(seconds: 3 * 3600) == Message("time.hours", ["count": 3]))
        #expect(Message.duration(seconds: 3 * 3600 + 25 * 60 + 40)
                == Message("time.hours_minutes", ["hours": Self.count("time.hours", 3), "minutes": Self.count("time.minutes", 25)]))
        #expect(Message.duration(seconds: 125)
                == Message("time.minutes_seconds", ["minutes": Self.count("time.minutes", 2), "seconds": Self.count("time.seconds", 5)]))
        #expect(Message.duration(seconds: 120) == Message("time.minutes", ["count": 2]))
        #expect(Message.duration(seconds: 42.9) == Message("time.seconds", ["count": 42]))
        #expect(Message.duration(seconds: -5) == Message("time.seconds", ["count": 0]))
    }
}
