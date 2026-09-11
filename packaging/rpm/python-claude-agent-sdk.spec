%global pypi_name claude-agent-sdk
%global dist_name claude_agent_sdk

Name:           python-%{pypi_name}
Version:        0.2.152
Release:        1%{?dist}
Summary:        Python SDK for Claude Code

License:        MIT
URL:            https://github.com/anthropics/claude-agent-sdk-python
Source:         %{pypi_source %{dist_name}}

BuildArch:      noarch
BuildRequires:  python3-devel

%global _description %{expand:
The Python SDK for Claude Code. Codinian uses it to start and supervise `claude`
processes; it is packaged here because Fedora does not carry it, and because the
wheel published on PyPI is not a thing a distribution can ship.

That wheel is 317 MB: it bundles a copy of the `claude` binary under
`claude_agent_sdk/_bundled/claude`, which is why PyPI tags it
`manylinux_2_17_x86_64` rather than `any`. The source distribution carries no
such binary, so this package is built from the sdist and is pure Python and
noarch. The SDK looks for a bundled CLI first and falls back to `claude` on
PATH, so a system-installed `claude` is what it ends up running. Anthropic ships
that in its own dnf repository as `claude-code`.}

%description %_description

%package -n python3-%{pypi_name}
Summary:        %{summary}

# The CLI this SDK drives. Not a hard dependency: it comes from Anthropic's own
# repository rather than from Fedora, so a Requires would make this package
# uninstallable on a machine that has not added that repository. Skipped
# silently where it is unavailable, installed where it is.
Recommends:     claude-code

%description -n python3-%{pypi_name} %_description

%prep
%autosetup -n %{dist_name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l %{dist_name}

%check
%pyproject_check_import

%files -n python3-%{pypi_name} -f %{pyproject_files}

%changelog
* Fri Sep 11 2026 higheraims <122185704+higheraims@users.noreply.github.com> - 0.2.152-1
- Initial package, built from the sdist so the bundled claude binary is left out
