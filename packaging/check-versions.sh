#!/usr/bin/env bash
# Check that every place carrying the version number agrees, and that a git tag
# passed as $1 agrees with them too.
#
# There are four: codinian/version.py, which the app and pyproject.toml both
# read; the RPM spec; the Debian changelog; and the tag. Deriving three of them
# from the fourth at build time was the alternative, and it costs more than it
# saves: setuptools-scm needs git metadata that a release tarball does not have,
# COPR builds the spec as committed, and a Debian changelog is an edited file
# with a date and a message in it rather than something to generate. So they are
# written by packaging/bump-version.sh and checked here.
#
#     packaging/check-versions.sh            # the three files agree
#     packaging/check-versions.sh v0.2.0     # ...and the tag agrees with them
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"

app=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/codinian/version.py")
spec=$(sed -n 's/^Version:[[:space:]]*\(.*\)$/\1/p' "$root/packaging/rpm/codinian.spec")
deb=$(sed -n '1s/^codinian (\([^)]*\)).*$/\1/p' "$root/packaging/debian/changelog")

fail=0
report() {
    printf '%-28s %s\n' "$1" "$2"
}

report "codinian/version.py" "$app"
report "packaging/rpm/codinian.spec" "$spec"
report "packaging/debian/changelog" "$deb"

for name in app spec deb; do
    if [[ -z "${!name}" ]]; then
        echo "could not read a version for: $name" >&2
        fail=1
    fi
done

if [[ "$app" != "$spec" || "$app" != "$deb" ]]; then
    echo "version mismatch: run packaging/bump-version.sh $app" >&2
    fail=1
fi

if [[ $# -ge 1 ]]; then
    tag="${1#v}"
    report "git tag" "$tag"
    if [[ "$tag" != "$app" ]]; then
        echo "tag $1 does not match codinian/version.py ($app)" >&2
        fail=1
    fi
fi

if [[ $fail -eq 0 ]]; then
    echo "versions agree"
fi
exit $fail
