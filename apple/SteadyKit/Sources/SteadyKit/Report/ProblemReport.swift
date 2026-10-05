import Foundation

/// The text of a problem report: what the user sees in full before choosing to send it, and
/// nothing else (docs/adr/0012-user-sent-problem-reports.md). Facts are written as plain
/// `key: value` lines in English for whoever reads the report; addresses are masked, except
/// the public resolvers every copy of the app measures.
public enum ProblemReport {
    public struct Context: Sendable {
        public var appVersion: String
        public var build: String
        public var system: String
        public var device: String
        public var language: String
        public var connection: String
        public var checks: [CheckResult]
        /// Per-minute rows of the last hour, oldest first.
        public var minutes: [MinuteAggregator.Minute]
        /// Recent lines of the app's own log.
        public var log: [String]
        /// Summaries of crashes and hangs the system reported (MetricKit).
        public var crashes: [String]
        public var description: String

        public init(appVersion: String, build: String, system: String, device: String, language: String,
                    connection: String, checks: [CheckResult], minutes: [MinuteAggregator.Minute],
                    log: [String], crashes: [String], description: String) {
            self.appVersion = appVersion
            self.build = build
            self.system = system
            self.device = device
            self.language = language
            self.connection = connection
            self.checks = checks
            self.minutes = minutes
            self.log = log
            self.crashes = crashes
            self.description = description
        }
    }

    /// Public resolvers and probe targets: the same for every user, so not personal.
    static let publicAddresses: Set<String> = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]

    public static func text(_ context: Context, renderer: Localizer) -> String {
        var lines: [String] = []
        let description = context.description.trimmingCharacters(in: .whitespacesAndNewlines)
        if !description.isEmpty {
            lines += ["## Description", description, ""]
        }
        lines += ["## App", "app: Ambysto Steady \(context.appVersion) (\(context.build))", "system: \(context.system)",
                  "device: \(context.device)", "language: \(context.language)", "connection: \(context.connection)", ""]
        lines.append("## Checks")
        for check in context.checks.sorted(by: { $0.id < $1.id }) {
            var line = "#\(check.id) \(check.key): \(check.status.rawValue) - \(renderer(check.summary))"
            if check.advice != .empty {
                line += " | advice: \(renderer.render(check.advice))"
            }
            lines.append(line)
        }
        lines += ["", "## Last hour (per minute: target sent/lost avg ms)"]
        for minute in context.minutes.suffix(60) {
            let time = ISO8601DateFormatter.string(from: Date(timeIntervalSince1970: TimeInterval(minute.start)),
                                                   timeZone: .gmt, formatOptions: [.withTime, .withColonSeparatorInTime])
            let rows = minute.rows.map { row in
                "\(row.target) \(row.sent)/\(row.lost)" + (row.avg.map { String(format: " %.1f", $0) } ?? "")
            }
            lines.append("\(time)Z " + rows.joined(separator: ", "))
        }
        if !context.crashes.isEmpty {
            lines += ["", "## Crashes and hangs"] + context.crashes
        }
        if !context.log.isEmpty {
            lines += ["", "## Recent log"] + context.log.suffix(80)
        }
        return redact(lines.joined(separator: "\n")) + "\n"
    }

    /// Replaces IPv4 and IPv6 addresses, other than the public resolvers, with "<address>".
    /// Candidates are confirmed with inet_pton, so times such as 10:15:17 stay as they are.
    static func redact(_ text: String) -> String {
        var result = text
        for match in candidates.matches(in: text, range: NSRange(text.startIndex..., in: text)).reversed() {
            guard let range = Range(match.range, in: result) else { continue }
            let token = String(result[range])
            let address = String(token.split(separator: "%", maxSplits: 1).first ?? "")
            if !publicAddresses.contains(address), isAddress(address) {
                result.replaceSubrange(range, with: "<address>")
            }
        }
        return result
    }

    private static let candidates = try! NSRegularExpression(
        pattern: #"(?<![\w:.])(?:[0-9A-Fa-f.:]*:[0-9A-Fa-f.:]*|(?:\d{1,3}\.){3}\d{1,3})(?:%[\w.]+)?(?![\w:])"#)

    private static func isAddress(_ text: String) -> Bool {
        var v4 = in_addr(), v6 = in6_addr()
        return inet_pton(AF_INET, text, &v4) == 1 || inet_pton(AF_INET6, text, &v6) == 1
    }
}
