#!/usr/bin/env bash
#
# Builds the .deb for this repo via a plain hand-written debian/ (control,
# rules, compat, install, changelog) - NOT the repo's original
# stdeb/py2dsc-based Makefile `deb` target, which is broken on Python 3.12
# (python3-stdeb 0.10.0, the only version available via apt on Ubuntu
# 24.04, imports configparser.SafeConfigParser, removed in 3.12). This
# matches how every other dcs* component in ic-server is packaged.
#
# Usage:
#   ./build-deb.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo apt-get install -y debhelper fakeroot

cd "$HERE"
dpkg-buildpackage -uc -b -rfakeroot

echo
echo "Built:"
ls -la "${HERE}"/../*.deb 2>/dev/null || ls -la "${HERE}"/*.deb
