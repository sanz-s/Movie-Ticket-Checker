#!/usr/bin/env bash
# Install / uninstall the ticket tracker as a background service.
#   macOS : LaunchAgent  (~/Library/LaunchAgents/com.user.doomsday-tracker.plist)
#   Linux : systemd user service (~/.config/systemd/user/doomsday-tracker.service)
#
#   ./install.sh               install (or update) and start
#   ./install.sh --uninstall   stop and remove the service (keeps your config/data)
#   PORT=9000 ./install.sh     use another dashboard port (default 8765)
set -euo pipefail

APP="doomsday-tracker"
LABEL="com.user.$APP"
PORT="${PORT:-8765}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES=(district_tracker.py tracker_server.py seatmap.py tracker_ui.html)
OS="$(uname -s)"

if [[ "$OS" == "Darwin" ]]; then
  DATA="${TRACKER_HOME:-$HOME/Library/Application Support/$APP}"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  LOG="$HOME/Library/Logs/$APP.log"
elif [[ "$OS" == "Linux" ]]; then
  DATA="${TRACKER_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/$APP}"
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  UNIT="$UNIT_DIR/$APP.service"
else
  echo "Unsupported OS: $OS (macOS and Linux only)" >&2; exit 1
fi

uninstall() {
  if [[ "$OS" == "Darwin" ]]; then
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
  else
    systemctl --user disable --now "$APP.service" 2>/dev/null || true
    rm -f "$UNIT"; systemctl --user daemon-reload 2>/dev/null || true
  fi
  echo "Service removed. Your config and data are still in: $DATA"
}

if [[ "${1:-}" == "--uninstall" ]]; then uninstall; exit 0; fi

PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || { echo "python3 not found - install Python 3.8+ first" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || { echo "Python 3.8+ required ($("$PY" -V))" >&2; exit 1; }

# Copy the app next to its data. (On macOS, background jobs often can't read
# files in ~/Desktop or ~/Documents, so we never run it from the repo.)
mkdir -p "$DATA"
for f in "${FILES[@]}"; do cp "$SRC/$f" "$DATA/"; done
echo "Installed app files to: $DATA"

if [[ "$OS" == "Darwin" ]]; then
  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DATA/tracker_server.py</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$DATA</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Started LaunchAgent $LABEL   (log: $LOG)"
else
  if ! command -v systemctl >/dev/null || ! systemctl --user show-environment >/dev/null 2>&1; then
    echo
    echo "systemd user services aren't available here. Run it manually instead:"
    echo "  cd \"$DATA\" && $PY tracker_server.py --port $PORT"
    exit 0
  fi
  command -v notify-send >/dev/null || \
    echo "Note: 'notify-send' not found - install libnotify (e.g. sudo apt install libnotify-bin) for desktop notifications."
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT" <<EOF
[Unit]
Description=Movie ticket tracker (District / TicketNew) with dashboard on port $PORT
After=network-online.target graphical-session.target

[Service]
WorkingDirectory=$DATA
ExecStart=$PY $DATA/tracker_server.py --port $PORT
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
  # let the service reach your desktop session for notifications
  systemctl --user import-environment DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR 2>/dev/null || true
  systemctl --user daemon-reload
  systemctl --user enable --now "$APP.service"
  systemctl --user restart "$APP.service"
  echo "Started systemd user service $APP   (log: journalctl --user -u $APP -f)"
fi

echo
echo "Dashboard: http://127.0.0.1:$PORT"
