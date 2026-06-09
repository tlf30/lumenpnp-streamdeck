#!/usr/bin/env bash
# Build a .deb package for Ubuntu that installs the Stream Deck controller,
# OpenPnP bridge scripts, udev rules, and a per-user systemd service.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_NAME="streamdeck-openpnp"
VERSION="${VERSION:-0.1.0}"
ARCH="all"
BUILD_DIR="${ROOT}/build/debian"
STAGE="${BUILD_DIR}/${PKG_NAME}_${VERSION}_${ARCH}"
DEBIAN_DIR="${STAGE}/DEBIAN"
INSTALL_ROOT="${STAGE}/usr/lib/streamdeck"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

need dpkg-deb
need rsync

rm -rf "${STAGE}"
mkdir -p \
  "${DEBIAN_DIR}" \
  "${INSTALL_ROOT}" \
  "${STAGE}/etc/streamdeck" \
  "${STAGE}/etc/udev/rules.d" \
  "${STAGE}/usr/bin" \
  "${STAGE}/usr/lib/systemd/user" \
  "${STAGE}/usr/share/streamdeck" \
  "${STAGE}/usr/share/doc/${PKG_NAME}"

rsync -a \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${ROOT}/streamdeck_app/" "${INSTALL_ROOT}/streamdeck_app/"

rsync -a --exclude '__pycache__' --exclude '*.pyc' \
  "${ROOT}/openpnp/" "${INSTALL_ROOT}/openpnp/"
mkdir -p "${INSTALL_ROOT}/bin"
install -m 755 "${ROOT}/bin/openpnpctl" "${INSTALL_ROOT}/bin/openpnpctl"
cp "${ROOT}/requirements.txt" "${INSTALL_ROOT}/requirements.txt"

cp "${ROOT}/packaging/debian/70-streamdeck.rules" \
  "${STAGE}/etc/udev/rules.d/70-streamdeck.rules"

cp "${ROOT}/packaging/debian/streamdeck.service" \
  "${STAGE}/usr/lib/systemd/user/streamdeck.service"

cp "${ROOT}/packaging/config.yaml.example" \
  "${STAGE}/usr/share/streamdeck/config.yaml.example"

cp "${ROOT}/packaging/debian/config.yaml" \
  "${STAGE}/etc/streamdeck/config.yaml"

install -m 755 "${ROOT}/packaging/debian/streamdeck-run" "${STAGE}/usr/bin/streamdeck-run"
install -m 755 "${ROOT}/packaging/debian/streamdeck-setup-user" "${STAGE}/usr/bin/streamdeck-setup-user"
install -m 755 "${ROOT}/packaging/debian/openpnpctl" "${STAGE}/usr/bin/openpnpctl"
install -m 755 "${ROOT}/packaging/debian/streamdeck-test-bridge" "${STAGE}/usr/bin/streamdeck-test-bridge"

cp "${ROOT}/README.md" \
  "${STAGE}/usr/share/doc/${PKG_NAME}/README.md"

cat >"${DEBIAN_DIR}/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: misc
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.10), python3-venv, python3-pip, libhidapi-libusb0, libnotify-bin, systemd | systemd-standalone
Maintainer: LumenPNP <support@lumenpnp.com>
Description: Stream Deck + XL controller for OpenPnP / LumenPNP
 Installs the Stream Deck controller service, OpenPnP bridge startup script,
 udev rules for Elgato USB devices, and user configuration under
 ~/.config/streamdeck/config.yaml.
EOF

install -m 755 "${ROOT}/packaging/debian/postinst" "${DEBIAN_DIR}/postinst"
install -m 755 "${ROOT}/packaging/debian/prerm" "${DEBIAN_DIR}/prerm"

OUTPUT="${BUILD_DIR}/${PKG_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "${STAGE}" "${OUTPUT}"

echo
echo "Built: ${OUTPUT}"
echo "Install: sudo apt install ./${OUTPUT#${ROOT}/}"
echo "Then per user: streamdeck-setup-user && systemctl --user enable --now streamdeck"