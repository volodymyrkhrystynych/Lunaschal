import Foundation
import Security

/// What the app shares with its share extension through the
/// `group.com.lunaschal.mobile` App Group: which server it is signed in to,
/// with that session's token, and the folder for links waiting on the server.
///
/// The app's own sign-in stays in `SessionToken`, untouched; this is a copy
/// the app keeps current. An unsigned simulator build has no entitlements, so
/// there the copy is never made and the extension says to sign in — the app
/// itself is unaffected.
enum SharedSignIn {
    static let appGroup = "group.com.lunaschal.mobile"
    private static let service = "Lunaschal.share"

    private static var base: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccessGroup as String: appGroup]
    }

    /// Replaces any earlier copy: one server at a time, as in the app.
    static func save(server: URL, token: String) {
        SecItemDelete(base as CFDictionary)
        var item = base
        item[kSecAttrAccount as String] = server.absoluteString
        item[kSecValueData as String] = Data(token.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(item as CFDictionary, nil)
    }

    static func remove() {
        SecItemDelete(base as CFDictionary)
    }

    static func read() -> (server: URL, token: String)? {
        var request = base
        request[kSecReturnAttributes as String] = true
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &result) == errSecSuccess,
              let found = result as? [String: Any],
              let account = found[kSecAttrAccount as String] as? String, let server = URL(string: account),
              let data = found[kSecValueData as String] as? Data,
              let token = String(data: data, encoding: .utf8) else { return nil }
        return (server, token)
    }

    /// Links shared while the server was out of reach. Nil without the App
    /// Group entitlement.
    static func importOutboxRoot() -> URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)?
            .appendingPathComponent("fic-imports", isDirectory: true)
    }

    /// YouTube links shared for the Capture composer's draft. Nil without the
    /// App Group entitlement.
    static func sharedLinksRoot() -> URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)?
            .appendingPathComponent("shared-links", isDirectory: true)
    }
}
