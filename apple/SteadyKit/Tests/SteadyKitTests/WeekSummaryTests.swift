import Testing
@testable import SteadyKit

/// The "Last 7 days" card: only what the stored minutes say.
struct WeekSummaryTests {
    static let now = 1_790_000_000.0

    /// One minute `ago` minutes before now: router and the two pings answer in `ms`, or nothing
    /// past the router answers when `offline`.
    static func minute(_ ago: Int, router: Double = 4, internet: Double = 20, lost: Int = 0,
                       offline: Bool = false) -> MinuteAggregator.Minute {
        let sent = 30
        func ping(_ target: String, _ ms: Double, _ lost: Int) -> PingQuality.Row {
            PingQuality.Row(target: target, sent: sent, lost: lost, avg: lost == sent ? nil : ms)
        }
        var rows = [ping("router", router, 0)]
        rows += offline ? [ping("cloudflare", internet, sent), ping("google", internet, sent),
                           PingQuality.Row(target: "tcp_cloudflare", sent: 6, lost: 6)]
                        : [ping("cloudflare", internet, lost), ping("google", internet + 2, 0),
                           PingQuality.Row(target: "tcp_cloudflare", sent: 6, lost: 0, avg: internet + 5)]
        return MinuteAggregator.Minute(start: Int(now) - 60 * ago, rows: rows)
    }

    @Test func mediansLossAndTimeMeasured() {
        let minutes = (1...90).map { Self.minute($0, router: $0 <= 45 ? 3 : 5, internet: 20, lost: $0 == 10 ? 3 : 0) }
        let week = WeekSummary(minutes, now: Self.now)
        #expect(week.measuredMinutes == 90)
        #expect(week.hasEnoughData)
        #expect(week.routerMedianMs == 4)          // 45 minutes at 3 ms, 45 at 5 ms
        #expect(week.internetMedianMs == 21)       // 1.1.1.1 at 20 ms and 8.8.8.8 at 22 ms, equal replies
        #expect(abs((week.internetLossPercent ?? 0) - 100 * 3.0 / (90 * 60)) < 1e-9)   // pings only
        #expect(week.outages == 0 && week.outageMinutes == 0)
    }

    @Test func outagesAreRunsOfMinutesWithNoInternetAtAll() {
        var minutes = (1...120).map { Self.minute($0) }
        // Minutes 20–22 ago without Internet: one outage of 3 minutes; minute 50: another.
        minutes = minutes.map { [20, 21, 22, 50].contains((Int(Self.now) - $0.start) / 60) ? Self.minute((Int(Self.now) - $0.start) / 60, offline: true) : $0 }
        let week = WeekSummary(minutes, now: Self.now)
        #expect(week.outages == 2)
        #expect(week.outageMinutes == 4)
    }

    @Test func aGapEndsAnOutage() {
        // Offline before the app was closed and again after it reopened: two outages, not one.
        let minutes = [Self.minute(10, offline: true), Self.minute(3, offline: true)]
        let week = WeekSummary(minutes, now: Self.now)
        #expect(week.outages == 2)
    }

    @Test func pingLossAloneIsNotAnOutage() {
        // ICMP lost but the TCP probe answered: rate-limited pings, the Internet was there.
        let minute = MinuteAggregator.Minute(start: Int(Self.now) - 60, rows: [
            PingQuality.Row(target: "router", sent: 30, lost: 0, avg: 3),
            PingQuality.Row(target: "cloudflare", sent: 30, lost: 30),
            PingQuality.Row(target: "tcp_cloudflare", sent: 6, lost: 0, avg: 25)])
        #expect(WeekSummary([minute], now: Self.now).outages == 0)
    }

    @Test func olderThanAWeekAndEmptyMinutesAreLeftOut() {
        let minutes = [Self.minute(7 * 24 * 60 + 1), MinuteAggregator.Minute(start: Int(Self.now) - 120, rows: []), Self.minute(1)]
        let week = WeekSummary(minutes, now: Self.now)
        #expect(week.measuredMinutes == 1)
        #expect(!week.hasEnoughData)
    }

    @Test func nothingMeasuredGivesNoNumbers() {
        let week = WeekSummary([], now: Self.now)
        #expect(week.measuredMinutes == 0)
        #expect(week.routerMedianMs == nil && week.internetMedianMs == nil && week.internetLossPercent == nil)
    }
}
