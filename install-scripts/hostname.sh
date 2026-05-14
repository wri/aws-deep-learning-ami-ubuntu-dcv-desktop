#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <hostname>"
  exit 1
fi

HOSTNAME_INPUT="$1"

if [[ -z "$HOSTNAME_INPUT" ]]; then
  echo "Hostname cannot be empty."
  exit 1
fi

# Ensure hostname is set and persisted across reboots.
sudo hostnamectl set-hostname "$HOSTNAME_INPUT"
echo "$HOSTNAME_INPUT" | sudo tee /etc/hostname >/dev/null

# Update /etc/hosts for local resolution without duplicating old entries.
if grep -qE '^[[:space:]]*127\.0\.1\.1[[:space:]]+' /etc/hosts; then
  sudo sed -i.bak -E "s/^[[:space:]]*127\.0\.1\.1[[:space:]]+.*/127.0.1.1 $HOSTNAME_INPUT/" /etc/hosts
else
  echo "127.0.1.1 $HOSTNAME_INPUT" | sudo tee -a /etc/hosts >/dev/null
fi

echo "Hostname set to: $HOSTNAME_INPUT"
