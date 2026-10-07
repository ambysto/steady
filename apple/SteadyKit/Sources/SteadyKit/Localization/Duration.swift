extension Message {
    /// A length of time as a message: "2 hours 5 minutes", "45 seconds". The port of
    /// `format_duration` in app/i18n.py, so both platforms word it the same way.
    public static func duration(seconds: Double) -> Message {
        let total = max(0, Int(seconds))
        let (hours, rest) = total.quotientAndRemainder(dividingBy: 3600)
        let (minutes, secs) = rest.quotientAndRemainder(dividingBy: 60)
        func count(_ key: String, _ value: Int) -> MessageValue { .message(Message(key, ["count": .number(Double(value))])) }
        if hours > 0, minutes == 0 { return Message("time.hours", ["count": .number(Double(hours))]) }
        if hours > 0 { return Message("time.hours_minutes", ["hours": count("time.hours", hours), "minutes": count("time.minutes", minutes)]) }
        if minutes > 0, secs > 0 {
            return Message("time.minutes_seconds", ["minutes": count("time.minutes", minutes), "seconds": count("time.seconds", secs)])
        }
        if minutes > 0 { return Message("time.minutes", ["count": .number(Double(minutes))]) }
        return Message("time.seconds", ["count": .number(Double(secs))])
    }
}
