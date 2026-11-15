#!/bin/sh
# Removes what install.sh put on the system. Your logs and settings stay.
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user, not root. It calls sudo itself." >&2
    exit 1
fi

echo "==> Removing hondalog"
sudo rm -f /usr/local/bin/hondalog /usr/local/bin/hondalog-sim \
    /usr/local/bin/hondalog-firstcontact /usr/local/bin/hondalog-map \
    /usr/local/bin/hondalog-log /usr/local/bin/hondalog-bb \
    /usr/local/bin/hondalog-bbsim /usr/local/bin/hondalog-tune
sudo rm -f /usr/local/share/applications/hondalog.desktop
sudo rm -f /usr/local/share/icons/hicolor/scalable/apps/hondalog.svg
sudo rm -f /usr/local/share/icons/hicolor/128x128/apps/hondalog.png
sudo rm -f /etc/udev/rules.d/99-hondalog.rules
sudo udevadm control --reload-rules || true
sudo rm -rf /opt/hondalog
command -v update-desktop-database >/dev/null 2>&1 && sudo update-desktop-database /usr/local/share/applications || true

echo
echo "Removed. Left alone on purpose:"
echo "  * your drives:    ~/hondalog/hondalog.db   (one SQLite file; -wal and -shm beside it while open)"
echo "  * your files:     ~/hondalog/logs          (exports, reports, black box captures)"
echo "  * your settings:  ~/.config/hondalog   (address map, window settings)"
echo "  * the Debian packages (python3-pyside6.*, python3-pyqtgraph, python3-serial)"
echo "  * your membership of the dialout group (undo: sudo gpasswd -d $(id -un) dialout)"
