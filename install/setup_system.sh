#!/usr/bin/env bash
set -euo pipefail

# One-time host setup for USB/VISA access (pyvisa-py).
# - Installs libusb runtime/dev headers if apt is available.
# - Installs udev rules to grant user access to Rigol/Siglent USBTMC devices.
# - Adds the current user to plugdev for group-based access.
# Run as an unprivileged user; the script will sudo where needed.

RULE_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/99-visa.rules"
RULE_DST="/etc/udev/rules.d/99-visa.rules"

echo "Installing udev rule to ${RULE_DST}"
sudo cp "${RULE_SRC}" "${RULE_DST}"
sudo udevadm control --reload-rules
sudo udevadm trigger

# Optionally install libusb packages if using apt-based distro.
if command -v apt-get >/dev/null 2>&1; then
  echo "Installing libusb runtime/dev packages (requires sudo)..."
  sudo apt-get install -y libusb-1.0-0 libusb-1.0-0-dev
else
  echo "Skipping libusb install (apt-get not found); install libusb 1.0 manually."
fi

# Ensure plugdev exists and user is a member.
if ! getent group plugdev >/dev/null 2>&1; then
  echo "Creating plugdev group (requires sudo)..."
  sudo groupadd plugdev
fi

if id -nG "${USER}" | grep -qw plugdev; then
  echo "User ${USER} already in plugdev."
else
  echo "Adding ${USER} to plugdev (requires sudo)..."
  sudo usermod -aG plugdev "${USER}"
  echo "You must log out/in or reboot for group membership to take effect."
fi

echo "Done. Unplug and replug instruments. Verify with: python3 examples/list_devices.py"
