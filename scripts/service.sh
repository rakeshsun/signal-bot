#!/usr/bin/env bash
# Run signal-bot in the background on macOS (launchd): starts at login, restarts if it stops.
#   ./scripts/service.sh install   # write the launchd file and start
#   ./scripts/service.sh start|stop|restart|status|logs|uninstall
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
LABEL="com.signalbot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$REPO/logs/signalbot.log"
DOMAIN="gui/$(id -u)"

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>ProgramArguments</key><array>
    <!-- caffeinate -i keeps the Mac from idle-sleeping while the bot runs -->
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$REPO/.venv/bin/signalbot</string><string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict></plist>
PL
}

case "${1:-status}" in
  install)   write_plist; launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
             launchctl bootstrap "$DOMAIN" "$PLIST"; echo "Installed and started. Logs: $LOG" ;;
  start)     launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null || launchctl kickstart "$DOMAIN/$LABEL"; echo started ;;
  stop)      launchctl bootout "$DOMAIN/$LABEL" && echo stopped ;;
  restart)   launchctl kickstart -k "$DOMAIN/$LABEL" && echo restarted ;;
  status)    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
               launchctl print "$DOMAIN/$LABEL" | grep -E "state =|pid =|last exit code" ; else echo "not running"; fi ;;
  logs)      tail -n 50 -f "$LOG" ;;
  uninstall) launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true; rm -f "$PLIST"; echo "removed" ;;
  *) echo "usage: $0 install|start|stop|restart|status|logs|uninstall"; exit 1 ;;
esac
