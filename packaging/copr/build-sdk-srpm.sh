#!/usr/bin/env bash
# Build the SRPM for python-claude-agent-sdk and print where it landed.
#
# The SDK is not in Fedora, so Codinian's COPR carries it. It is not built from
# this repository the way codinian itself is: the source is a PyPI sdist named
# in the spec, which spectool fetches. Upload the result with
#
#     copr-cli build higheraims/codinian <path printed below>
#
# Deliberately the sdist and not the wheel: the wheel on PyPI is 317 MB because
# it bundles a copy of the claude binary, which is Anthropic's to distribute and
# not ours. The sdist is pure Python, and the SDK falls back to the claude on
# PATH when it finds no bundled copy.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
spec="$(dirname "$here")/rpm/python-claude-agent-sdk.spec"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

for tool in rpmbuild spectool; do
    command -v "$tool" >/dev/null || {
        echo "missing $tool: dnf install rpm-build rpmdevtools" >&2
        exit 1
    }
done

spectool -g -C "$workdir" "$spec"
rpmbuild -bs \
    --define "_sourcedir $workdir" \
    --define "_srcrpmdir ${OUTDIR:-$PWD}" \
    "$spec"
