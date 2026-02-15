# 🐙 Open Octopus Japan

> **Unofficial** open-source toolkit for [Octopus Energy Japan](https://octopusenergy.co.jp) customers.
> Not affiliated with or endorsed by Octopus Energy.
> 
> *Adapted from the original UK version. Some UK-only features are hidden.*

<img src="docs/menubar-screenshot.png" width="300" alt="Open Octopus Menu Bar App">

## What's Included

| Component | Description |
|-----------|-------------|
| **Menu Bar App** | Native macOS app with live rates, usage, insights |
| **CLI Tools** | Terminal commands for quick access to your data |
| **TUI** | Interactive terminal dashboard |
| **AI Assistant** | Ask questions about your energy usage |
| **Alerts** | Notifications for rate changes, charging, sessions *(coming soon)* |

## Features

### Menu Bar App (macOS)
- Live rate display with countdown to off-peak
- Smart charging status with golden indicator when EV is charging
- Usage sparkline with off-peak hour highlighting
- Rate comparison (peak vs off-peak savings)
- Monthly cost projection
- AI chat with quick action buttons

### CLI Tools
```bash
octopus rate       # Current electricity rate
octopus account    # Account balance
octopus usage      # Consumption data
octopus dispatch   # EV charging schedule
octopus power      # Live power (if available)
octopus sessions   # Saving sessions
```

### TUI (Terminal UI)
```bash
octopus tui        # Interactive dashboard
```

### AI Assistant
```bash
octopus-ask "What's the best time to run my dishwasher?"
octopus-ask "How much did I spend this week?"
octopus-ask "Compare my usage to last month"
```

## Installation

```bash
pip install open-octopus
```

### Configuration

Set your Octopus Energy Japan credentials:
```bash
export OCTOPUS_EMAIL="your-email@example.com"
export OCTOPUS_PASSWORD="your-password"
export ANTHROPIC_API_KEY="sk-ant-xxxxx"    # For AI features (optional)
```

Or create `~/.octopus.env`:
```bash
OCTOPUS_EMAIL=your-email@example.com
OCTOPUS_PASSWORD=your-password
ANTHROPIC_API_KEY=sk-ant-xxxxx
```

### Menu Bar App (macOS)

```bash
git clone https://github.com/abracadabra50/open-octopus.git
cd open-octopus
xcodebuild -workspace OctopusMenuBar.xcworkspace -scheme OctopusMenuBar build
open ~/Library/Developer/Xcode/DerivedData/OctopusMenuBar-*/Build/Products/Debug/OctopusMenuBar.app
```

## Supported Tariffs (Japan)

- グリーンオクトパス (Green Octopus)
- シンプルオクトパス (Simple Octopus)
- Other Japan tariffs

## Roadmap

- [ ] **Alerts** - macOS notifications for:
  - Off-peak rate starting/ending
  - EV dispatch starting/ending
  - Saving sessions
  - Low balance warning
- [ ] **Widgets** - macOS desktop widgets
- [ ] **Gas support** - Full gas meter integration
- [ ] **Historical charts** - Weekly/monthly usage graphs

## License

MIT

## Credits

Built with Python and SwiftUI. AI powered by [Claude](https://anthropic.com).
