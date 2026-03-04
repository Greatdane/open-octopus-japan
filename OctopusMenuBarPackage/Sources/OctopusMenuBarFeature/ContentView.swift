import SwiftUI

// MARK: - Data Models

public struct OctopusData: Codable, Sendable {
    var timestamp: String?
    var rate: Double?
    var balance: Double
    var balanceIsCredit: Bool
    var yesterdayKwh: Double
    var yesterdayCost: Double
    var todayKwh: Double
    var todayCost: Double
    var hourlyUsage: [Double]
    var tariffName: String?
    var standingCharge: Double
    var error: String?
    var response: String?
    var monthlyProjection: Double
    var peakRate: Double?
    var halfHourlyUsage: [Double]  // 48 slots for last 24h
    var dataDateLatest: String?   // Actual date of "today" data
    var dataDatePrevious: String? // Actual date of "yesterday" data

    enum CodingKeys: String, CodingKey {
        case timestamp, rate, balance, error, response
        case balanceIsCredit = "balance_is_credit"
        case yesterdayKwh = "yesterday_kwh"
        case yesterdayCost = "yesterday_cost"
        case todayKwh = "today_kwh"
        case todayCost = "today_cost"
        case hourlyUsage = "hourly_usage"
        case tariffName = "tariff_name"
        case standingCharge = "standing_charge"
        case monthlyProjection = "monthly_projection"
        case peakRate = "peak_rate"
        case halfHourlyUsage = "half_hourly_usage"
        case dataDateLatest = "data_date_latest"
        case dataDatePrevious = "data_date_previous"
    }

    init() {
        balance = 0
        balanceIsCredit = false
        yesterdayKwh = 0
        yesterdayCost = 0
        todayKwh = 0
        todayCost = 0
        hourlyUsage = []
        standingCharge = 0
        monthlyProjection = 0
        halfHourlyUsage = []
    }
}

// MARK: - Settings

public enum UsageDisplayMode: String, CaseIterable, Identifiable {
    case hourly = "Hourly"           // 24 hourly bars
    case halfHourly = "Half-hourly"  // 48 half-hourly bars

    public var id: String { rawValue }
}

public enum MenuBarDisplayMode: String, CaseIterable, Identifiable {
    case auto = "Auto"           // Rate > Icon
    case rate = "Rate"           // Always show rate (or icon if unavailable)
    case iconOnly = "Icon Only"  // Just the ⚡ icon
    case octopus = "Octopus"     // 🐙 emoji

    public var id: String { rawValue }

    public var description: String {
        switch self {
        case .auto: return "Rate → Icon"
        case .rate: return "Current rate"
        case .iconOnly: return "Minimal ⚡"
        case .octopus: return "🐙"
        }
    }
}

// MARK: - App State

@MainActor
public class AppState: ObservableObject {
    @Published public var data = OctopusData()
    @Published public var isLoading = true
    @Published public var lastError: String?
    @Published public var aiQuery = ""
    @Published public var aiResponse: String?
    @Published public var isAskingAI = false
    @AppStorage("menuBarDisplayMode") public var displayMode: MenuBarDisplayMode = .auto
    @AppStorage("usageDisplayMode") public var usageMode: UsageDisplayMode = .halfHourly

    private var pythonBridge: PythonBridge?
    private var refreshTimer: Timer?

    public var menuBarTitle: String {
        switch displayMode {
        case .iconOnly:
            return "⚡"

        case .octopus:
            return "🐙"

        case .rate:
            if let rate = data.rate {
                return String(format: "¥%.0f", rate)
            }
            return "⚡"

        case .auto:
            if let rate = data.rate {
                return String(format: "¥%.0f", rate)
            }
            return "⚡"
        }
    }

    public init() {
        Task { @MainActor in
            self.setupBridge()
            self.startAutoRefresh()
        }
    }

    private func setupBridge() {
        pythonBridge = PythonBridge { [weak self] data in
            Task { @MainActor in self?.handleData(data) }
        }
        pythonBridge?.start()
    }

    private func startAutoRefresh() {
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    private func handleData(_ newData: OctopusData) {
        isLoading = false
        lastError = newData.error

        if let response = newData.response {
            aiResponse = response
            isAskingAI = false
            return
        }

        if newData.error == nil {
            data = newData
        }
    }

    public func askAI(_ question: String) {
        guard !question.isEmpty else { return }
        isAskingAI = true
        aiResponse = nil
        pythonBridge?.sendCommand(["command": "ask", "question": question])
    }

    public func refresh() {
        isLoading = true
        pythonBridge?.sendCommand(["command": "refresh"])
    }

    public func quit() {
        refreshTimer?.invalidate()
        pythonBridge?.stop()
        NSApplication.shared.terminate(nil)
    }
}

// MARK: - Python Bridge

public final class PythonBridge: @unchecked Sendable {
    private var process: Process?
    private var outputPipe: Pipe?
    private var inputPipe: Pipe?
    private let onData: @Sendable (OctopusData) -> Void

    public init(onData: @escaping @Sendable (OctopusData) -> Void) {
        self.onData = onData
    }

    public func start() {
        process = Process()
        outputPipe = Pipe()
        inputPipe = Pipe()

        let serverPath = findServerPath()
        process?.executableURL = URL(fileURLWithPath: serverPath)
        process?.arguments = []
        process?.standardOutput = outputPipe
        process?.standardInput = inputPipe
        process?.standardError = FileHandle.nullDevice

        var env = ProcessInfo.processInfo.environment
        let envFile = NSHomeDirectory() + "/.octopus.env"
        if let contents = try? String(contentsOfFile: envFile, encoding: .utf8) {
            for line in contents.components(separatedBy: .newlines) {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                guard !trimmed.isEmpty, !trimmed.hasPrefix("#") else { continue }
                let parts = trimmed.split(separator: "=", maxSplits: 1)
                if parts.count == 2 {
                    env[String(parts[0]).trimmingCharacters(in: .whitespaces)] =
                        String(parts[1]).trimmingCharacters(in: .whitespaces)
                }
            }
        }
        process?.environment = env

        let handler = self.onData
        outputPipe?.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty { Self.processOutput(data, handler: handler) }
        }

        try? process?.run()
    }

    private static func processOutput(_ data: Data, handler: @escaping @Sendable (OctopusData) -> Void) {
        guard let string = String(data: data, encoding: .utf8) else { return }
        for line in string.components(separatedBy: .newlines) where !line.isEmpty {
            if let octopusData = try? JSONDecoder().decode(OctopusData.self, from: Data(line.utf8)) {
                handler(octopusData)
            }
        }
    }

    private func findServerPath() -> String {
        let paths = [
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/octopus-server",
            "/opt/homebrew/bin/octopus-server",
            "/usr/local/bin/octopus-server",
            "\(NSHomeDirectory())/.local/bin/octopus-server"
        ]
        return paths.first { FileManager.default.fileExists(atPath: $0) } ?? "octopus-server"
    }

    public func sendCommand(_ command: [String: Any]) {
        guard let inputPipe = inputPipe,
              let data = try? JSONSerialization.data(withJSONObject: command) else { return }
        inputPipe.fileHandleForWriting.write(data)
        inputPipe.fileHandleForWriting.write("\n".data(using: .utf8)!)
    }

    public func stop() {
        sendCommand(["command": "quit"])
        process?.terminate()
    }
}

// MARK: - Reusable Components

struct CardView<Content: View>: View {
    let icon: String
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: icon)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.secondary)
            content()
        }
        .padding(10)
        .background(Color.primary.opacity(0.04))
        .cornerRadius(8)
        .padding(.horizontal, 10)
    }
}

struct RateProgressBar: View {
    let progress: Double
    let isOffPeak: Bool

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.secondary.opacity(0.15))
                RoundedRectangle(cornerRadius: 2)
                    .fill(isOffPeak ? Color.cyan : Color.orange)
                    .frame(width: geo.size.width * min(max(progress, 0), 1))
            }
        }
        .frame(height: 4)
    }
}

struct QuickButton: View {
    let text: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(text)
                .font(.system(size: 9))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color.primary.opacity(0.06))
                .cornerRadius(4)
        }
        .buttonStyle(.plain)
    }
}

struct SparklineBar: View {
    let value: Double
    let maxVal: Double
    let isOffPeak: Bool
    let timeLabel: String
    @State private var isHovered = false

    var body: some View {
        RoundedRectangle(cornerRadius: 1)
            .fill(isOffPeak ? Color.cyan.opacity(0.6) : Color.secondary.opacity(0.4))
            .frame(height: maxVal > 0 ? CGFloat(value / maxVal) * 28 + 2 : 2)
            .onHover { hovering in
                isHovered = hovering
            }
            .popover(isPresented: $isHovered, arrowEdge: .bottom) {
                if value > 0.01 {
                    VStack(spacing: 2) {
                        Text(String(format: "%.2f kWh", value))
                            .font(.system(size: 10, weight: .medium))
                        Text(timeLabel)
                            .font(.system(size: 9))
                            .foregroundColor(.secondary)
                    }
                    .padding(6)
                }
            }
    }
}

struct CurvedSeparator: View {
    var body: some View {
        Canvas { context, size in
            var path = Path()
            // Curve that makes content above appear to float
            // Start at top-left, curve down at left edge
            path.move(to: CGPoint(x: 0, y: 0))
            path.addQuadCurve(
                to: CGPoint(x: size.width * 0.15, y: size.height - 2),
                control: CGPoint(x: 0, y: size.height - 2)
            )
            // Flat bottom section
            path.addLine(to: CGPoint(x: size.width * 0.85, y: size.height - 2))
            // Curve down at right edge
            path.addQuadCurve(
                to: CGPoint(x: size.width, y: 0),
                control: CGPoint(x: size.width, y: size.height - 2)
            )
            context.stroke(path, with: .color(.secondary.opacity(0.15)), lineWidth: 1)
        }
        .frame(height: 5)
    }
}

// MARK: - Menu Bar View

public struct MenuBarView: View {
    @EnvironmentObject var state: AppState

    public init() {}

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if state.isLoading && state.data.rate == nil {
                loadingView
            } else if let error = state.lastError {
                errorView(error)
            } else {
                heroSection
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: 8) {
                        usageCard
                        rateCard
                        insightsCard
                        aiCard
                    }
                    .padding(.vertical, 8)
                }
                // Curved separator for floating effect
                CurvedSeparator()
                footer
            }
        }
        .frame(width: 320, height: 750)
        .background(Color(NSColor.windowBackgroundColor))
    }

    // MARK: - Hero Section

    private var heroSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                // Rate display
                VStack(alignment: .leading, spacing: 2) {
                    if let rate = state.data.rate {
                        HStack(spacing: 4) {
                            Image(systemName: "sun.max.fill")
                                .foregroundColor(.orange)
                            Text(String(format: "¥%.1f", rate))
                                .font(.system(size: 22, weight: .semibold, design: .rounded))
                        }
                        Text("Rate per kWh")
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                    }
                }

                Spacer()

                // Balance
                VStack(alignment: .trailing, spacing: 2) {
                    Text(formatBalance(state.data.balance, credit: state.data.balanceIsCredit))
                        .font(.system(size: 16, weight: .semibold, design: .monospaced))
                        .foregroundColor(state.data.balanceIsCredit ? .green : .primary)
                    Text("Balance")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
            }

            // Standing charge
            HStack {
                Text(String(format: "¥%.1f", state.data.rate ?? 0))
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.orange)
                Spacer()
                Text(String(format: "Standing: ¥%.0f/day", state.data.standingCharge))
                    .font(.system(size: 9))
                    .foregroundColor(.secondary)
            }
        }
        .padding(12)
        .background(Color.primary.opacity(0.02))
    }

    // MARK: - Rate Card

    private var rateCard: some View {
        CardView(icon: "chart.bar.fill", title: "RATES") {
            VStack(alignment: .leading, spacing: 6) {
                // Rate row
                HStack {
                    Circle()
                        .fill(Color.orange)
                        .frame(width: 6, height: 6)
                    Text("Rate")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text(String(format: "¥%.1f/kWh", state.data.peakRate ?? state.data.rate ?? 0))
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                }

                // Standing charge
                HStack {
                    Text("Standing charge")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text(String(format: "¥%.0f/day", state.data.standingCharge))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.secondary)
                }
                .padding(.top, 4)
            }
        }
    }


    // MARK: - Usage Card

    private var usageCard: some View {
        CardView(icon: "chart.line.uptrend.xyaxis", title: "USAGE") {
            VStack(alignment: .leading, spacing: 8) {
                // Latest day (may not be "today" due to smart meter delay)
                HStack {
                    Text(formatDataDateLabel(state.data.dataDateLatest, fallback: "Latest"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text(formatKwh(state.data.todayKwh))
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                    Text(formatCost(state.data.todayCost))
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)

                    // Change indicator
                    if state.data.yesterdayKwh > 0 && state.data.todayKwh > 0 {
                        let change = ((state.data.todayKwh / state.data.yesterdayKwh) - 1) * 100
                        Text(String(format: "%@%.0f%%", change >= 0 ? "+" : "", change))
                            .font(.system(size: 9, weight: .medium))
                            .foregroundColor(change < 0 ? .green : .orange)
                    }
                }

                // Previous day
                HStack {
                    Text(formatDataDateLabel(state.data.dataDatePrevious, fallback: "Previous"))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text(formatKwh(state.data.yesterdayKwh))
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                    Text(formatCost(state.data.yesterdayCost))
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }

                // Sparkline with labels
                if !state.data.hourlyUsage.isEmpty || !state.data.halfHourlyUsage.isEmpty {
                    VStack(spacing: 2) {
                        sparkline
                        HStack {
                            Text("-24h")
                            Spacer()
                            Text("-12h")
                            Spacer()
                            Text("Now")
                        }
                        .font(.system(size: 8))
                        .foregroundColor(.secondary)
                    }
                    .padding(.top, 4)
                }
            }
        }
    }

    private var sparkline: some View {
        // Use half-hourly or hourly based on setting
        let useHalfHourly = state.usageMode == .halfHourly && !state.data.halfHourlyUsage.isEmpty
        let values = useHalfHourly ? state.data.halfHourlyUsage : state.data.hourlyUsage
        let maxVal = values.max() ?? 1

        return HStack(alignment: .bottom, spacing: useHalfHourly ? 0.5 : 1) {
            ForEach(Array(values.enumerated()), id: \.offset) { index, value in
                SparklineBar(
                    value: value,
                    maxVal: maxVal,
                    isOffPeak: isOffPeakForIndex(index, count: values.count, useHalfHourly: useHalfHourly),
                    timeLabel: timeLabelForIndex(index, count: values.count, useHalfHourly: useHalfHourly)
                )
            }
        }
        .frame(height: 32)
    }

    private func timeLabelForIndex(_ index: Int, count: Int, useHalfHourly: Bool) -> String {
        // Calculate the time for this bar
        // Data spans last 24-48h, index 0 is oldest
        let slotFromEnd = count - index - 1  // How many slots ago

        if useHalfHourly {
            let hoursAgo = slotFromEnd / 2
            let minute = (slotFromEnd % 2) * 30
            let hour = (24 - hoursAgo) % 24
            return String(format: "%02d:%02d", hour, 30 - minute)
        } else {
            let hour = (24 - slotFromEnd) % 24
            return String(format: "%02d:00", hour)
        }
    }

    private func isOffPeakForIndex(_ index: Int, count: Int, useHalfHourly: Bool) -> Bool {
        // Calculate actual hour for this slot
        let slotsPerDay = useHalfHourly ? 48 : 24
        let slotInDay = index % slotsPerDay

        if useHalfHourly {
            // Off-peak: 23:30 (slot 47) to 05:30 (slots 0-11)
            return slotInDay >= 47 || slotInDay <= 11
        } else {
            // Off-peak: hours 0-5 and 23
            return slotInDay < 6 || slotInDay == 23
        }
    }

    // MARK: - Insights Card

    private var insightsCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("INSIGHTS", systemImage: "lightbulb.fill")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 6) {

                // Monthly projection
                if state.data.monthlyProjection > 0 {
                    HStack(spacing: 4) {
                        Image(systemName: "calendar")
                            .font(.system(size: 10))
                            .foregroundColor(.purple)
                        Text(String(format: "On track for ¥%.0f/month", state.data.monthlyProjection))
                            .font(.system(size: 11, weight: .medium))
                    }
                }

                // Estimated savings (rough calculation)
                if state.data.yesterdayCost > 0 {
                    let standardRate = 30.0  // Average standard rate (yen/kWh)
                    let savingsEstimate = (state.data.yesterdayKwh * standardRate) - state.data.yesterdayCost
                    if savingsEstimate > 0 {
                        HStack(spacing: 4) {
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.system(size: 10))
                                .foregroundColor(.green)
                            Text(String(format: "Saved ~¥%.0f yesterday vs standard", savingsEstimate))
                                .font(.system(size: 11))
                                .foregroundColor(.green)
                        }
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.02))
    }

    // MARK: - AI Card

    private var aiCard: some View {
        CardView(icon: "sparkles", title: "ASK") {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    TextField("Ask about your energy...", text: $state.aiQuery)
                        .textFieldStyle(.plain)
                        .font(.system(size: 11))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 6)
                        .background(Color(NSColor.textBackgroundColor))
                        .cornerRadius(6)
                        .onSubmit { submitAI() }

                    Button(action: submitAI) {
                        if state.isAskingAI {
                            ProgressView()
                                .scaleEffect(0.5)
                                .frame(width: 20, height: 20)
                        } else {
                            Image(systemName: "arrow.up.circle.fill")
                                .font(.system(size: 16))
                                .foregroundColor(.accentColor)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(state.aiQuery.isEmpty || state.isAskingAI)
                }

                // Quick action buttons
                HStack(spacing: 6) {
                    QuickButton(text: "Best time?") {
                        state.aiQuery = "When is the best time to use energy today?"
                        submitAI()
                    }
                    QuickButton(text: "This week?") {
                        state.aiQuery = "How much did I spend this week?"
                        submitAI()
                    }
                    QuickButton(text: "Compare") {
                        state.aiQuery = "How does today compare to yesterday?"
                        submitAI()
                    }
                }

                if let response = state.aiResponse {
                    Text(markdownToAttributed(response))
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)
                }
            }
        }
    }

    private func submitAI() {
        let query = state.aiQuery
        state.aiQuery = ""
        state.askAI(query)
    }

    // MARK: - Footer

    private var footer: some View {
        HStack(spacing: 8) {
            Button(action: { state.refresh() }) {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 10))
                    Text(formatLastUpdated())
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                }
            }
            .buttonStyle(.plain)
            .opacity(state.isLoading ? 0.5 : 1)

            Spacer()

            // Branding + tariff
            HStack(spacing: 4) {
                Button(action: { NSWorkspace.shared.open(URL(string: "https://github.com/abracadabra50/open-octopus")!) }) {
                    HStack(spacing: 2) {
                        Text("🐙")
                            .font(.system(size: 10))
                        Text("Open Octopus")
                            .font(.system(size: 9, weight: .medium))
                            .foregroundColor(.secondary)
                    }
                }
                .buttonStyle(.plain)
                if let tariff = state.data.tariffName {
                    Text("·")
                        .foregroundColor(.secondary)
                    Text(formatTariffName(tariff))
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                }
            }

            Spacer()

            Button(action: { NSWorkspace.shared.open(URL(string: "https://octopusenergy.co.jp/")!) }) {
                Image(systemName: "arrow.up.right.square")
                    .font(.system(size: 11))
            }
            .buttonStyle(.plain)

            // Settings menu
            Menu {
                Section("Menu Bar Display") {
                    ForEach(MenuBarDisplayMode.allCases) { mode in
                        Button(action: { state.displayMode = mode }) {
                            HStack {
                                Text(mode.rawValue)
                                if state.displayMode == mode {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }

                Divider()

                Section("Usage Chart") {
                    ForEach(UsageDisplayMode.allCases) { mode in
                        Button(action: { state.usageMode = mode }) {
                            HStack {
                                Text(mode.rawValue)
                                if state.usageMode == mode {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }
            } label: {
                Image(systemName: "gearshape")
                    .font(.system(size: 11))
            }
            .menuStyle(.borderlessButton)
            .frame(width: 20)

            Button(action: { state.quit() }) {
                Image(systemName: "xmark")
                    .font(.system(size: 10))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
    }

    // MARK: - Loading & Error Views

    private var loadingView: some View {
        VStack(spacing: 12) {
            Spacer()
            ProgressView()
                .scaleEffect(0.8)
            Text("Loading...")
                .font(.system(size: 11))
                .foregroundColor(.secondary)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func errorView(_ error: String) -> some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 24))
                .foregroundColor(.orange)
            Text(error)
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Button("Retry") { state.refresh() }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Formatters

    private func formatBalance(_ amount: Double, credit: Bool) -> String {
        let formatted = String(format: "¥%.0f", amount)
        return credit ? "+\(formatted)" : formatted
    }

    private func formatPower(_ watts: Int) -> String {
        watts >= 1000 ? String(format: "%.1fkW", Double(watts)/1000) : "\(watts)W"
    }

    private func formatCostPerHour(_ watts: Int, rate: Double) -> String {
        let cost = (Double(watts) / 1000) * rate
        return String(format: "¥%.0f/h", cost)
    }

    private func formatTimeRemaining(_ seconds: Int) -> String {
        let h = seconds / 3600
        let m = (seconds % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }

    private func formatKwh(_ kwh: Double) -> String {
        String(format: "%.1f kWh", kwh)
    }

    private func formatCost(_ cost: Double) -> String {
        String(format: "¥%.0f", cost)
    }

    private func formatTime(_ iso: String?) -> String {
        guard let iso = iso else { return "-" }
        let formatter = ISO8601DateFormatter()
        guard let date = formatter.date(from: iso) else { return "-" }
        let tf = DateFormatter()
        tf.dateFormat = "HH:mm"
        return tf.string(from: date)
    }

    private func formatLastUpdated() -> String {
        guard let timestamp = state.data.timestamp else { return "now" }
        let formatter = ISO8601DateFormatter()
        guard let date = formatter.date(from: timestamp) else { return "now" }
        let seconds = Int(Date().timeIntervalSince(date))
        if seconds < 60 { return "now" }
        let minutes = seconds / 60
        return "\(minutes)m ago"
    }

    private func markdownToAttributed(_ text: String) -> AttributedString {
        do {
            return try AttributedString(
                markdown: text,
                options: AttributedString.MarkdownParsingOptions(
                    interpretedSyntax: .inlineOnlyPreservingWhitespace
                )
            )
        } catch {
            return AttributedString(text)
        }
    }

    private func formatTariffName(_ name: String) -> String {
        // Shorten tariff names like "INTELLI-VAR-24-10-29" to "INTELLI-VAR"
        let parts = name.split(separator: "-")
        if parts.count >= 2 {
            return parts.prefix(2).joined(separator: "-")
        }
        return name
    }

    private func calculateRateProgress() -> Double {
        // Estimate progress through current rate period
        // For a 30-min period, calculate how far through we are
        let totalSeconds = 1800.0  // 30 minutes typical for Agile
        let elapsed = totalSeconds - Double(state.data.rateEndsInSeconds)
        return max(0, min(1, elapsed / totalSeconds))
    }

    private func formatSessionDate(_ iso: String) -> String {
        let formatter = ISO8601DateFormatter()
        guard let date = formatter.date(from: iso) else { return "-" }
        let df = DateFormatter()
        df.dateFormat = "MMM d"
        return df.string(from: date)
    }

    private func formatDataDateLabel(_ dateStr: String?, fallback: String) -> String {
        guard let dateStr = dateStr else { return fallback }

        // Parse YYYY-MM-DD format
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        guard let date = df.date(from: dateStr) else { return fallback }

        let calendar = Calendar.current
        let today = calendar.startOfDay(for: Date())
        let yesterday = calendar.date(byAdding: .day, value: -1, to: today)!

        if calendar.isDate(date, inSameDayAs: today) {
            return "Today"
        } else if calendar.isDate(date, inSameDayAs: yesterday) {
            return "Yesterday"
        } else {
            // Show date like "Dec 28"
            let displayDf = DateFormatter()
            displayDf.dateFormat = "MMM d"
            return displayDf.string(from: date)
        }
    }

    private func formatDuration(_ minutes: Int) -> String {
        let h = minutes / 60
        let m = minutes % 60
        if h > 0 {
            return "\(h)h \(m)m"
        }
        return "\(m)m"
    }
}
