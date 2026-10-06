#!/bin/bash
# Registers the self-hosted fallback runner inside the observer-fallback VM.
# setup-vm.sh runs it from the Mac with RUNNER_URL set and RUNNER_TOKEN, a
# one-hour, register-only token of the single observer-control repository,
# exported on stdin ahead of this script; no personal credential enters the VM.
#
# A repository-level runner serves only observer-control, so participant
# repositories in the same organization can never schedule work on it.
set -euo pipefail
: "${RUNNER_TOKEN:?registration token of the observer-control repository}"
: "${RUNNER_URL:?https://github.com/<runner organization>/observer-control}"
[[ "$RUNNER_URL" =~ ^https://github\.com/AGENTIC-OBSERVER26-runner-[0-9]+/observer-control$ ]] ||
  { echo "RUNNER_URL must be a runner organization's observer-control repository" >&2; exit 2; }
RUNNER_NAME="${RUNNER_NAME:-observer-fallback-1}"
home=/home/runner/actions-runner
# The VM is disposable: to re-register, delete and recreate it.
[ ! -e "$home/.runner" ] || { echo "A runner is already registered in this VM." >&2; exit 2; }
systemctl is-active --quiet observer-egress-guard || { echo "Egress guard is not active." >&2; exit 2; }

port="$(cat /opt/observer/host-proxy-port 2>/dev/null || true)"
proxy_env=()
if [ -n "$port" ]; then
  proxy="http://192.168.5.2:$port"
  export https_proxy="$proxy" http_proxy="$proxy"
  proxy_env=("https_proxy=$proxy" "http_proxy=$proxy" "no_proxy=localhost,127.0.0.1")
fi

case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=x64 ;; *) echo "unsupported architecture" >&2; exit 2 ;; esac
release="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)"
version="$(jq -r .tag_name <<<"$release" | sed 's/^v//')"
# The release notes carry the official checksum of every package.
sha="$(jq -r .body <<<"$release" | sed -n "s/.*<!-- BEGIN SHA linux-$arch -->\([0-9a-f]\{64\}\)<!-- END SHA linux-$arch -->.*/\1/p")"
[ -n "$sha" ] || { echo "runner checksum not found in release $version" >&2; exit 1; }
package="actions-runner-linux-$arch-$version.tar.gz"
install -d -o runner -g runner "$home"
curl -fsSL -o "/tmp/$package" "https://github.com/actions/runner/releases/download/v$version/$package"
echo "$sha  /tmp/$package" | sha256sum -c -
tar -xzf "/tmp/$package" -C "$home" && rm -f "/tmp/$package"
chown -R runner:runner "$home"
"$home/bin/installdependencies.sh" >/dev/null

# Python 3.12 for actions/setup-python, installed once into the tool cache
# (job cleanup keeps it) so no job downloads it. Checked against the release
# asset's digest; LD_LIBRARY_PATH as setup-python sets it for the job steps.
py_release="$(curl -fsSL 'https://api.github.com/repos/actions/python-versions/releases?per_page=50' |
  jq -c '[.[] | select((.tag_name | test("^3\\.12\\.[0-9]+-")) and (.prerelease | not))][0]')"
py_asset="$(jq -c --arg n "linux-24.04-$arch.tar.gz" '.assets[] | select(.name | endswith($n))' <<<"$py_release")"
py_sha="$(jq -r '.digest // empty' <<<"$py_asset" | sed 's/^sha256://')"
[ -n "$py_sha" ] || { echo "Python 3.12 release digest not found" >&2; exit 1; }
py_dir="$(mktemp -d)"
curl -fsSL -o "$py_dir/python.tgz" "$(jq -r .browser_download_url <<<"$py_asset")"
echo "$py_sha  $py_dir/python.tgz" | sha256sum -c -
tar -xzf "$py_dir/python.tgz" -C "$py_dir" && rm -f "$py_dir/python.tgz"
chown -R runner:runner "$py_dir"
py_version="$(jq -r .tag_name <<<"$py_release" | cut -d- -f1)"
tool="$home/_work/_tool"
install -d -o runner -g runner "$home/_work" "$tool"
(cd "$py_dir" && sudo -u runner env "${proxy_env[@]}" ${PIP_INDEX_URL:+PIP_INDEX_URL="$PIP_INDEX_URL"} \
  RUNNER_TOOL_CACHE="$tool" LD_LIBRARY_PATH="$tool/Python/$py_version/$arch/lib" bash ./setup.sh >/dev/null)
rm -rf "$py_dir"
test -e "$tool/Python/$py_version/$arch.complete"

sudo -u runner env "${proxy_env[@]}" "$home/config.sh" --unattended --replace --url "$RUNNER_URL" \
  --token "$RUNNER_TOKEN" --name "$RUNNER_NAME" --labels observer-fallback --work _work
{
  printf '%s\n' "${proxy_env[@]}"
  echo "ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/observer/job-cleanup.sh"
  echo "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/opt/observer/job-cleanup.sh"
} >> "$home/.env"
cd "$home"
./svc.sh install runner
# The runner never runs without the egress guard.
unit="$(cat .service)"
install -d "/etc/systemd/system/$unit.d"
printf '[Unit]\nRequires=observer-egress-guard.service\nAfter=observer-egress-guard.service\n' \
  > "/etc/systemd/system/$unit.d/egress-guard.conf"
systemctl daemon-reload
./svc.sh start
echo "Runner $RUNNER_NAME registered with labels self-hosted, Linux, $arch, observer-fallback."
