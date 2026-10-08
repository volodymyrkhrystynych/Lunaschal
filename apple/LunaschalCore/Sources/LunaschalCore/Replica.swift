import Foundation

public enum JSONValue: Codable, Equatable {
    case null, bool(Bool), number(Double), string(String), array([JSONValue]), object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let bool = try? value.decode(Bool.self) { self = .bool(bool) }
        else if let number = try? value.decode(Double.self) { self = .number(number) }
        else if let text = try? value.decode(String.self) { self = .string(text) }
        else if let list = try? value.decode([JSONValue].self) { self = .array(list) }
        else { self = .object(try value.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .null: try value.encodeNil()
        case .bool(let bool): try value.encode(bool)
        case .number(let number): try value.encode(number)
        case .string(let text): try value.encode(text)
        case .array(let list): try value.encode(list)
        case .object(let object): try value.encode(object)
        }
    }

    public var string: String? { if case .string(let value) = self { return value }; return nil }
    public var number: Double? { if case .number(let value) = self { return value }; return nil }
}

public struct SyncChange: Codable, Equatable, Identifiable {
    public let revision: Int64
    public let collection: String
    public let id: String
    public let deleted: Bool
    public let data: [String: JSONValue]?

    public var title: String {
        if collection == "newspaper_frontpages" {
            let label = [data?["paper"]?.string, data?["date"]?.string].compactMap { $0 }.joined(separator: " · ")
            return label.isEmpty ? "Front page" : label
        }
        if collection == "food_entries" { return data?["dish"]?.string ?? "Meal" }
        return data?["title"]?.string ?? data?["name"]?.string ?? data?["content"]?.string ?? collection
    }
}

public struct SyncPage: Codable {
    public let protocolVersion: Int
    public let epoch: String
    public let mode: String
    public let changes: [SyncChange]
    public let hasMore: Bool
    public let cursor: String
    public let collections: [String]
}

public struct ReplicaOperation: Codable, Identifiable, Equatable {
    public let id: String
    public let epoch: String
    public let collection: String
    public let recordId: String
    public let baseRevision: Int64
    public let action: String
    public let data: [String: JSONValue]

    public init(epoch: String, record: SyncChange, action: String = "update", data: [String: JSONValue]) {
        id = ULID.make()
        self.epoch = epoch
        collection = record.collection
        recordId = record.id
        baseRevision = record.revision
        self.action = action
        self.data = data
    }
}

public struct PendingEdit: Identifiable {
    public var id: String { operation.id }
    public let operation: ReplicaOperation
    public let state: String
    public let error: String?
    public let original: SyncChange
    public let conflict: SyncChange?
}

public struct OperationReply: Codable {
    public let operationId: String?
    public let change: SyncChange?
    public let conflict: Bool?
    public let current: SyncChange?
    public let error: String?
    public let resetRequired: Bool?
}

public enum ReplicaError: LocalizedError {
    case database(String), invalidPage, needsBootstrap, editAlreadyPending, invalidEdit
    public var errorDescription: String? {
        switch self {
        case .database(let message): return "Device database: \(message)"
        case .invalidPage: return "The server returned an incompatible sync page. Local changes were kept."
        case .needsBootstrap: return "Download the latest server records before editing."
        case .editAlreadyPending: return "This entry already has a pending edit. Sync or resolve it first."
        case .invalidEdit: return "This record cannot be edited with that operation."
        }
    }
}
