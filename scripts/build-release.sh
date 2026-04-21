#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI_DIR="$ROOT/cli"
MACOS_DIR="$ROOT/macos"
BUILD_DIR="$ROOT/build"
VERSION="${1:-1.0.0}"

echo "=== Building Open Octopus Japan v${VERSION} ==="
echo ""

# Clean previous build
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# -------------------------------------------------------------------
# Step 1: Build octopus-server with PyInstaller
# -------------------------------------------------------------------
echo "=== Step 1/5: Building octopus-server with PyInstaller ==="
cd "$CLI_DIR"

python3 -m venv "$BUILD_DIR/buildenv"
source "$BUILD_DIR/buildenv/bin/activate"

pip install --upgrade pip
pip install -e ".[agent]"
pip install pyinstaller

pyinstaller \
  --name octopus-server \
  --noconfirm \
  --onedir \
  --console \
  --distpath "$BUILD_DIR/pyinstaller-dist" \
  --workpath "$BUILD_DIR/pyinstaller-work" \
  --specpath "$BUILD_DIR" \
  --hidden-import open_octopus \
  --hidden-import open_octopus.menubar_server \
  --hidden-import open_octopus.client \
  --hidden-import open_octopus.models \
  --hidden-import open_octopus.agent \
  --collect-all httpx \
  --collect-all httpcore \
  --collect-all anyio \
  --collect-all sniffio \
  --collect-all h11 \
  --collect-all certifi \
  --collect-all rich \
  --collect-all typer \
  --exclude-module tkinter \
  --exclude-module test \
  --exclude-module unittest \
  --exclude-module xmlrpc \
  --exclude-module lib2to3 \
  --exclude-module pydoc \
  server_entry.py

deactivate

# Rename the binary from server_entry to octopus-server
mv "$BUILD_DIR/pyinstaller-dist/octopus-server/server_entry" \
   "$BUILD_DIR/pyinstaller-dist/octopus-server/octopus-server" 2>/dev/null || true
echo "   PyInstaller output: $BUILD_DIR/pyinstaller-dist/octopus-server/"
echo ""

# -------------------------------------------------------------------
# Step 2: Build the Swift app (Release)
# -------------------------------------------------------------------
echo "=== Step 2/5: Building macOS app with Xcode ==="
cd "$MACOS_DIR"

xcodebuild \
  -project OctopusMenuBar.xcodeproj \
  -scheme OctopusMenuBar \
  -configuration Release \
  -derivedDataPath "$BUILD_DIR/xcode-derived" \
  CODE_SIGN_IDENTITY="-" \
  CODE_SIGNING_REQUIRED=NO \
  CURRENT_PROJECT_VERSION="$VERSION" \
  MARKETING_VERSION="$VERSION" \
  build

APP_PATH="$BUILD_DIR/xcode-derived/Build/Products/Release/OctopusMenuBar.app"
echo "   App built: $APP_PATH"
echo ""

# -------------------------------------------------------------------
# Step 3: Bundle PyInstaller output into .app
# -------------------------------------------------------------------
echo "=== Step 3/5: Bundling Python server into .app ==="
RESOURCES="$APP_PATH/Contents/Resources"
mkdir -p "$RESOURCES/PythonServer"
cp -R "$BUILD_DIR/pyinstaller-dist/octopus-server/"* "$RESOURCES/PythonServer/"
chmod +x "$RESOURCES/PythonServer/octopus-server"

BUNDLE_SIZE=$(du -sh "$APP_PATH" | cut -f1)
echo "   Bundle size: $BUNDLE_SIZE"
echo ""

# -------------------------------------------------------------------
# Step 4: Ad-hoc re-sign
# -------------------------------------------------------------------
echo "=== Step 4/5: Ad-hoc code signing ==="
codesign --force --deep --sign - "$APP_PATH"
echo "   Signed."
echo ""

# -------------------------------------------------------------------
# Step 5: Create DMG
# -------------------------------------------------------------------
echo "=== Step 5/5: Creating DMG ==="
DMG_NAME="OpenOctopusJapan-${VERSION}-arm64.dmg"

# Create a staging folder with the app + a symlink to Applications
STAGING="$BUILD_DIR/dmg-staging"
mkdir -p "$STAGING"
cp -R "$APP_PATH" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

hdiutil create \
  -volname "Open Octopus Japan" \
  -srcfolder "$STAGING" \
  -ov -format UDZO \
  "$BUILD_DIR/$DMG_NAME"

DMG_SIZE=$(du -sh "$BUILD_DIR/$DMG_NAME" | cut -f1)
echo ""
echo "=== Build complete ==="
echo "   DMG: $BUILD_DIR/$DMG_NAME ($DMG_SIZE)"
echo ""
echo "To create a GitHub release:"
echo "   gh release create v${VERSION} '$BUILD_DIR/$DMG_NAME' --title 'v${VERSION}' --notes-file -"
