#!/usr/bin/env bash
# install.sh — one-shot installer for the BabyVision-v2 viewer service.
#
# Run once, as root (or via sudo):
#   sudo bash /home/matianyi/BabyVision-v2/deploy/install.sh
#
# What it does:
#   1. installs the Caddy binary into /usr/local/bin/caddy
#   2. drops the Caddyfile into /etc/caddy/
#   3. drops the systemd unit into /etc/systemd/system/
#   4. drops the sudoers fragment into /etc/sudoers.d/ (so matianyi can
#      start/stop the service without a password)
#   5. creates /var/log/caddy and Caddy data dirs owned by matianyi
#   6. reloads systemd, enables and starts the service
#
# Idempotent — safe to re-run.

set -euo pipefail

REPO=/home/matianyi/BabyVision-v2
DEPLOY="$REPO/deploy"
CADDY_SRC="${CADDY_SRC:-/tmp/caddy}"   # override if binary is elsewhere
SERVICE_USER=matianyi

if [[ $EUID -ne 0 ]]; then
	echo "install.sh: must run as root (try: sudo bash $0)" >&2
	exit 1
fi

echo "==> installing Caddy binary"
if [[ ! -x "$CADDY_SRC" ]]; then
	echo "install.sh: Caddy binary not found at $CADDY_SRC" >&2
	echo "Download it first, e.g.:" >&2
	echo "  curl -fsSL https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_amd64.tar.gz \\" >&2
	echo "    | tar -xz -C /tmp caddy && chmod +x /tmp/caddy" >&2
	exit 1
fi
install -o root -g root -m 0755 "$CADDY_SRC" /usr/local/bin/caddy

echo "==> creating /etc/caddy and /var/log/caddy"
install -d -o root -g root -m 0755 /etc/caddy
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 /var/log/caddy
# Caddy data + config dirs (used for autosaved Caddyfile, certs later).
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 \
	"/home/$SERVICE_USER/.local/share/caddy" \
	"/home/$SERVICE_USER/.config/caddy"

echo "==> installing /etc/caddy/Caddyfile"
install -o root -g root -m 0644 "$DEPLOY/Caddyfile" /etc/caddy/Caddyfile

echo "==> installing /etc/systemd/system/babyvision-viewer.service"
install -o root -g root -m 0644 \
	"$DEPLOY/babyvision-viewer.service" \
	/etc/systemd/system/babyvision-viewer.service

echo "==> installing /etc/sudoers.d/babyvision-viewer"
TMP_SUDO="$(mktemp)"
trap 'rm -f "$TMP_SUDO"' EXIT
install -o root -g root -m 0440 "$DEPLOY/babyvision-viewer.sudoers" "$TMP_SUDO"
if ! visudo -c -q -f "$TMP_SUDO"; then
	echo "install.sh: sudoers fragment failed validation; aborting" >&2
	exit 1
fi
install -o root -g root -m 0440 "$TMP_SUDO" /etc/sudoers.d/babyvision-viewer

echo "==> sanity-checking Caddyfile"
/usr/local/bin/caddy validate --config /etc/caddy/Caddyfile

# `caddy validate` opens the configured log writer during the dry run, which
# creates /var/log/caddy/babyvision-viewer.log as root. The service then runs
# as $SERVICE_USER and can't write to that file. Re-own anything in the log
# dir so the next start succeeds.
chown -R "$SERVICE_USER:$SERVICE_USER" /var/log/caddy

echo "==> reloading systemd and enabling service"
systemctl daemon-reload
systemctl enable babyvision-viewer.service
systemctl restart babyvision-viewer.service

sleep 1
systemctl --no-pager --lines=10 status babyvision-viewer.service || true

echo
echo "Done. Try:"
echo "  $REPO/scripts/viewer status"
echo "  curl -fsI http://localhost/"
