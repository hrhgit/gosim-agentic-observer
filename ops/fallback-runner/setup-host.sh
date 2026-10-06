#!/bin/bash
# The administrator part of the fallback runner setup, done once on the
# organizer's Mac. Run it as the organizer (not with sudo); it asks for the
# administrator password once:
#
#   bash ops/fallback-runner/setup-host.sh
#
# 1. a hidden standard account, observerfb, that only runs the VM;
# 2. the host firewall: pf anchor observer-fallback (loaded now and at boot)
#    bounds everything that account can reach (see pf.anchor);
# 3. a sudoers rule letting the organizer run limactl, and only limactl, as
#    observerfb without a password, so the on-demand watcher can start and
#    stop the VM (fallback-watcher.py) and setup-vm.sh can create it.
#
# It is idempotent. Nothing else changes; undo with
#   sudo rm /etc/sudoers.d/observer-fallback /Library/LaunchDaemons/org.agentic-observer.pf.plist /etc/pf.anchors/observer-fallback
#   sudo cp /etc/pf.conf.before-observer-fallback /etc/pf.conf && sudo pfctl -f /etc/pf.conf
#   sudo sysadminctl -deleteUser observerfb
set -euo pipefail
account=observerfb
limactl=/opt/homebrew/bin/limactl
here="$(cd "$(dirname "$0")" && pwd)"
organizer="$(id -un)"
[ "$(id -u)" -ne 0 ] || { echo "Run as the organizer's own account, not with sudo." >&2; exit 2; }
[ -x "$limactl" ] || { echo "Lima is missing: brew install lima" >&2; exit 2; }

echo "Administrator password (asked once) for: account $account, pf rules, limactl-only sudo rule."
sudo -v
while sleep 50; do sudo -n -v 2>/dev/null || exit; done &
keepalive=$!
trap 'kill "$keepalive" 2>/dev/null' EXIT

echo "== 1/3 VM account $account"
if ! id "$account" >/dev/null 2>&1; then
  # A random password nobody keeps: the account is never used to log in.
  sudo sysadminctl -addUser "$account" -fullName "Observer fallback VM" \
    -password "$(openssl rand -base64 30)" -home "/Users/$account" 2>&1 | grep -v -i securetoken || true
fi
id "$account" >/dev/null
sudo dscl . -create "/Users/$account" IsHidden 1
[ -d "/Users/$account" ] || sudo createhomedir -c -u "$account" >/dev/null
sudo chown "$account":staff "/Users/$account" && sudo chmod 700 "/Users/$account"

echo "== 2/3 host firewall (pf anchor observer-fallback)"
# Reloading /etc/pf.conf replaces the main ruleset. Refuse if another program
# (a VPN or proxy) has put rules there, instead of silently removing them.
foreign="$(sudo pfctl -s rules 2>/dev/null; sudo pfctl -s nat 2>/dev/null)"
if grep -v -e '"com.apple' -e '"observer-fallback"' <<<"$foreign" | grep -q .; then
  echo "pf already holds rules from another program; not reloading /etc/pf.conf:" >&2
  echo "$foreign" >&2
  exit 1
fi
sudo install -m 644 -o root -g wheel "$here/pf.anchor" /etc/pf.anchors/observer-fallback
if ! grep -q '^anchor "observer-fallback"' /etc/pf.conf; then
  sudo cp -n /etc/pf.conf /etc/pf.conf.before-observer-fallback
  printf 'anchor "observer-fallback"\nload anchor "observer-fallback" from "/etc/pf.anchors/observer-fallback"\n' \
    | sudo tee -a /etc/pf.conf >/dev/null
fi
sudo pfctl -nf /etc/pf.conf
sudo install -m 644 -o root -g wheel "$here/org.agentic-observer.pf.plist" /Library/LaunchDaemons/
sudo launchctl bootout system/org.agentic-observer.pf 2>/dev/null || true
sudo launchctl bootstrap system /Library/LaunchDaemons/org.agentic-observer.pf.plist
sudo pfctl -E -f /etc/pf.conf 2>&1 | grep -v -e 'ALTQ' -e 'flushing of rules' -e 'present in the main ruleset' || true
rules="$(sudo pfctl -a observer-fallback -s rules 2>/dev/null)"
echo "$rules"
grep -q 'port = 18080' <<<"$rules" && grep -q 'block return out quick on lo0 all user' <<<"$rules" ||
  { echo "pf anchor rules are not loaded" >&2; exit 1; }
sudo pfctl -s info | grep -q 'Status: Enabled' || { echo "pf is not enabled" >&2; exit 1; }

echo "== 3/3 sudo rule: $organizer may run only $limactl as $account"
rule="$(mktemp)"
printf '%s ALL=(%s) NOPASSWD: %s\n' "$organizer" "$account" "$limactl" > "$rule"
sudo visudo -cqf "$rule"
sudo install -m 440 -o root -g wheel "$rule" /etc/sudoers.d/observer-fallback
rm -f "$rule"
sudo -k
sudo -n -H -u "$account" "$limactl" --version >/dev/null ||
  { echo "the limactl sudo rule does not work (is /etc/sudoers.d included?)" >&2; exit 1; }
echo "Done. The VM itself is created without a password: bash ops/fallback-runner/setup-vm.sh"
