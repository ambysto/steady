/// The speed test's live figure: the rate over about the last `window` seconds, from readings of a
/// running byte total. Pure, so the arithmetic is tested without a network.
public struct LiveRate: Sendable {
    public let window: Double
    private var points: [(seconds: Double, bytes: Int64)] = []

    public init(window: Double = 1) {
        self.window = window
    }

    /// Adds a reading and returns the rate in Mbit/s since the oldest reading still in the window,
    /// nil until two readings are apart in time.
    public mutating func add(seconds: Double, bytes: Int64) -> Double? {
        points.append((seconds, bytes))
        // The base is the newest reading at least `window` old, so the span covers the whole window.
        while points.count > 2, points[1].seconds <= seconds - window {
            points.removeFirst()
        }
        guard let base = points.first, seconds > base.seconds else { return nil }
        return Double(max(0, bytes - base.bytes)) * 8 / (seconds - base.seconds) / 1e6
    }
}
