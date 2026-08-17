# SPDX-License-Identifier: GPL-2.0-only
# RPM spec for Fedora, RHEL and openSUSE.
# Build with: rpmbuild -ba packaging/ironkey-lockerplus.spec

Name:           ironkey-lockerplus
Version:        1.8.1
Release:        1%{?dist}
Summary:        Set up and use Kingston IronKey Locker+ encrypted drives
License:        GPL-2.0-only
URL:            https://github.com/SimuZSkyNeT/ironkey-linux-gui
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.8
Requires:       python3-gobject
Requires:       gtk3
Requires:       python3-pycryptodome
Requires:       polkit
Recommends:     udisks2
Recommends:     exfatprogs
Recommends:     python3-cryptography

%description
A graphical application for Kingston IronKey Locker+ 50 G2 encrypted USB
drives, including first-time initialization, which no other Linux tool can
perform. Unlock, mount, lock, format, browse the contents, run speed and
integrity tests, and read firmware diagnostics.

%prep
%autosetup -n ironkey-linux-gui-%{version}

%install
mkdir -p %{buildroot}%{_datadir}/%{name}
install -m 644 src/*.py %{buildroot}%{_datadir}/%{name}/
chmod 755 %{buildroot}%{_datadir}/%{name}/ironkey_gui.py \
          %{buildroot}%{_datadir}/%{name}/ironkey_backend.py

mkdir -p %{buildroot}%{_bindir}
printf '#!/usr/bin/env bash\nexec python3 %{_datadir}/%{name}/ironkey_gui.py "$@"\n' \
    > %{buildroot}%{_bindir}/ironkey-gui
%{_bindir}/ironkey
chmod 755 %{buildroot}%{_bindir}/ironkey-gui
%{_bindir}/ironkey

printf '#!/usr/bin/env bash\nexec python3 %{_datadir}/%{name}/ironkey_cli.py "$@"\n' \
    > %{buildroot}%{_bindir}/ironkey
chmod 755 %{buildroot}%{_bindir}/ironkey

mkdir -p %{buildroot}%{_datadir}/applications
sed 's|^Exec=.*|Exec=%{_bindir}/ironkey-gui
%{_bindir}/ironkey|' ironkey.desktop \
    > %{buildroot}%{_datadir}/applications/%{name}.desktop

%files
%license LICENSE
%doc README.md
%{_datadir}/%{name}/
%{_bindir}/ironkey-gui
%{_bindir}/ironkey
%{_datadir}/applications/%{name}.desktop

%changelog
* Tue Aug 18 2026 Simuz <318048242+SimuZSkyNeT@users.noreply.github.com> - 1.6.0-1
- Built-in file browser, drive details, diagnostics and tools
