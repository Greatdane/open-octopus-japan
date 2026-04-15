import SwiftUI
import AppKit

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
    var fca: Double                // Fuel cost adjustment (yen/kWh)
    var rel: Double                // Renewable energy levy (yen/kWh)
    var tierRates: [String: Double] // Tiered rates e.g. {"0-15kWh": 0.0, "15-120kWh": 20.08}
    var billingCycleDay: Int       // Day of month billing resets (from agreement)
    var billingCycleKwh: Double    // kWh used so far this billing cycle
    var billingCycleCost: Double   // Cost so far this billing cycle (yen)
    var billingDaysRemaining: Int  // Days until next billing date
    var halfHourlyUsage: [Double]  // 48 slots for last 24h
    var dataDateLatest: String?   // Actual date of "today" data
    var dataDatePrevious: String? // Actual date of "yesterday" data

    enum CodingKeys: String, CodingKey {
        case timestamp, rate, balance, error, response, fca, rel
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
        case tierRates = "tier_rates"
        case billingCycleDay = "billing_cycle_day"
        case billingCycleKwh = "billing_cycle_kwh"
        case billingCycleCost = "billing_cycle_cost"
        case billingDaysRemaining = "billing_days_remaining"
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
        fca = 0
        rel = 0
        tierRates = [:]
        billingCycleDay = 1
        billingCycleKwh = 0
        billingCycleCost = 0
        billingDaysRemaining = 0
        halfHourlyUsage = []
    }
}

// MARK: - History Data

public struct TierBreakdown: Codable, Sendable {
    var tier: String    // e.g. "0-15kWh"
    var kwh: Double
    var rate: Double    // effective rate (base + FCA + REL)
    var cost: Double
}

public struct HistoryEntry: Codable, Identifiable, Sendable {
    var date: String
    var kwh: Double
    var cost: Double
    var tierBreakdown: [TierBreakdown]?

    public var id: String { date }

    enum CodingKeys: String, CodingKey {
        case date, kwh, cost
        case tierBreakdown = "tier_breakdown"
    }
}

public struct HistoryResponse: Codable, Sendable {
    var history: [HistoryEntry]?
    var error: String?
}

// MARK: - Tier Colours

/// Consistent colours for consumption tiers across rates card and history
func tierColor(for index: Int) -> Color {
    let colors: [Color] = [
        .green,          // Tier 1 (free/cheapest)
        .yellow,         // Tier 2
        .orange,         // Tier 3
        .red,            // Tier 4 (most expensive)
    ]
    return colors[min(index, colors.count - 1)]
}

func tierColorForKey(_ key: String, allKeys: [String]) -> Color {
    let sorted = allKeys.sorted { a, b in
        let aStart = Double(a.split(separator: "-").first ?? "0") ?? 0
        let bStart = Double(b.split(separator: "-").first ?? "0") ?? 0
        return aStart < bStart
    }
    let index = sorted.firstIndex(of: key) ?? 0
    return tierColor(for: index)
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
    @Published public var historyEntries: [HistoryEntry] = []
    @Published public var showHistory = true
    @Published public var showAI = false
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
            self.observeWakeFromSleep()
        }
    }

    private func setupBridge() {
        pythonBridge = PythonBridge(
            onData: { [weak self] data in
                Task { @MainActor in self?.handleData(data) }
            },
            onHistory: { [weak self] response in
                Task { @MainActor in
                    self?.historyEntries = response.history ?? []
                }
            }
        )
        pythonBridge?.start()

        // Auto-fetch history since it's shown by default
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            self?.fetchHistory()
        }
    }

    private func observeWakeFromSleep() {
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.pythonBridge?.restart()
                // Give the process a moment to spawn before sending commands
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                    self?.refresh()
                    self?.fetchHistory()
                }
            }
        }
    }

    public func fetchHistory(days: Int = 30) {
        pythonBridge?.sendCommand(["command": "history", "days": days])
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
    private let onHistory: @Sendable (HistoryResponse) -> Void
    private var isStopping = false

    public init(
        onData: @escaping @Sendable (OctopusData) -> Void,
        onHistory: @escaping @Sendable (HistoryResponse) -> Void = { _ in }
    ) {
        self.onData = onData
        self.onHistory = onHistory
    }

    public func start() {
        isStopping = false
        launch()
    }

    public func restart() {
        isStopping = true
        process?.terminate()
        isStopping = false
        launch()
    }

    private func launch() {
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

        let dataHandler = self.onData
        let historyHandler = self.onHistory
        outputPipe?.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty { Self.processOutput(data, dataHandler: dataHandler, historyHandler: historyHandler) }
        }

        process?.terminationHandler = { [weak self] _ in
            guard let self = self, !self.isStopping else { return }
            // Process died unexpectedly — respawn after a short delay to avoid tight crash loops
            DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in
                self?.launch()
            }
        }

        try? process?.run()
    }

    private static func processOutput(
        _ data: Data,
        dataHandler: @escaping @Sendable (OctopusData) -> Void,
        historyHandler: @escaping @Sendable (HistoryResponse) -> Void
    ) {
        guard let string = String(data: data, encoding: .utf8) else { return }
        for line in string.components(separatedBy: .newlines) where !line.isEmpty {
            let lineData = Data(line.utf8)
            // Try history response first (has "history" key)
            if let historyResp = try? JSONDecoder().decode(HistoryResponse.self, from: lineData),
               historyResp.history != nil {
                historyHandler(historyResp)
            } else if let octopusData = try? JSONDecoder().decode(OctopusData.self, from: lineData) {
                dataHandler(octopusData)
            }
        }
    }

    private func findServerPath() -> String {
        let home = NSHomeDirectory()
        let paths = [
            "\(home)/open-octopus-japan/venv/bin/octopus-server",
            "/opt/homebrew/bin/octopus-server",
            "/usr/local/bin/octopus-server",
            "\(home)/.local/bin/octopus-server",
            "/Library/Frameworks/Python.framework/Versions/3.14/bin/octopus-server",
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/octopus-server",
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
        isStopping = true
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
    let timeLabel: String
    @State private var isHovered = false

    var body: some View {
        RoundedRectangle(cornerRadius: 1)
            .fill(Color.orange.opacity(0.5))
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
                        if state.showHistory {
                            historyCard
                        }
                        insightsCard
                        if state.showAI {
                            aiCard
                        }
                    }
                    .padding(.vertical, 8)
                }
                // Curved separator for floating effect
                CurvedSeparator()
                footer
            }
        }
        .frame(width: 320)
        .frame(maxHeight: 700)
        .fixedSize(horizontal: false, vertical: true)
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
                        if state.data.billingCycleKwh > 0 {
                            Text(String(format: "%.0f kWh this cycle", state.data.billingCycleKwh))
                                .font(.system(size: 9))
                                .foregroundColor(.secondary.opacity(0.7))
                        }
                    }
                }

                Spacer()

                // Latest day usage
                VStack(alignment: .trailing, spacing: 2) {
                    HStack(spacing: 4) {
                        Image(systemName: "bolt.fill")
                            .foregroundColor(.orange)
                        Text(formatKwh(state.data.todayKwh))
                            .font(.system(size: 22, weight: .semibold, design: .rounded))
                    }
                    Text("Usage today")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    Text(formatCost(state.data.todayCost))
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(.secondary.opacity(0.7))
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
                // Tiered rates (if available)
                if !state.data.tierRates.isEmpty {
                    let allKeys = Array(state.data.tierRates.keys)
                    let sortedTiers = state.data.tierRates.sorted { a, b in
                        let aStart = Double(a.key.split(separator: "-").first ?? "0") ?? 0
                        let bStart = Double(b.key.split(separator: "-").first ?? "0") ?? 0
                        return aStart < bStart
                    }
                    ForEach(sortedTiers, id: \.key) { tier, rate in
                        HStack(spacing: 6) {
                            Circle()
                                .fill(tierColorForKey(tier, allKeys: allKeys))
                                .frame(width: 6, height: 6)
                            Text(tier)
                                .font(.system(size: 10))
                                .foregroundColor(.secondary)
                            Spacer()
                            Text(String(format: "¥%.2f/kWh", rate))
                                .font(.system(size: 10, design: .monospaced))
                        }
                    }
                } else {
                    // Flat rate fallback
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
                }

                Divider()

                // Adjustments
                if state.data.fca > 0 || state.data.rel > 0 {
                    HStack {
                        Text("FCA + REL")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                        Spacer()
                        Text(String(format: "+¥%.2f/kWh", state.data.fca + state.data.rel))
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundColor(.secondary)
                    }
                }

                // Standing charge
                HStack {
                    Text("Standing charge")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text(String(format: "¥%.1f/day", state.data.standingCharge))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.secondary)
                }
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

    // MARK: - Insights Card

    private var insightsCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("BILLING CYCLE", systemImage: "calendar")
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 6) {
                // Cycle cost so far
                if state.data.billingCycleCost > 0 {
                    HStack {
                        Text("This cycle so far")
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                        Spacer()
                        Text(String(format: "¥%.0f", state.data.billingCycleCost))
                            .font(.system(size: 11, weight: .semibold))
                    }

                    HStack {
                        Text(String(format: "%.0f kWh used", state.data.billingCycleKwh))
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                        Spacer()
                        if state.data.billingDaysRemaining > 0 {
                            Text(String(format: "%d days left", state.data.billingDaysRemaining))
                                .font(.system(size: 10))
                                .foregroundColor(.secondary)
                        }
                    }

                    Divider()

                    // Projected full month
                    if state.data.monthlyProjection > 0 {
                        HStack {
                            Text("Projected bill")
                                .font(.system(size: 11))
                                .foregroundColor(.secondary)
                            Spacer()
                            Text(String(format: "¥%.0f", state.data.monthlyProjection))
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundColor(.primary)
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

    // MARK: - History View

    private var historyCard: some View {
        CardView(icon: "clock.arrow.circlepath", title: "HISTORY") {
            VStack(alignment: .leading, spacing: 6) {
                if state.historyEntries.isEmpty {
                    Text("No history yet. Data is logged on each refresh.")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                } else {
                    // Bar chart of recent days
                    let entries = Array(state.historyEntries.prefix(14))
                    let maxKwh = entries.map(\.kwh).max() ?? 1

                    let allTierKeys = Array(state.data.tierRates.keys)

                    ForEach(entries) { entry in
                        HStack(spacing: 6) {
                            Text(formatHistoryDate(entry.date))
                                .font(.system(size: 9, design: .monospaced))
                                .foregroundColor(.secondary)
                                .frame(width: 45, alignment: .leading)

                            // Segmented bar coloured by tier
                            GeometryReader { geo in
                                let totalWidth = max(2, geo.size.width * CGFloat(entry.kwh / maxKwh))
                                HStack(spacing: 0) {
                                    if let tiers = entry.tierBreakdown, !tiers.isEmpty, entry.kwh > 0 {
                                        ForEach(Array(tiers.enumerated()), id: \.offset) { idx, tb in
                                            let segWidth = totalWidth * CGFloat(tb.kwh / entry.kwh)
                                            RoundedRectangle(cornerRadius: idx == 0 ? 2 : 0)
                                                .fill(tierColorForKey(tb.tier, allKeys: allTierKeys).opacity(0.7))
                                                .frame(width: max(1, segWidth))
                                        }
                                    } else {
                                        RoundedRectangle(cornerRadius: 2)
                                            .fill(Color.orange.opacity(0.5))
                                            .frame(width: totalWidth)
                                    }
                                    Spacer(minLength: 0)
                                }
                            }
                            .frame(height: 10)

                            Text(String(format: "%.1f kWh", entry.kwh))
                                .font(.system(size: 9, design: .monospaced))
                                .foregroundColor(.secondary)
                                .frame(width: 55, alignment: .trailing)

                            Text(String(format: "¥%.0f", entry.cost))
                                .font(.system(size: 9, design: .monospaced))
                                .frame(width: 40, alignment: .trailing)
                        }
                    }

                    // Summary
                    let totalKwh = entries.map(\.kwh).reduce(0, +)
                    let totalCost = entries.map(\.cost).reduce(0, +)
                    let avgKwh = totalKwh / Double(entries.count)

                    Divider()
                    HStack {
                        Text(String(format: "Avg: %.1f kWh/day", avgKwh))
                            .font(.system(size: 9))
                            .foregroundColor(.secondary)
                        Spacer()
                        Text(String(format: "Total: ¥%.0f", totalCost))
                            .font(.system(size: 9, weight: .medium))
                    }
                }
            }
        }
    }

    private func formatHistoryDate(_ dateStr: String) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateStr) else { return dateStr }
        formatter.dateFormat = "M/d (E)"
        formatter.locale = Locale(identifier: "ja_JP")
        return formatter.string(from: date)
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

            // Branding
            Button(action: { NSWorkspace.shared.open(URL(string: "https://github.com/Greatdane/open-octopus-japan")!) }) {
                HStack(spacing: 2) {
                    Text("🐙")
                        .font(.system(size: 10))
                    Text("Open Octopus Japan")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(.secondary)
                }
            }
            .buttonStyle(.plain)

            Spacer()

            Button(action: {
                state.showHistory.toggle()
                if state.showHistory && state.historyEntries.isEmpty {
                    state.fetchHistory()
                }
            }) {
                Image(systemName: state.showHistory ? "clock.fill" : "clock")
                    .font(.system(size: 11))
                    .foregroundColor(state.showHistory ? .accentColor : .secondary)
            }
            .buttonStyle(.plain)
            .help("Usage History")

            Button(action: { state.showAI.toggle() }) {
                Image(systemName: state.showAI ? "sparkles" : "sparkles")
                    .font(.system(size: 11))
                    .foregroundColor(state.showAI ? .accentColor : .secondary)
            }
            .buttonStyle(.plain)
            .help("AI Assistant")

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

        let displayDf = DateFormatter()
        displayDf.dateFormat = "MMM d"
        let dateLabel = displayDf.string(from: date)

        if calendar.isDate(date, inSameDayAs: today) {
            return "Today (\(dateLabel))"
        } else if calendar.isDate(date, inSameDayAs: yesterday) {
            return "Yesterday (\(dateLabel))"
        } else {
            return dateLabel
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
