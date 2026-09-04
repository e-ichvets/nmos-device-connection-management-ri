#!/usr/bin/env bash
#
# Installs the nmos-dcm .deb (built by
# build-deb.sh) on a target box, then sets up a dedicated venv for its
# Python dependencies instead of fighting the system Python:
#
# - dpkg -i / apt-get install -f still places the actual product (the
#   nmosconnection module, /usr/bin/connectionmanagement,
#   share/ipp-connectionmanagement schemas, systemd unit) and its real
#   apt dependencies (flask, jsonschema, gevent, netifaces, requests, six)
#   exactly as before.
# - The venv is created with --system-site-packages, so it inherits all
#   of the above as fallbacks (no need to reinstall nmosconnection itself,
#   or netifaces/jsonschema/requests/six/gevent, inside it) - but anything
#   pip-installed INSIDE the venv takes precedence for that package name,
#   with zero risk of touching a dpkg-owned file (unlike
#   --break-system-packages/--ignore-installed against the real system
#   Python, which is what forced this rework in the first place).
# - nmoscommon/mediatimestamp/nodefacade (setup.py's install_requires,
#   never real apt packages under any name) plus their own transitive
#   PyPI-only dependencies (discovered one ModuleNotFoundError at a time,
#   then cross-checked against nmoscommon 0.20.1's and nodefacade 0.12.4's
#   actual requires_dist on PyPI) go in with --no-deps, same reasoning as
#   before: without it, pip tries to build nmoscommon's own ancient
#   gevent pin (<=1.4.0) from source, which fails outright on Python 3.12
#   (that gevent/libev version's C extension uses longintrepr.h, removed/
#   moved in 3.12) - the inherited system python3-gevent already works
#   fine at runtime regardless of not matching that exact old pin.
# - flask+werkzeug are the one real version INCOMPATIBILITY (not just a
#   missing package): nmoscommon's webapi.py imports
#   werkzeug.wrappers.BaseResponse, removed in modern Werkzeug (2.1+).
#   nmoscommon pins werkzeug>=0.14.1,<1.0.0 itself - installed together
#   with a compatible old flask in one pip invocation so the resolver
#   picks a consistent pairing, overriding (only within this venv) the
#   modern flask/werkzeug the system/apt copy provides.
#
# Usage:
#   ./install.sh <path-to-deb>

set -euo pipefail

DEB_FILE="${1:?usage: install.sh <path-to-deb>}"
VENV_DIR="/opt/dreamcatcher/venv/nmos-connection-management"

sudo dpkg -i "$DEB_FILE"
sudo apt-get install -y -f
sudo apt-get install -y python3-venv

sudo python3 -m venv --system-site-packages "$VENV_DIR"
sudo "$VENV_DIR/bin/pip" install --upgrade pip

sudo "$VENV_DIR/bin/pip" install --no-deps \
    nmoscommon mediatimestamp nodefacade \
    gevent-websocket flask-sockets pygments \
    greenlet socketio-client pyzmq python-dateutil \
    oauthlib requests-oauthlib flask-oauthlib \
    ws4py websocket-client ujson mediajson authlib \
    pyopenssl cryptography dnspython \
    "zeroconf<0.25.0,>=0.21.0" zeroconf-monkey \
    cysystemd mdnsbridge typing_extensions deprecated wrapt

# dcMxlDriver.py's own dependency (in-process mxl-fabrics-proxy-config.yaml
# editing), not part of the nmoscommon chain above - installed with deps
# since it's a small, self-contained, actively-maintained package.
sudo "$VENV_DIR/bin/pip" install pyyaml

sudo "$VENV_DIR/bin/pip" install "flask<1.2" "werkzeug<1.0.0,>=0.14.1" "markupsafe<2.0" \
    "oauthlib<3.0.0,>=1.1.2,!=2.0.3,!=2.0.4,!=2.0.5" \
    "requests-oauthlib<1.2.0,>=0.6.2" cachelib

echo
echo "Run it directly with:"
echo "  sudo $VENV_DIR/bin/python3 /usr/bin/connectionmanagement"
