# 🐙 Open Octopus Japan

> **Unofficial** open-source toolkit for [Octopus Energy Japan](https://octopusenergy.co.jp) customers.
> Not affiliated with or endorsed by Octopus Energy.

<img src="docs/menubar-screenshot.png" width="300" alt="Open Octopus Menu Bar App">

## What's Included

| Component | Description |
|-----------|-------------|
| **Menu Bar App** | Native macOS app with live rates, usage, insights |
| **CLI Tools** | Terminal commands for quick access to your data |
| **TUI** | Interactive terminal dashboard |
| **AI Assistant** | Ask questions about your energy usage |
| **Alerts** | Notifications for rate changes *(coming soon)* |

## Features

### Menu Bar App (macOS)
- Live electricity rate display
- Account balance status
- Quick usage overview (today vs yesterday)
- Monthly cost projection
- AI chat with quick action buttons

### CLI Tools
```bash
octopus account    # Account balance and details
octopus usage      # Daily consumption (last 7 days)
octopus usage -d 30                          # Last 30 days
octopus usage --start 2026-02-15 --end 2026-03-01  # Date range
octopus usage --start 2026-02-15             # From date to today
octopus status     # Quick overview of balance and current rate
octopus tui        # Interactive terminal dashboard
```

### AI Assistant (octopus-ask)
```bash
octopus-ask "What's my balance?"
octopus-ask "How much did I use yesterday?"
octopus-ask "What's my electricity rate?"
```

## Installation

```bash
pip install open-octopus
```

### Configuration

Set your Octopus Energy Japan credentials using email and password:
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
git clone https://github.com/Greatdane/open-octopus-japan.git
cd open-octopus-japan
xcodebuild -workspace OctopusMenuBar.xcworkspace -scheme OctopusMenuBar build
open ~/Library/Developer/Xcode/DerivedData/OctopusMenuBar-*/Build/Products/Debug/OctopusMenuBar.app
```

## Supported Tariffs (Japan)

- グリーンオクトパス (Green Octopus)
- シンプルオクトパス (Simple Octopus)
- Other Japan electricity tariffs

## Roadmap

- [ ] **Alerts** - macOS notifications for:
  - Low balance warning
  - Usage breaking previous records
- [ ] **Widgets** - macOS desktop widgets
- [ ] **Historical charts** - Weekly/monthly usage graphs

## License

MIT

## Credits

Built with Python and SwiftUI. AI powered by [Claude](https://anthropic.com).
