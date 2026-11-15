#!/bin/sh
# hondalog installer for Debian 13 "trixie". Run as your normal user:
#     ./install.sh
# It calls sudo for the system parts. Safe to run again.
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user, not root. It calls sudo itself." >&2
    exit 1
fi

SRC=$(cd "$(dirname "$0")" && pwd)
PREFIX=/opt/hondalog
ME=$(id -un)

# Debian packages. python3-pyqtgraph does not list PySide6 as a Qt binding, so
# apt also pulls in PyQt5 as its dependency; hondalog still runs on PySide6.
REQUIRED="python3 python3-numpy python3-serial python3-pyqtgraph
          python3-pyside6.qtcore python3-pyside6.qtgui python3-pyside6.qtwidgets"
# Nice to have; each is tried on its own and may be missing.
OPTIONAL="python3-pyside6.qtsvg python3-pyside6.qtopenglwidgets python3-pyside6.qttest
          qt6-qpa-plugins qt6-svg-plugins libxcb-cursor0 python3-pytest"

deps_ok() {
    PYTHONPATH="$PREFIX" PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/hondalog-pyc "$1" -c '
import hondalog.ui, PySide6.QtWidgets, pyqtgraph, serial, numpy
assert pyqtgraph.Qt.QT_LIB == "PySide6", pyqtgraph.Qt.QT_LIB' >/dev/null 2>&1
}

echo "==> Installing Debian packages (sudo)"
sudo apt-get update
APT_OK=1
# shellcheck disable=SC2086
sudo apt-get install -y $REQUIRED || APT_OK=0
for pkg in $OPTIONAL; do
    sudo apt-get install -y "$pkg" >/dev/null 2>&1 || echo "    (optional package $pkg not installed)"
done

echo "==> Copying hondalog to $PREFIX"
sudo mkdir -p "$PREFIX"
sudo rm -rf "$PREFIX/hondalog" "$PREFIX/bin" "$PREFIX/tests" "$PREFIX/tools"
sudo cp -r "$SRC/hondalog" "$SRC/bin" "$SRC/tests" "$SRC/tools" "$SRC/README.md" \
           "$SRC/pyproject.toml" "$SRC/uninstall.sh" "$PREFIX/"
sudo find "$PREFIX" -name __pycache__ -prune -exec rm -rf {} +
sudo chmod -R a+rX "$PREFIX"

if [ "$APT_OK" -eq 1 ] && deps_ok python3; then
    sudo rm -rf "$PREFIX/.venv"
    echo "==> Python libraries: using the Debian packages"
else
    echo "==> Debian packages are not enough here; building a venv in $PREFIX/.venv"
    sudo apt-get install -y python3-venv python3-pip libegl1 libxkbcommon-x11-0 libxcb-icccm4 \
        libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libfontconfig1 libdbus-1-3
    sudo python3 -m venv "$PREFIX/.venv"
    sudo "$PREFIX/.venv/bin/pip" install --upgrade pip
    sudo "$PREFIX/.venv/bin/pip" install PySide6-Essentials pyqtgraph pyserial numpy
    if ! deps_ok "$PREFIX/.venv/bin/python"; then
        echo "ERROR: the Python libraries still do not import. See the output above." >&2
        exit 1
    fi
fi

echo "==> Commands: /usr/local/bin/hondalog, /usr/local/bin/hondalog-sim"
sudo ln -sf "$PREFIX/bin/hondalog" /usr/local/bin/hondalog
sudo ln -sf "$PREFIX/bin/hondalog-sim" /usr/local/bin/hondalog-sim
sudo ln -sf "$PREFIX/bin/hondalog-bb" /usr/local/bin/hondalog-bb
sudo ln -sf "$PREFIX/bin/hondalog-bbsim" /usr/local/bin/hondalog-bbsim

echo "==> udev rule: /dev/hondalog for the FTDI cable (0403:6001)"
sudo install -m 644 "$SRC/packaging/99-hondalog.rules" /etc/udev/rules.d/99-hondalog.rules
sudo udevadm control --reload-rules || true
sudo udevadm trigger --subsystem-match=tty || true

echo "==> Launcher and icon"
sudo install -D -m 644 "$SRC/packaging/hondalog.desktop" /usr/local/share/applications/hondalog.desktop
sudo install -D -m 644 "$SRC/hondalog/data/hondalog.svg" /usr/local/share/icons/hicolor/scalable/apps/hondalog.svg
sudo install -D -m 644 "$SRC/hondalog/data/hondalog.png" /usr/local/share/icons/hicolor/128x128/apps/hondalog.png
command -v update-desktop-database >/dev/null 2>&1 && sudo update-desktop-database /usr/local/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && sudo gtk-update-icon-cache -q -t -f /usr/local/share/icons/hicolor || true

mkdir -p "$HOME/hondalog/logs"

RELOGIN=0
if id -nG "$ME" | tr ' ' '\n' | grep -qx dialout; then
    echo "==> $ME is already in the dialout group"
else
    echo "==> Adding $ME to the dialout group (needed to open /dev/ttyUSB0)"
    sudo usermod -aG dialout "$ME"
    RELOGIN=1
fi

if dpkg -s brltty >/dev/null 2>&1; then
    echo
    echo "WARNING: brltty is installed. It grabs FTDI adapters, and the cable will"
    echo "         vanish right after plugging in. Fix: sudo apt remove brltty"
fi

echo
echo "Done."
if [ "$RELOGIN" -eq 1 ]; then
    echo "  * LOG OUT AND BACK IN (or reboot) before using the real cable."
    echo "    The dialout group only applies to new logins. The simulator works now."
fi
echo "  * Try it without the car:   hondalog-sim      (leave it running)"
echo "                              hondalog --port /tmp/hondalog-sim --connect"
echo "  * Logs go to ~/hondalog/logs. The address map is ~/.config/hondalog/addressmap.toml"
