import Foundation

/// Reading helpers for the parts of a chat reply the server leaves
/// open-ended: a message's metadata and a confirm card's editable payload.
/// Read loosely, so one odd field never takes the whole conversation down.
public extension JSONValue {
    /// Parses a JSON string, or nil when it isn't one.
    static func parse(_ text: String?) -> JSONValue? {
        guard let text, let data = text.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(JSONValue.self, from: data)
    }

    subscript(key: String) -> JSONValue? {
        if case let .object(value) = self { return value[key] }
        return nil
    }

    var bool: Bool? { if case let .bool(value) = self { return value }; return nil }
    var array: [JSONValue]? { if case let .array(value) = self { return value }; return nil }
    var object: [String: JSONValue]? { if case let .object(value) = self { return value }; return nil }
    var int: Int? { number.flatMap { $0 == $0.rounded() ? Int(exactly: $0) : nil } }

    /// What an editable field shows: text as is, a number without a ".0".
    var text: String {
        switch self {
        case let .string(value): return value
        case let .number(value): return value == value.rounded() && abs(value) < 1e15 ? String(Int64(value)) : String(value)
        default: return ""
        }
    }
}
