import Foundation
import os
import SQLite3

/// Per-minute measurement rows in SQLite, in the `minute_stats` table of app/storage.py (ADR-0011).
/// Used from the main actor only, by `LiveMonitor`.
public final class MinuteStore {
    public struct StoreError: Error, CustomStringConvertible {
        public let description: String
    }

    static let retentionDays = 30.0
    private var db: OpaquePointer?

    private static let log = Logger(subsystem: "com.ambysto.steady", category: "storage")

    /// The app's database: Application Support/History/metrics.sqlite. The whole folder is
    /// excluded from backups, so SQLite's -wal and -shm files are too.
    public static func standard() throws -> MinuteStore {
        let fileManager = FileManager.default
        let support = try fileManager.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                          appropriateFor: nil, create: true)
        var folder = support.appending(path: "History", directoryHint: .isDirectory)
        try fileManager.createDirectory(at: folder, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        do {
            try folder.setResourceValues(values)
        } catch {
            log.error("could not exclude the history from backups: \(String(describing: error), privacy: .public)")
        }
        let url = folder.appending(path: "metrics.sqlite")
        // Builds before 2026-10-05 kept the database directly in Application Support.
        for suffix in ["", "-wal", "-shm"] {
            let old = support.appending(path: "metrics.sqlite" + suffix)
            let new = folder.appending(path: "metrics.sqlite" + suffix)
            if fileManager.fileExists(atPath: old.path), !fileManager.fileExists(atPath: new.path) {
                try? fileManager.moveItem(at: old, to: new)
            }
        }
        return try MinuteStore(url: url)
    }

    public init(url: URL) throws {
        guard sqlite3_open_v2(url.path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            let message = db.map { String(cString: sqlite3_errmsg($0)) } ?? "cannot open"
            sqlite3_close(db)
            db = nil   // deinit runs even though init throws, and must not close it again
            throw StoreError(description: message)
        }
        try execute("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS minute_stats (
                ts     INTEGER NOT NULL,
                target TEXT    NOT NULL,
                ip     TEXT,
                sent   INTEGER NOT NULL,
                lost   INTEGER NOT NULL,
                avg    REAL,
                max    REAL,
                jitter REAL,
                PRIMARY KEY (ts, target)
            ) WITHOUT ROWID;
            """)
    }

    deinit {
        sqlite3_close(db)
    }

    /// Stores a closed (or partial) minute. A row that already exists for the same minute and
    /// target, written before the app was closed and reopened, is merged into.
    public func insert(_ minute: MinuteAggregator.Minute) throws {
        try execute("BEGIN")
        do {
            for row in minute.rows {
                try run("""
                    INSERT INTO minute_stats (ts, target, sent, lost, avg, max, jitter) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (ts, target) DO UPDATE SET
                        avg = CASE
                            WHEN excluded.avg IS NULL THEN avg
                            WHEN avg IS NULL THEN excluded.avg
                            ELSE round((avg * (sent - lost) + excluded.avg * (excluded.sent - excluded.lost))
                                       / ((sent - lost) + (excluded.sent - excluded.lost)), 1)
                        END,
                        max = CASE WHEN max IS NULL OR excluded.max > max THEN COALESCE(excluded.max, max) ELSE max END,
                        sent = sent + excluded.sent, lost = lost + excluded.lost,
                        jitter = COALESCE(excluded.jitter, jitter)
                    """, [.int(minute.start), .text(row.target), .int(row.sent), .int(row.lost), .double(row.avg),
                          .double(row.max), .double(row.jitter)])
            }
            try execute("COMMIT")
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    /// Minutes starting at or after `start`, oldest first.
    public func minutes(since start: Int) throws -> [MinuteAggregator.Minute] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT ts, target, sent, lost, jitter, avg, max FROM minute_stats WHERE ts >= ? ORDER BY ts, target",
                                 -1, &statement, nil) == SQLITE_OK else { throw lastError() }
        defer { sqlite3_finalize(statement) }
        sqlite3_bind_int64(statement, 1, Int64(start))
        var minutes: [MinuteAggregator.Minute] = []
        var rows: [PingQuality.Row] = []
        var current: Int?
        while sqlite3_step(statement) == SQLITE_ROW {
            let ts = Int(sqlite3_column_int64(statement, 0))
            if let current, ts != current {
                minutes.append(MinuteAggregator.Minute(start: current, rows: rows))
                rows = []
            }
            current = ts
            func double(_ column: Int32) -> Double? {
                sqlite3_column_type(statement, column) == SQLITE_NULL ? nil : sqlite3_column_double(statement, column)
            }
            rows.append(PingQuality.Row(target: String(cString: sqlite3_column_text(statement, 1)),
                                        sent: Int(sqlite3_column_int64(statement, 2)),
                                        lost: Int(sqlite3_column_int64(statement, 3)), jitter: double(4),
                                        avg: double(5), max: double(6)))
        }
        if let current {
            minutes.append(MinuteAggregator.Minute(start: current, rows: rows))
        }
        return minutes
    }

    /// How many minutes are stored (for the Settings screen).
    public func minuteCount() throws -> Int {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT COUNT(DISTINCT ts) FROM minute_stats", -1, &statement, nil) == SQLITE_OK else {
            throw lastError()
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else { throw lastError() }
        return Int(sqlite3_column_int64(statement, 0))
    }

    /// Deletes every stored minute (Settings > Delete history), then rewrites the file and empties
    /// the WAL so the old rows do not stay behind in free pages.
    public func deleteAll() throws {
        try execute("DELETE FROM minute_stats; VACUUM; PRAGMA wal_checkpoint(TRUNCATE);")
    }

    /// Deletes rows older than the retention period; returns how many.
    @discardableResult
    public func purge(now: Double) throws -> Int {
        try run("DELETE FROM minute_stats WHERE ts < ?", [.int(Int(now - Self.retentionDays * 86400))])
        return Int(sqlite3_changes(db))
    }

    // MARK: - SQLite plumbing

    private enum Value {
        case int(Int), text(String), double(Double?)
    }

    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    private func run(_ sql: String, _ values: [Value]) throws {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw lastError() }
        defer { sqlite3_finalize(statement) }
        for (index, value) in values.enumerated() {
            let position = Int32(index + 1)
            switch value {
            case .int(let number): sqlite3_bind_int64(statement, position, Int64(number))
            case .text(let text): sqlite3_bind_text(statement, position, text, -1, Self.transient)
            case .double(let number?): sqlite3_bind_double(statement, position, number)
            case .double(nil): sqlite3_bind_null(statement, position)
            }
        }
        guard sqlite3_step(statement) == SQLITE_DONE else { throw lastError() }
    }

    private func execute(_ sql: String) throws {
        guard sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK else { throw lastError() }
    }

    private func lastError() -> StoreError {
        StoreError(description: String(cString: sqlite3_errmsg(db)))
    }
}
