#!/usr/bin/env bash
# Deploy the --user PATH fix so systemd transient units (Claude Code RC-bridge Stop hooks) find node.
# Idempotent: installs the persistent env file AND applies to the already-running --user manager.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.config/environment.d"
cp "$here/10-fleet-path.conf" "$HOME/.config/environment.d/10-fleet-path.conf"
# apply to the live manager (environment.d is only re-read at manager start / next login)
cur="$(systemctl --user show-environment | sed -n 's/^PATH=//p')"
case ":$cur:" in *":$HOME/.local/bin:"*) ;; *) systemctl --user set-environment PATH="$HOME/.local/bin:$HOME/bin:${cur:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}";; esac
echo "installed environment.d/10-fleet-path.conf and applied to the live --user manager"
