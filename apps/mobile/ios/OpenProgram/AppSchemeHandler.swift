import Foundation
import UniformTypeIdentifiers
import WebKit

/// Serves `openprogram://app/…`.
///
/// The UI's own files come from the static export inside the app, so
/// opening the app reads none of them over the network. Server paths
/// (`/api/`, `/files/`, …) are forwarded to the computer with the device
/// credential and no Origin, and streamed back as they arrive, so event
/// streams work and the page sees every request as same-origin.
final class AppSchemeHandler: NSObject, WKURLSchemeHandler {
    static let scheme = "openprogram"
    static let origin = "openprogram://app"

    /// First path segments that belong to the computer, not to the UI.
    private static let serverPrefixes: Set<String> = [
        "api", "files", "hooks", "gateway", "application-assets", "docs", "healthz",
    ]

    private let root: URL
    private let target: () -> (origin: String, token: String)?
    private var running: [ObjectIdentifier: ProxyCall] = [:]

    init(root: URL, target: @escaping () -> (origin: String, token: String)?) {
        self.root = root
        self.target = target
    }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else {
            task.didFailWithError(URLError(.badURL))
            return
        }
        let first = url.path.split(separator: "/").first.map(String.init) ?? ""
        if Self.serverPrefixes.contains(first) {
            forward(task, url: url)
        } else {
            serveLocal(task, url: url)
        }
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {
        running.removeValue(forKey: ObjectIdentifier(task))?.cancel()
    }

    // MARK: UI files

    /// The worker's rule (interfaces/server/frontend.py): the file, its
    /// folder's index.html, the page's .html, the nearest ancestor page,
    /// then the chat shell.
    private func serveLocal(_ task: WKURLSchemeTask, url: URL) {
        let parts = url.path.split(separator: "/").map(String.init).filter { $0 != ".." && $0 != "." }
        var candidates: [URL] = []
        if parts.isEmpty {
            candidates.append(root.appendingPathComponent("index.html"))
        } else {
            let file = parts.reduce(root) { $0.appendingPathComponent($1) }
            candidates += [file, file.appendingPathComponent("index.html")]
            if file.pathExtension.isEmpty { candidates.append(file.appendingPathExtension("html")) }
            var ancestors = parts
            while !ancestors.isEmpty {
                ancestors.removeLast()
                if !ancestors.isEmpty {
                    candidates.append(ancestors.reduce(root) { $0.appendingPathComponent($1) }.appendingPathExtension("html"))
                }
            }
            candidates += [root.appendingPathComponent("chat.html"), root.appendingPathComponent("index.html")]
        }
        var isDirectory: ObjCBool = false
        guard let found = candidates.first(where: {
            FileManager.default.fileExists(atPath: $0.path, isDirectory: &isDirectory) && !isDirectory.boolValue
        }), let data = try? Data(contentsOf: found) else {
            respond(task, url: url, status: 404, type: "text/plain", data: Data("not found".utf8))
            return
        }
        let type = UTType(filenameExtension: found.pathExtension)?.preferredMIMEType
            ?? (found.pathExtension == "txt" ? "text/x-component" : "application/octet-stream")
        respond(task, url: url, status: 200, type: type, data: data)
    }

    private func respond(_ task: WKURLSchemeTask, url: URL, status: Int, type: String, data: Data) {
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: [
            "Content-Type": type,
            "Content-Length": String(data.count),
            "Cache-Control": "no-cache",
        ])!
        task.didReceive(response)
        task.didReceive(data)
        task.didFinish()
    }

    // MARK: Forwarding

    private func forward(_ task: WKURLSchemeTask, url: URL) {
        guard let target = target(),
              var parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let base = URLComponents(string: target.origin) else {
            // No computer answers yet: the page retries as it would after a network drop.
            task.didFailWithError(URLError(.notConnectedToInternet))
            return
        }
        parts.scheme = base.scheme
        parts.host = base.host
        parts.port = base.port
        guard let destination = parts.url else {
            task.didFailWithError(URLError(.badURL))
            return
        }
        var request = URLRequest(url: destination, timeoutInterval: 300)
        request.httpMethod = task.request.httpMethod ?? "GET"
        for (name, value) in task.request.allHTTPHeaderFields ?? [:] {
            switch name.lowercased() {
            case "origin", "cookie", "referer", "authorization", "host": continue
            default: request.setValue(value, forHTTPHeaderField: name)
            }
        }
        request.setValue("Bearer \(target.token)", forHTTPHeaderField: "Authorization")
        request.httpBody = task.request.httpBody ?? task.request.httpBodyStream.map(Self.read)
        let call = ProxyCall(task: task, pageURL: url) { [weak self] in
            self?.running.removeValue(forKey: ObjectIdentifier(task))
        }
        running[ObjectIdentifier(task)] = call
        call.start(request)
    }

    private static func read(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

/// One forwarded request, streamed back into its scheme task on the main thread.
/// Nothing is sent to a task WebKit has stopped: that would raise.
private final class ProxyCall: NSObject, URLSessionDataDelegate {
    private let task: WKURLSchemeTask
    private let pageURL: URL
    private let done: () -> Void
    private var running: URLSessionDataTask?
    private var stopped = false

    /// One session for every forwarded request, so requests reuse its
    /// connections: a session per request paid a new TLS handshake each
    /// time, most of a second over the internet. Each request still gets
    /// its own delegate (`URLSessionTask.delegate`).
    private static let shared: URLSession = {
        let config = URLSessionConfiguration.default
        config.httpCookieAcceptPolicy = .never
        config.httpShouldSetCookies = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 300
        config.httpMaximumConnectionsPerHost = 8
        return URLSession(configuration: config, delegate: nil, delegateQueue: .main)
    }()

    init(task: WKURLSchemeTask, pageURL: URL, done: @escaping () -> Void) {
        self.task = task
        self.pageURL = pageURL
        self.done = done
    }

    func start(_ request: URLRequest) {
        let task = Self.shared.dataTask(with: request)
        task.delegate = self
        running = task
        task.resume()
    }

    func cancel() {
        stopped = true
        running?.cancel()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard !stopped else { return completionHandler(.cancel) }
        let http = response as? HTTPURLResponse
        var headers: [String: String] = [:]
        for (key, value) in http?.allHeaderFields ?? [:] {
            guard let key = key as? String, let value = value as? String else { continue }
            // The body arrives already decoded; its length is no longer the header's.
            if ["content-encoding", "content-length", "set-cookie"].contains(key.lowercased()) { continue }
            headers[key] = value
        }
        let page = HTTPURLResponse(url: pageURL, statusCode: http?.statusCode ?? 200,
                                   httpVersion: "HTTP/1.1", headerFields: headers)!
        task.didReceive(page)
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard !stopped else { return }
        task.didReceive(data)
    }

    func urlSession(_ session: URLSession, task sessionTask: URLSessionTask, didCompleteWithError error: Error?) {
        defer {
            running = nil
            done()
        }
        guard !stopped else { return }
        stopped = true
        if let error { task.didFailWithError(error) } else { task.didFinish() }
    }
}
