%global appid net.higheraims.codinian

Name:           codinian
Version:        0.2.0
Release:        1%{?dist}
Summary:        Run and supervise Claude Agent SDK sessions

License:        GPL-3.0-only
URL:            https://github.com/higheraims/codinian
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

# Explicit rather than %%pyproject_buildrequires, which would generate a build
# dependency on python3dist(claude-agent-sdk) from pyproject.toml. That package
# is not in Fedora (it is built from python-claude-agent-sdk.spec beside this
# file), so generating it would make this spec unbuildable anywhere the SDK has
# not been built first, including a plain Fedora container in CI. Nothing here
# needs the SDK to produce a wheel.
BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

# The GTK stack, from the distribution rather than from PyPI: PyGObject builds
# against the system libraries, so a wheel cannot express these. The list is
# every typelib the code calls gi.require_version on.
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       webkitgtk6.0
Requires:       vte291-gtk4

# The Python dependencies are not listed here. rpm generates them from the
# installed dist-info, so the wheel metadata is the single source: this package
# comes out requiring python3dist(aiohttp) >= 3.12.14, python3dist(pyyaml),
# python3dist(qrcode) and python3dist(claude-agent-sdk), with the aiohttp floor
# carried straight through from pyproject.toml. Restating them here would be a
# second place to forget.

# The claude CLI. Anthropic ships it in its own dnf repository, so this is a
# Recommends rather than a Requires for the same reason as in the SDK package:
# a hard dependency would make this uninstallable without that repository. The
# app starts without it and says what is missing when a session fails to start.
Recommends:     claude-code

%description
Codinian starts and supervises local Claude Code sessions through the Claude
Agent SDK and puts a GTK window around them: a sidebar of running sessions, a
transcript pane, tool-call approvals, and the project's git state and issue
tracker beside them.

The same sessions are reachable from a phone or a second machine over an HTTP
server that binds to loopback by default and is intended to be published on a
tailnet. Approving a tool call runs that tool on this machine, so the bind stays
closed and every request carries a token.

%prep
%autosetup -n %{name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files codinian

install -Dpm 0644 packaging/%{appid}.desktop \
    %{buildroot}%{_datadir}/applications/%{appid}.desktop
install -Dpm 0644 packaging/%{appid}.metainfo.xml \
    %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml
install -Dpm 0644 codinian/remote/static/codinian.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{appid}.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml
# %%pyproject_check_import is deliberately not run. Importing the package means
# importing gi and the SDK: the GTK typelibs are not in a build root, and adding
# them would pull the whole desktop stack into every build to prove something
# the test suite already proves. The suite runs in CI instead.

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_datadir}/applications/%{appid}.desktop
%{_metainfodir}/%{appid}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg

%changelog
* Fri Sep 11 2026 higheraims <122185704+higheraims@users.noreply.github.com> - 0.2.0-1
- Initial package (ISSUE-047)
