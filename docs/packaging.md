# Packaging

What gets built, how to build it, and the two places a distribution cannot give
Codinian what it needs.

Everything here is under `packaging/`, except the COPR entry point, which has to
live at `.copr/Makefile` because that is where COPR looks.

## The version number

One source: `codinian/version.py`. `pyproject.toml` reads it, the app's About
tab reads it, and the RPM spec and Debian changelog carry copies.

```bash
packaging/bump-version.sh 0.3.0    # writes all three, plus a changelog entry each
packaging/check-versions.sh        # they agree
packaging/check-versions.sh v0.3.0 # ...and so does the tag
```

The check runs in CI on every push, and on a tag it compares the tag too, so a
tag that disagrees with the tree fails the release rather than publishing an
archive whose name does not match what is in it.

Generating the other three from one of them was the alternative, and it costs
more than it saves. `setuptools-scm` needs git metadata that a release tarball
does not carry, COPR builds the spec exactly as committed, and a Debian
changelog is a written history with a date and a message rather than something
worth generating.

## Building locally

An RPM, from a working tree:

```bash
rm -rf /tmp/stage && mkdir -p /tmp/stage/codinian-0.2.0
rsync -a --exclude=.git --exclude=__pycache__ ./ /tmp/stage/codinian-0.2.0/
tar czf ~/rpmbuild/SOURCES/codinian-0.2.0.tar.gz -C /tmp/stage codinian-0.2.0
rpmbuild -bb packaging/rpm/codinian.spec
```

From a commit, the way COPR does it:

```bash
make -f .copr/Makefile srpm outdir=/tmp/srpms
rpmbuild --rebuild /tmp/srpms/codinian-*.src.rpm
```

A .deb needs a Debian userspace, so a container is the practical route. This is
what the CI job does:

```bash
podman run --rm -v "$PWD:/src:ro,z" debian:sid bash -c '
  apt-get update -qq
  apt-get install -y --no-install-recommends debhelper dh-python python3-all \
      python3-setuptools pybuild-plugin-pyproject desktop-file-utils build-essential
  cp -r /src /build && cd /build && cp -r packaging/debian debian
  dpkg-buildpackage -us -uc -b'
```

`debian/` is assembled by copying `packaging/debian/` into place rather than
kept at the repository root, so the root stays free of a directory that only
one of the two packaging paths uses. `.gitignore` covers the copy.

## Fedora and the COPR repository

Fedora 44 satisfies everything Codinian needs except one package:
`python3-aiohttp` is 3.13.5, above the 3.12.14 floor, and `python3-mcp`,
`python3-anyio`, `python3-jsonschema`, `python3-sniffio`, `python3-pyyaml` and
`python3-qrcode` are all there.

The exception is `claude-agent-sdk`, which is in no Fedora repository. So the
COPR carries two packages: Codinian, and the SDK.

### Why the SDK is built from the sdist

The wheel Anthropic publishes on PyPI is 317 MB, because it bundles a copy of
the `claude` binary at `claude_agent_sdk/_bundled/claude`. That is why PyPI tags
it `manylinux_2_17_x86_64` rather than `any`, and it is not something this
project can redistribute: the CLI is Anthropic's.

The source distribution is 345 KB and contains no binary. The SDK looks for a
bundled CLI first and falls back to `claude` on `PATH`, so a package built from
the sdist runs the system CLI, which is the one being updated by a package
manager. `packaging/copr/build-sdk-srpm.sh` is what builds it.

`pip install claude-agent-sdk` gets the 317 MB wheel. `pip install --no-binary
claude-agent-sdk claude-agent-sdk` gets the small one, which is what the CI test
job uses.

### Setting up the project

Not yet created. Once it is:

1. Create a COPR project, `higheraims/codinian`, with the Fedora releases to
   build for.
2. Add a package for Codinian using the **Make** source method, which runs
   `make -f .copr/Makefile srpm`. Add a GitHub webhook so a push builds it.
3. Add the SDK. It is not built from this repository, so build its SRPM and
   upload it:

   ```bash
   OUTDIR=/tmp packaging/copr/build-sdk-srpm.sh
   copr-cli build higheraims/codinian /tmp/python-claude-agent-sdk-*.src.rpm
   ```

   It needs rebuilding when Anthropic releases a version worth picking up, which
   means editing `Version:` in `packaging/rpm/python-claude-agent-sdk.spec`.

### Installing, once it is published

```bash
sudo dnf copr enable higheraims/codinian
sudo dnf install codinian
```

## Debian and Ubuntu

The packaging works. The dependency does not, on any released Debian or Ubuntu.

`aiohttp` carries the remote server, so Codinian floors it at 3.12.14, the
release that fixed the last request-smuggling hole in its pure-Python HTTP
parser (CVE-2025-53643). That floor matters most on exactly the path Codinian
recommends: `tailscale serve` puts a proxy in front of this server, which is the
setting request smuggling exploits.

Measured, in containers:

| Target | python3-aiohttp | Satisfies the floor |
| --- | --- | --- |
| Fedora 44 | 3.13.5 | yes |
| Debian sid | 3.14.1 | yes |
| Debian 13 (trixie) | 3.11.16-1+deb13u1 | no |
| Ubuntu 25.10 | 3.11.16-1ubuntu0.1 | no |
| Ubuntu 24.04 LTS | 3.9.1-1ubuntu0.1 | no |

Debian's own security tracker lists trixie's 3.11.16-1+deb13u1 as vulnerable to
CVE-2025-53643, marked no-DSA (minor issue), so the version number is not
understating what is installed there.

`debian/control` therefore keeps `python3-aiohttp (>= 3.12.14)`. The .deb builds
on every target above and installs on sid; on the others apt refuses it. That is
the intended behaviour: a package that installed anyway would be running a
parser with a known hole in it, on the machine where approving a tool call runs
that tool.

Every other dependency is available everywhere, including
`gir1.2-webkit-6.0` and `gir1.2-vte-3.91`.

There is no apt repository. Publishing one needs a signing key and somewhere to
host it, neither of which exists yet.

Two further gaps on this path, both from `claude-agent-sdk` not being in Debian:
it is not a dependency of the .deb, and the app cannot start a session until it
is installed with pip. `pip install --no-binary claude-agent-sdk
claude-agent-sdk` is the form that avoids the bundled CLI.

## The claude CLI

Anthropic ships it in its own dnf repository as `claude-code`. Both RPM packages
carry `Recommends: claude-code` rather than `Requires:`, because a hard
dependency on a package from a repository the user has not added would make
Codinian uninstallable. dnf skips a Recommends it cannot resolve and installs it
where it can.

### Which copy a session runs

Two can be present at once: the `claude-code` package's `/usr/bin/claude`, and
the one inside the SDK if it came from the PyPI wheel. The SDK's own search
prefers the bundled copy, which is the wrong way round for a packaged install:
that copy is pinned to whatever version the SDK release was built around, while
dnf moves `/usr/bin/claude` several times a week.

So Codinian chooses instead and passes the answer as
`ClaudeAgentOptions(cli_path=...)`. Default is the system install, falling back
to the bundled copy where there is none, which is who the bundle was put in the
wheel for. `codinian/claude_cli.py` has the search order and the reasoning;
Settings > About lists both copies, their versions and the bundled one's size.

An upgrade while the app is running is safe and needs no restart: the running
process holds the old inode and the next session picks up the new binary. That
is a property of the RPM path, not of pip, where the library and the binary
move together.

## What CI covers

`.github/workflows/packaging.yml`:

- the three version numbers agree, and match the tag on a tag push
- the SDK and Codinian RPMs both build on Fedora, and `dnf install` resolves
  every generated dependency against real repositories
- the installed package is imported from `/`, not from the checkout, because cwd
  precedes site-packages on `sys.path` and the checkout has a `codinian/`
  directory in it
- the desktop entry and the AppStream metadata validate, from the buildroot in
  `%check` and again after installation
- the .deb builds on Debian sid, Debian 13 and Ubuntu 25.10, and each job records
  what its distribution can offer for aiohttp. The install step is allowed to
  fail, for the reason above.
