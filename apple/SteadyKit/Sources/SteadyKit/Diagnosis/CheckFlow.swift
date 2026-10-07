/// The Overview's "Check my connection" on Apple platforms (ADR-0020 point 11): which results
/// count as problems, what the user can do about each, and the order they are shown in.
///
/// The Apple app changes nothing on the device, so there is no "the app can fix this" and no
/// Fix button: a problem is either something the user can fix or something nothing on this
/// device fixes. Either way the check's own advice is shown.
public enum CheckFlow {
    /// Who can act on a problem; `ui.check.kind.<rawValue>` names it.
    public enum Remedy: String, Sendable {
        case you
        case none
    }

    public struct Problem: Equatable, Sendable {
        public let result: CheckResult
        public let remedy: Remedy
    }

    /// Only `warn` and `bad` count; `info` and `ok` never do. Worst first, then by check number.
    public static func problems(_ results: [CheckResult]) -> [Problem] {
        results.filter { $0.status >= .warn }
            .sorted { ($0.status, -$0.id) > ($1.status, -$1.id) }
            .map { Problem(result: $0, remedy: remedy(for: $0)) }
    }

    /// The results that are fine; with the problems, what the result headline counts.
    public static func okCount(_ results: [CheckResult]) -> Int {
        results.filter { $0.status == .ok }.count
    }

    /// `MANUAL_STEPS` in app/suggestions.py, for the checks Apple platforms run: a check whose
    /// result calls for a step the user can take gets "you can fix this", any other counted
    /// problem "nothing on this device fixes this", as on Windows when no tweak or step matches.
    public static func remedy(for result: CheckResult) -> Remedy {
        switch (result.key, result.status) {
        case ("signal", .warn), ("signal", .bad): .you              // move_closer
        case ("interference", .warn): .you                          // router_channel
        case ("dns", .warn): .you                                   // dns_server
        case ("physical_link", .warn): .you                         // antenna
        case ("bufferbloat", .warn), ("bufferbloat", .bad): .you    // router_sqm (not in the run)
        default: .none
        }
    }
}

extension LiveStats {
    /// Median round-trip time of the replies in the window; nil without any.
    public var medianRTT: Double? {
        let replies = samples.compactMap { $0 }.sorted()
        guard !replies.isEmpty else { return nil }
        let middle = replies.count / 2
        return replies.count.isMultiple(of: 2) ? (replies[middle - 1] + replies[middle]) / 2 : replies[middle]
    }
}
