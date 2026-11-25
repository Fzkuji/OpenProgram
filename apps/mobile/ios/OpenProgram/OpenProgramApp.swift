import SwiftUI
import VisionKit

@main
struct OpenProgramApp: App {
    @StateObject private var store = ComputerStore.shared
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .onChange(of: phase) { _, now in
                    if now == .active { store.connect() }
                }
        }
    }
}

/// Pairing until a computer is paired; then its UI.
struct RootView: View {
    @EnvironmentObject private var store: ComputerStore
    @State private var showComputers = false

    var body: some View {
        Group {
            if let computer = store.current, let token = store.credential(for: computer.id) {
                // The UI shows at once; until an address answers it aims at the
                // likeliest one, and moves when another answers.
                let origin = store.address ?? ComputerStore.ordered(computer.addresses).first ?? ""
                WebShell(computer: computer, origin: origin, token: token) { action in
                    switch action {
                    case "signedOut": store.forget(computer.id)
                    case "computers": showComputers = true
                    default: break
                    }
                }
                .id("\(computer.id)|\(origin)")
                .ignoresSafeArea()
            } else {
                PairView()
            }
        }
        .sheet(isPresented: $showComputers) { ComputersView() }
    }
}

/// First screen: one action, scan the code the computer shows.
struct PairView: View {
    @EnvironmentObject private var store: ComputerStore
    @Environment(\.dismiss) private var dismiss
    var inSheet = false
    @State private var scanning = false
    @State private var busy = false
    @State private var problem: String?

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            Image("Logo")
                .resizable()
                .frame(width: 88, height: 88)
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                .shadow(color: Color(red: 0.23, green: 0.23, blue: 0.66).opacity(0.3), radius: 12, y: 8)
            Text("Connect your computer")
                .font(.system(size: 28, weight: .bold))
                .padding(.top, 26)
            Text("In OpenProgram on the computer, open **Settings → Devices → Add device** and scan the code it shows.")
                .font(.system(size: 16))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.top, 10)
                .padding(.horizontal, 28)
            if let problem {
                Text(problem)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.top, 16)
                    .padding(.horizontal, 28)
            }
            Spacer()
            VStack(spacing: 10) {
                Button {
                    scanning = true
                } label: {
                    Label("Scan QR code", systemImage: "qrcode.viewfinder")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .disabled(busy || !DataScannerViewController.isSupported)
                // The system's paste button: tapping it is the consent, so
                // iOS shows no "Allow Paste" prompt.
                PasteButton(payloadType: String.self) { strings in
                    guard let text = strings.first else { return }
                    Task { await pair(text) }
                }
                .labelStyle(.titleAndIcon)
                .buttonBorderShape(.capsule)
                .disabled(busy)
            }
            .controlSize(.large)
            .padding(.horizontal, 24)
            .padding(.bottom, 24)
        }
        .overlay { if busy { ProgressView().controlSize(.large) } }
        .fullScreenCover(isPresented: $scanning) {
            ScannerView { text in
                scanning = false
                Task { await pair(text) }
            } onCancel: {
                scanning = false
            }
            .ignoresSafeArea()
        }
    }

    private func pair(_ link: String) async {
        busy = true
        problem = nil
        do {
            try await store.pair(link: link)
            if inSheet { dismiss() }
        } catch {
            problem = error.localizedDescription
        }
        busy = false
    }
}

/// The camera, reading the first QR code it sees.
struct ScannerView: UIViewControllerRepresentable {
    let onCode: (String) -> Void
    let onCancel: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode) }

    func makeUIViewController(context: Context) -> UINavigationController {
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])],
            qualityLevel: .balanced,
            isHighlightingEnabled: true
        )
        scanner.delegate = context.coordinator
        scanner.navigationItem.leftBarButtonItem = UIBarButtonItem(
            systemItem: .cancel, primaryAction: UIAction { _ in onCancel() }
        )
        try? scanner.startScanning()
        return UINavigationController(rootViewController: scanner)
    }

    func updateUIViewController(_ controller: UINavigationController, context: Context) {}

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let onCode: (String) -> Void
        private var done = false

        init(onCode: @escaping (String) -> Void) { self.onCode = onCode }

        func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            guard !done else { return }
            for case .barcode(let code) in items {
                if let text = code.payloadStringValue {
                    done = true
                    scanner.stopScanning()
                    onCode(text)
                    return
                }
            }
        }
    }
}

/// The paired computers: which one is in use, add another, forget one.
struct ComputersView: View {
    @EnvironmentObject private var store: ComputerStore
    @Environment(\.dismiss) private var dismiss
    @State private var adding = false

    var body: some View {
        NavigationStack {
            List {
                Section("Computers") {
                    ForEach(store.computers) { computer in
                        Button {
                            store.select(computer.id)
                            dismiss()
                        } label: {
                            HStack(spacing: 12) {
                                Image("Logo").resizable().frame(width: 30, height: 30)
                                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(computer.name).foregroundStyle(.primary)
                                    Text(status(of: computer)).font(.footnote).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if computer.id == store.currentID {
                                    Image(systemName: "checkmark").foregroundStyle(.tint)
                                }
                            }
                        }
                    }
                    .onDelete { offsets in
                        offsets.map { store.computers[$0].id }.forEach(store.forget)
                    }
                    Button {
                        adding = true
                    } label: {
                        Label("Add computer", systemImage: "plus")
                    }
                }
            }
            .navigationTitle("Computers")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .sheet(isPresented: $adding) { PairView(inSheet: true) }
        }
    }

    private func status(of computer: Computer) -> String {
        guard computer.id == store.currentID, let address = store.address else {
            return String(localized: "Not connected")
        }
        let via = ComputerStore.isLocal(address) ? String(localized: "Same Wi-Fi") : String(localized: "Anywhere")
        return String(localized: "Connected · \(via)")
    }
}
