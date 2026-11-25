import Foundation
import Network
import Security

/// A computer this phone is paired with. The credential is not here: it lives in the Keychain.
struct Computer: Codable, Identifiable, Equatable {
    /// The device id the computer gave this phone when it paired.
    let id: String
    var name: String
    /// Every address the pairing link named, as origins (`http://10.0.0.5:18100`, `https://x.ts.net`).
    var addresses: [String]
}

enum PairingError: LocalizedError {
    case notALink, unreachable, refused

    var errorDescription: String? {
        switch self {
        case .notALink:
            return String(localized: "This is not an OpenProgram pairing code. On the computer open Settings → Devices → Add device.")
        case .unreachable:
            return String(localized: "Can't reach the computer. Check that the phone and the computer are online, then scan again.")
        case .refused:
            return String(localized: "This code has expired or was already used. Show a new one on the computer.")
        }
    }
}

/// The paired computers, the one in use, and the address it answers on.
///
/// Addresses are tried local network first. A network failure only moves
/// on to the next address; only a 401 from a computer that answered means
/// this phone was revoked (docs: reference/design/distribution/mobile-app).
@MainActor
final class ComputerStore: ObservableObject {
    static let shared = ComputerStore()

    @Published private(set) var computers: [Computer] = []
    @Published private(set) var currentID: String?
    /// The origin the current computer answered on, or nil while none has.
    @Published private(set) var address: String?

    var current: Computer? { computers.first { $0.id == currentID } }

    private let defaults = UserDefaults.standard
    private let monitor = NWPathMonitor()
    private var probing: Task<Void, Never>?

    private init() {
        if let data = defaults.data(forKey: "computers.v1"),
           let saved = try? JSONDecoder().decode([Computer].self, from: data) {
            computers = saved
        }
        currentID = defaults.string(forKey: "current.v1") ?? computers.first?.id
        monitor.pathUpdateHandler = { [weak self] _ in
            Task { @MainActor in self?.connect() }
        }
        monitor.start(queue: .global(qos: .utility))
    }

    // MARK: Pairing

    /// Pair with the computer a pairing link names: `<origin>/#pair=<code>&via=<origin>…`.
    func pair(link: String) async throws {
        let (addresses, code) = try Self.parse(link: link)
        var lastError: Error = PairingError.unreachable
        for origin in Self.ordered(addresses) {
            do {
                let paired = try await Self.redeem(code: code, at: origin)
                let computer = Computer(id: paired.deviceID, name: paired.name, addresses: addresses)
                Keychain.set(paired.token, for: computer.id)
                computers.removeAll { $0.id == computer.id }
                computers.append(computer)
                select(computer.id)
                return
            } catch PairingError.refused {
                throw PairingError.refused
            } catch {
                lastError = error
            }
        }
        throw lastError
    }

    static func parse(link: String) throws -> (addresses: [String], code: String) {
        guard let url = URL(string: link.trimmingCharacters(in: .whitespacesAndNewlines)),
              let first = origin(of: url),
              let fragment = url.fragment else { throw PairingError.notALink }
        var items = URLComponents()
        items.percentEncodedQuery = fragment
        let query = items.queryItems ?? []
        guard let code = query.first(where: { $0.name == "pair" })?.value, !code.isEmpty else {
            throw PairingError.notALink
        }
        var addresses = [first]
        for via in query where via.name == "via" {
            if let value = via.value, let url = URL(string: value), let extra = origin(of: url), !addresses.contains(extra) {
                addresses.append(extra)
            }
        }
        return (addresses, code)
    }

    private static func origin(of url: URL) -> String? {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host, !host.isEmpty else { return nil }
        return url.port.map { "\(scheme)://\(host):\($0)" } ?? "\(scheme)://\(host)"
    }

    private struct Paired { let deviceID: String; let token: String; let name: String }

    private static func redeem(code: String, at origin: String) async throws -> Paired {
        var request = URLRequest(url: URL(string: origin + "/api/auth/pair")!, timeoutInterval: 15)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // No Origin: the computer answers a native client with the credential, not a cookie.
        request.httpBody = try JSONSerialization.data(withJSONObject: ["code": code, "label": "OpenProgram app on iPhone"])
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 401 { throw PairingError.refused }
        guard status == 200,
              let body = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = body["device_id"] as? String, let token = body["token"] as? String else {
            throw PairingError.unreachable
        }
        return Paired(deviceID: id, token: token, name: (body["name"] as? String) ?? "OpenProgram")
    }

    // MARK: Choosing and forgetting

    func select(_ id: String) {
        currentID = id
        address = nil
        save()
        connect()
    }

    /// The computer refused this phone (revoked), or the user removed it.
    func forget(_ id: String) {
        Keychain.remove(for: id)
        computers.removeAll { $0.id == id }
        if currentID == id {
            currentID = computers.first?.id
            address = nil
        }
        save()
        connect()
    }

    func credential(for id: String) -> String? { Keychain.get(for: id) }

    private func save() {
        defaults.set(try? JSONEncoder().encode(computers), forKey: "computers.v1")
        defaults.set(currentID, forKey: "current.v1")
    }

    // MARK: Addresses

    /// Find the address the current computer answers on, local network first.
    /// Called at start, when the network changes and when the app comes back.
    func connect() {
        probing?.cancel()
        guard let computer = current, let token = credential(for: computer.id) else { return }
        probing = Task { [weak self] in
            while !Task.isCancelled {
                switch await Self.firstAnswering(computer.addresses, token: token) {
                case .answered(let origin):
                    if self?.address != origin { self?.address = origin }
                    return
                case .revoked:
                    self?.forget(computer.id)
                    return
                case .none:
                    try? await Task.sleep(for: .seconds(5))
                }
            }
        }
    }

    private enum Probe { case answered(String), revoked, none }

    private static func firstAnswering(_ addresses: [String], token: String) async -> Probe {
        for origin in ordered(addresses) {
            var request = URLRequest(url: URL(string: origin + "/api/auth/session")!,
                                     timeoutInterval: isLocal(origin) ? 2 : 10)
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            guard let (_, response) = try? await session.data(for: request),
                  let status = (response as? HTTPURLResponse)?.statusCode else { continue }
            if status == 204 { return .answered(origin) }
            if status == 401 { return .revoked }
        }
        return .none
    }

    /// Plain-HTTP addresses (Same Wi-Fi) before the https ones (Anywhere).
    static func ordered(_ addresses: [String]) -> [String] {
        addresses.filter(isLocal) + addresses.filter { !isLocal($0) }
    }

    static func isLocal(_ origin: String) -> Bool { origin.hasPrefix("http://") }

    static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.httpCookieAcceptPolicy = .never
        config.httpShouldSetCookies = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }()
}

/// Device credentials, one Keychain item per computer, readable after the first unlock.
enum Keychain {
    private static let service = "ai.openprogram.mobile.device"

    static func set(_ value: String, for account: String) {
        remove(for: account)
        let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: Data(value.utf8),
        ]
        SecItemAdd(item as CFDictionary, nil)
    }

    static func get(for account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
        ]
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func remove(for account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
