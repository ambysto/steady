/// Verdict of one diagnosis check, ordered from best to worst as in app/diagnostics.py.
public enum CheckStatus: String, Sendable, Codable, Comparable {
    case ok, info, warn, bad

    private var rank: Int {
        switch self {
        case .ok: 0
        case .info: 1
        case .warn: 2
        case .bad: 3
        }
    }

    public static func < (lhs: CheckStatus, rhs: CheckStatus) -> Bool { lhs.rank < rhs.rank }

    /// The worst of `statuses`; `.ok` when there are none.
    public static func worst(_ statuses: some Sequence<CheckStatus>) -> CheckStatus {
        statuses.max() ?? .ok
    }
}

/// One check's result. Text is kept as messages (ADR-0006) and rendered by `Localizer`.
/// `details` and `advice` hold `.empty` where Python stores "" (no advice, no optional part).
public struct CheckResult: Equatable, Sendable {
    /// Number of the check in docs/DIAGNOSTICS.md.
    public var id: Int
    public var key: String
    public var status: CheckStatus
    public var summary: Message
    public var details: [MessageValue]
    public var advice: MessageValue

    public var title: Message { Message("diag.\(key).title") }

    /// Summaries that say the check could not judge (not enough data yet, not on Wi‑Fi): the
    /// Diagnostics tab shows them, but they are nothing to note in the Overview's summary.
    static let notAssessed: Set<String> = ["diag.ping.no_data", "diag.physical_link.not_enough",
                                           "diag.interference.not_connected", "diag.signal.not_connected",
                                           "diag.common.no_wifi_card"]

    /// false while the check has nothing to go on.
    public var isAssessed: Bool { !Self.notAssessed.contains(summary.key) }

    public init(id: Int, key: String, status: CheckStatus, summary: Message,
                details: [MessageValue] = [], advice: MessageValue = .empty) {
        self.id = id
        self.key = key
        self.status = status
        self.summary = summary
        self.details = details
        self.advice = advice
    }
}
