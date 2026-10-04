import Foundation
import Security

enum SessionToken {
    private static func query(_ server: URL) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "Lunaschal.session",
         kSecAttrAccount as String: server.absoluteString]
    }

    static func read(server: URL) throws -> String? {
        var request = query(server)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data,
              let token = String(data: data, encoding: .utf8) else { throw KeychainError(status: status) }
        return token
    }

    static func save(_ token: String, server: URL) throws {
        let attributes: [String: Any] = [
            kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]
        let status = SecItemUpdate(query(server) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let added = SecItemAdd(query(server).merging(attributes) { _, new in new } as CFDictionary, nil)
            guard added == errSecSuccess else { throw KeychainError(status: added) }
        } else if status != errSecSuccess { throw KeychainError(status: status) }
    }

    static func remove(server: URL) throws {
        let status = SecItemDelete(query(server) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(status: status) }
    }

    private struct KeychainError: LocalizedError {
        let status: OSStatus
        var errorDescription: String? { "Could not access the saved sign-in (\(status))." }
    }
}
