#!/bin/bash
# Creates the fallback VM, checks every isolation layer, registers the runner,
# stops the VM and starts the on-demand watcher. Run as the organizer after
# setup-host.sh; no password is needed (limactl runs as observerfb through the
# sudo rule setup-host.sh installed, the registration token comes from the
# organizer's gh login):
#
#   bash ops/fallback-runner/setup-vm.sh
#
# Optional environment: RUNNER_REPOSITORY (AGENTIC-OBSERVER26-runner-13/observer-control),
# RESOLVERS ("223.5.5.5 119.29.29.29"), APT_MIRROR (TUNA ubuntu-ports), PIP_MIRROR (TUNA PyPI),
# IMAGE (a local copy of the pinned image; it is checked against the pinned digest).
# Re-running skips what exists. To start over: limactl delete the instance as observerfb.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
shared=/Users/Shared/observer-fallback
account=observerfb
limactl=/opt/homebrew/bin/limactl
instance=observer-fallback
repository="${RUNNER_REPOSITORY:-AGENTIC-OBSERVER26-runner-13/observer-control}"
resolvers="${RESOLVERS-223.5.5.5 119.29.29.29}"
apt_mirror="${APT_MIRROR-https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports}"
pip_mirror="${PIP_MIRROR-https://pypi.tuna.tsinghua.edu.cn/simple}"
agents="$HOME/Library/LaunchAgents"
[ "$(id -u)" -ne 0 ] || { echo "Run as the organizer's own account, not with sudo." >&2; exit 2; }
vm() { sudo -n -H -u "$account" "$limactl" "$@"; }
vmsh() { vm shell --workdir / "$instance" -- "$@"; }
vm --version >/dev/null || { echo "Run setup-host.sh first (limactl sudo rule missing)." >&2; exit 2; }
[[ "$repository" =~ ^AGENTIC-OBSERVER26-runner-[0-9]+/observer-control$ ]] || { echo "bad RUNNER_REPOSITORY" >&2; exit 2; }

# The watcher would stop a VM that is still being set up; this script starts it again at the end.
launchctl bootout "gui/$(id -u)/org.agentic-observer.fallback-watcher" 2>/dev/null || true

echo "== files in $shared (organizer-owned; observerfb can read, not write)"
mkdir -p "$shared/images"
chmod 755 "$shared" "$shared/images"
install -m 644 "$here/egress-proxy.py" "$here/fallback-watcher.py" "$here/observer-fallback.yaml" "$shared/"

echo "== egress proxy (LaunchAgent, 127.0.0.1:18080)"
install -m 644 "$here/org.agentic-observer.egress-proxy.plist" "$agents/"
launchctl bootout "gui/$(id -u)/org.agentic-observer.egress-proxy" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$agents/org.agentic-observer.egress-proxy.plist"
sleep 2
[ "$(curl -s -o /dev/null -w '%{http_code}' -m 20 -x http://127.0.0.1:18080 https://api.github.com/zen)" = 200 ] ||
  { echo "the egress proxy cannot reach GitHub" >&2; exit 1; }
[ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 -x http://127.0.0.1:18080 https://127.0.0.1:443/ || true)" != 200 ] ||
  { echo "the egress proxy forwards to loopback" >&2; exit 1; }

if ! vm list --json 2>/dev/null | grep -q "\"name\":\"$instance\""; then
  echo "== image"
  read -r location digest < <(awk '/location:/ && /arm64/ {gsub(/"/,"",$3); l=$3}
    l && /digest:/ {gsub(/"|sha256:/,"",$2); print l, $2; exit}' "$shared/observer-fallback.yaml")
  image="${IMAGE:-$shared/images/$(basename "$(dirname "$location")")-$(basename "$location")}"
  if [ ! -f "$image" ]; then
    curl -fL -m 3600 -o "$image.part" "$location" && mv "$image.part" "$image"
  fi
  [ "$(shasum -a 256 "$image" | cut -d' ' -f1)" = "$digest" ] || { echo "$image does not match the pinned digest" >&2; exit 1; }
  chmod 644 "$image"
  echo "== create and boot the VM as $account"
  vm create --name "$instance" --tty=false --set \
    ".param.HostProxyPort=\"18080\" | .param.Resolvers=\"$resolvers\" | .param.AptMirror=\"$apt_mirror\" | .images=[{\"location\":\"$image\",\"arch\":\"aarch64\",\"digest\":\"sha256:$digest\"}]" \
    "$shared/observer-fallback.yaml"
fi
vm list --json | grep "\"name\":\"$instance\"" | grep -q '"status":"Running"' || vm start --tty=false "$instance"
# 2 means done with recoverable warnings (for example deprecated keys).
rc=0; vmsh sudo cloud-init status --wait >/dev/null || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 2 ] || { vmsh sudo cloud-init status --long; exit 1; }

echo "== isolation checks"
# A loopback-only service on the Mac (serving an empty directory) stands in
# for the browser's debugging port.
python3 -m http.server --bind 127.0.0.1 --directory "$(mktemp -d)" 18899 >/dev/null 2>&1 &
canary=$!
trap 'kill "$canary" 2>/dev/null' EXIT
sleep 1
curl -sf -o /dev/null -m 3 http://127.0.0.1:18899/ || { echo "host canary did not start" >&2; exit 1; }
fail=0
check() { # name expected-output command...
  local name="$1" want="$2"; shift 2
  local got; got="$("$@" 2>/dev/null | tail -n 1 || true)"
  if [ "$got" = "$want" ]; then echo "ok    $name"; else echo "FAIL  $name (got: ${got:-nothing})"; fail=1; fi
}
host_files() { vmsh ls /Users >/dev/null 2>&1 && echo visible || echo blocked; }
check "no host files in the VM" blocked host_files
check "gVisor is Docker's default runtime" runsc vmsh sudo docker info --format '{{.DefaultRuntime}}'
check "containers cannot reach the Mac" blocked vmsh sudo -u runner docker run --rm alpine:3.20 sh -c \
  'nc -z -w 3 192.168.5.2 18899 && echo reached || echo blocked'
check "VM root without the in-VM guard cannot reach the Mac" blocked vmsh sudo sh -c \
  'iptables -I OUTPUT 1 -j ACCEPT; curl -sf -m 5 -o /dev/null http://192.168.5.2:18899/ && r=reached || r=blocked; iptables -D OUTPUT 1; echo $r'
check "...nor through the egress proxy" blocked vmsh sudo sh -c \
  'curl -sf -m 5 -o /dev/null -x http://192.168.5.2:18080 http://127.0.0.1:18899/ && echo reached || echo blocked'
check "the runner account reaches GitHub through the proxy" 200 vmsh sudo -u runner \
  curl -s -m 20 -o /dev/null -w '%{http_code}\n' -x http://192.168.5.2:18080 https://api.github.com/zen
[ "$fail" -eq 0 ] || { echo "Isolation checks failed; the runner is not registered." >&2; exit 1; }

# The restricted-egress forwarder image, pulled once (job cleanup keeps
# registry images); through the Mac's proxy a first pull can outlast a job's
# pull timeout.
proxy_image="$(sed -n 's/^PROXY_IMAGE = "\(.*\)"$/\1/p' "$here/../../project_platform/egress.py")"
[ -n "$proxy_image" ] && vmsh sudo docker pull -q "$proxy_image" >/dev/null

echo "== runner on $repository"
if ! vmsh sudo test -e /home/runner/actions-runner/.runner; then
  token="$(gh api -X POST "repos/$repository/actions/runners/registration-token" -q .token)"
  # The one-hour, register-only token travels on stdin, never on a command line.
  { printf 'export RUNNER_TOKEN=%q\n' "$token"; cat "$here/install-runner.sh"; } |
    vmsh sudo RUNNER_URL="https://github.com/$repository" PIP_INDEX_URL="$pip_mirror" bash -s
fi
for _ in $(seq 60); do
  status="$(gh api "repos/$repository/actions/runners" -q '.runners[] | select(.name=="observer-fallback-1") | .status')"
  [ "$status" = online ] && break
  sleep 5
done
[ "$status" = online ] || { echo "the runner did not come online" >&2; exit 1; }
echo "runner observer-fallback-1 is online"

echo "== stop the VM; the watcher starts it on demand"
vm stop "$instance"
install -m 644 "$here/org.agentic-observer.fallback-watcher.plist" "$agents/"
launchctl bootstrap "gui/$(id -u)" "$agents/org.agentic-observer.fallback-watcher.plist"
sleep 5
tail -n 3 "$shared/watcher.log"
echo "Done. Enable with fallback_capacity=1 on the runner organization (ops/self-hosted-fallback-runner.md)."
