#!/usr/bin/env bash
# Set the version in every place that carries one, then say what to do next.
#
#     packaging/bump-version.sh 0.3.0
#
# Touches codinian/version.py (which the app and pyproject.toml read), the RPM
# spec, and the Debian changelog. It does not commit, tag or push: the changelog
# entry it writes says "release" and nothing else, and a release worth tagging
# usually deserves a better line than that.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $(basename "$0") <version>   e.g. 0.3.0" >&2
    exit 2
fi

version="${1#v}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "not a version this understands: $version (expected N.N.N)" >&2
    exit 2
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
name="$(git -C "$root" config user.name || echo higheraims)"
email="$(git -C "$root" config user.email || echo noreply@users.noreply.github.com)"

sed -i "s/^__version__ = \".*\"$/__version__ = \"$version\"/" "$root/codinian/version.py"
sed -i "0,/^Version:.*$/s//Version:        $version/" "$root/packaging/rpm/codinian.spec"

# Prepended rather than rewritten: a changelog is a history, and the RPM one
# below is the same idea.
changelog="$root/packaging/debian/changelog"
{
    printf 'codinian (%s) unstable; urgency=medium\n\n  * Release %s.\n\n -- %s <%s>  %s\n\n' \
        "$version" "$version" "$name" "$email" "$(date -R)"
    cat "$changelog"
} > "$changelog.tmp"
mv "$changelog.tmp" "$changelog"

spec="$root/packaging/rpm/codinian.spec"
{
    sed '/^%changelog$/q' "$spec"
    printf '* %s %s <%s> - %s-1\n- Release %s\n\n' \
        "$(date '+%a %b %d %Y')" "$name" "$email" "$version" "$version"
    sed '1,/^%changelog$/d' "$spec"
} > "$spec.tmp"
mv "$spec.tmp" "$spec"

"$root/packaging/check-versions.sh"

cat <<EOF

Next:
  git commit -am "Version bump to $version"
  git tag -a v$version -m "v$version"
  git push --follow-tags

The tag is what the release workflow and the COPR webhook watch.
EOF
