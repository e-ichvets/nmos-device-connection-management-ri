# DreamCatcher's own MXL sender/receiver driver logic - genuinely
# production code, as opposed to the surrounding nmosDriver.py's demo/
# mock RTP admin API this was extracted out of (inherited from the BBC
# reference implementation - random-address generators, an example web
# UI, etc., none of which apply to real MXL senders/receivers). Kept in
# its own module so it's obvious at a glance which part of this codebase
# is real DreamCatcher logic.

from __future__ import absolute_import

import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import yaml

from .activationController import activationController
from .mxlReceiver import MxlReceiver
from .mxlSender import MxlSender

__tp__ = 'transport_params'

# Path to a JSON object mapping sender uuid -> {"legs", "transport_params"}
# (per dcstranscode/scripts/make-mxl-config.py's own --nmos-senders-config),
# each bootstrapped as a fixed-id, pre-staged, pre-activated mxl sender at
# startup - so senders survive a restart with the SAME id/state instead of
# only existing after a fresh POST /api/senders/ with a random uuid4().
DC_MXL_SENDERS_CONFIG_ENV = "DC_MXL_SENDERS_CONFIG"

# Path to a JSON object mapping receiver uuid -> {"input", "leg", "endpoints"}
# - pure topology, hand-provisioned by the operator (not generated the way
# DC_MXL_SENDERS_CONFIG is). No mxl_domain_id/mxl_flow_id here at all -
# those only ever arrive later via a real PATCH .../receivers/<id>/staged,
# same as any other receiver. "input" is which local dcstranscode input
# bridge (mxl-dcstranscode-input@<input>) this receiver's data feeds; up to
# 3 receiver uuids (video/audio/data) can share one input. "endpoints" is
# a list of {"host", "port", "protocol"} describing the remote source(s)
# this receiver could pull from - only entries with "protocol": "mxl" are
# ever used (the first one found), since the subscription URL built from
# it is always mxl://<host>:<port>/...; host/port is the remote
# mxl-fabrics-proxy's own address (its --listen port, e.g. 2283 - not the
# source DC's NMOS Connection API port) ("leg" is always 1 this iteration
# - no multi-leg support yet). See _onReceiverActivated() for how this
# gets used.
DC_MXL_RECEIVERS_CONFIG_ENV = "DC_MXL_RECEIVERS_CONFIG"

# Where the locally-running mxl-fabrics-proxy's own config lives, and its
# admin API (for POST /v1/reload) - see
# _resolveFabricsProxyConfigPath()/_reloadFabricsProxy() below.
# DC_MXL_FABRICS_PROXY_CONFIG_ENV is an explicit override; if unset, the
# real path is discovered from the running container itself (its own
# --config arg + matching bind mount), falling back to
# DEFAULT_FABRICS_PROXY_CONFIG only if that discovery fails.
DC_MXL_FABRICS_PROXY_CONFIG_ENV = "DC_MXL_FABRICS_PROXY_CONFIG"
DC_MXL_FABRICS_PROXY_ADMIN_URL_ENV = "DC_MXL_FABRICS_PROXY_ADMIN_URL"
DC_MXL_FABRICS_PROXY_CONTAINER_ENV = "DC_MXL_FABRICS_PROXY_CONTAINER"
DEFAULT_FABRICS_PROXY_CONFIG = "/opt/dreamcatcher/share/mxl/mxl-fabrics-proxy-config.yaml"
DEFAULT_FABRICS_PROXY_ADMIN_URL = "http://localhost:2283"
DEFAULT_FABRICS_PROXY_CONTAINER = "mxl-fabrics-proxy"

# essence "format" -> make-mxl-config.py's own flag for it - see
# _detectFlowsUnderDomain()/_generateInputConfig() below.
FORMAT_TO_FLAG = {
    "urn:x-nmos:format:video": "-v",
    "urn:x-nmos:format:audio": "-a",
    "urn:x-nmos:format:data": "-d",
}


class DcMxlDriver:
    """Creates and bootstraps real MXL senders/receivers. Constructed by
    NmosDriverWebApi with the pieces it needs (logger/manager/
    facadeWrapper) plus a reference back to addSenderToIS04 - that one
    method stays on NmosDriverWebApi since it's genuinely transport-
    agnostic (shared with the RTP path), not MXL-specific itself."""

    def __init__(self, logger, manager, facadeWrapper, addSenderToIS04):
        self.logger = logger
        self.manager = manager
        self.facadeWrapper = facadeWrapper
        self.addSenderToIS04 = addSenderToIS04
        # receiverId -> {"input": ..., "endpoints": [...]}, populated only
        # for statically-bootstrapped receivers (see addReceiverFromConfig) -
        # remembered here since it's not part of NMOS state at all, just
        # DcMxlDriver's own bookkeeping for _onReceiverActivated().
        self._receiverTopology = {}
        # receiverId -> (endpoint, domain_id, flow_id) for whatever this
        # receiver's own _syncReceiver() last wired up - needed because a
        # later PATCH clearing mxl_domain_id/mxl_flow_id back to null (a
        # removal) carries none of that itself; _onReceiverActivated()
        # pops this to know what to unwind.
        self._receiverActiveFlow = {}
        self.logger.writeDebug("DreamCatcher MXL driver running")

    def bootstrapStaticSenders(self):
        config_path = os.environ.get(DC_MXL_SENDERS_CONFIG_ENV)
        if not config_path:
            return
        try:
            senders_by_id = json.loads(Path(config_path).read_text())
        except (OSError, ValueError) as e:
            self.logger.writeWarning(
                "Could not load {}={}: {}".format(DC_MXL_SENDERS_CONFIG_ENV, config_path, e))
            return
        bootstrapped = 0
        for senderId, entry in senders_by_id.items():
            try:
                self.addSenderFromConfig(senderId, entry)
                bootstrapped += 1
            except Exception as e:
                self.logger.writeWarning(
                    "Failed to bootstrap static mxl sender {}: {}".format(senderId, e))
        self.logger.writeDebug(
            "Bootstrapped {}/{} static mxl senders from {}={}".format(
                bootstrapped, len(senders_by_id), DC_MXL_SENDERS_CONFIG_ENV, config_path))

    def addSenderFromConfig(self, senderId, entry):
        legs = entry.get('legs', 1)
        self.addSenderToIS04(senderId, "urn:x-nmos:transport:mxl")
        self.addSenderToIS05(legs, senderId, entry.get('transport_params'))

    def addSender(self, legs):
        senderId = str(uuid4())
        self.addSenderToIS04(senderId, "urn:x-nmos:transport:mxl")
        self.addSenderToIS05(legs, senderId)
        return senderId

    def addSenderToIS05(self, legs, senderId, transportParams=None):
        # Create an instance of an MXL sender - no destination selector and
        # no file factory (no SDP-equivalent manifest), unlike RtpSender.
        sender = MxlSender(self.logger, legs)
        # Pre-stage real values (from a static config bootstrap) before
        # activating, instead of coming up with "auto"/empty defaults -
        # see addSenderFromConfig()/DC_MXL_SENDERS_CONFIG_ENV above.
        if transportParams is not None:
            for leg, leg_params in enumerate(transportParams):
                for key, value in leg_params.items():
                    sender.setStagedParameter(value, key, leg=leg)
        controller = activationController(senderId, sender, self.facadeWrapper)
        sender.setActivateCallback(controller.activateSender)
        sender.activateStaged()
        self.manager.addSender(sender, senderId)

    def bootstrapStaticReceivers(self):
        config_path = os.environ.get(DC_MXL_RECEIVERS_CONFIG_ENV)
        if not config_path:
            return
        try:
            receivers_by_id = json.loads(Path(config_path).read_text())
        except (OSError, ValueError) as e:
            self.logger.writeWarning(
                "Could not load {}={}: {}".format(DC_MXL_RECEIVERS_CONFIG_ENV, config_path, e))
            return
        bootstrapped = 0
        for receiverId, entry in receivers_by_id.items():
            try:
                self.addReceiverFromConfig(receiverId, entry)
                bootstrapped += 1
            except Exception as e:
                self.logger.writeWarning(
                    "Failed to bootstrap static mxl receiver {}: {}".format(receiverId, e))
        self.logger.writeDebug(
            "Bootstrapped {}/{} static mxl receivers from {}={}".format(
                bootstrapped, len(receivers_by_id), DC_MXL_RECEIVERS_CONFIG_ENV, config_path))

    def addReceiverFromConfig(self, receiverId, entry):
        legs = entry.get('leg', 1)
        self._receiverTopology[receiverId] = {
            'input': entry.get('input'),
            'endpoints': entry.get('endpoints', []),
        }
        self.addReceiverToIS05(legs, receiverId)

    def addReceiver(self, legs):
        receiverId = str(uuid4())
        self.addReceiverToIS05(legs, receiverId)
        return receiverId

    def addReceiverToIS05(self, legs, receiverId):
        # Instantiate an IS-05 MXL receiver - its transport manager is
        # fixed (MxlTransportManager, no SDP ingestion), so unlike
        # RtpReceiver, MxlReceiver's own constructor already builds it.
        # Unlike MxlSender, MxlReceiver's own constructor does not
        # activateStaged() itself - done explicitly below either way.
        # No pre-staging here (unlike senders) - a receiver's
        # mxl_domain_id/mxl_flow_id only ever arrive via a real PATCH,
        # never from static config (see DC_MXL_RECEIVERS_CONFIG_ENV above).
        receiver = MxlReceiver(self.logger, legs)
        controller = activationController(receiverId, receiver, self.facadeWrapper)

        def onActivate():
            controller.activateReceiver()
            self._onReceiverActivated(receiver, receiverId)

        receiver.setActivateCallback(onActivate)
        self.manager.addReceiver(receiver, receiverId)
        # registerReceiver() must run before activateStaged() - the latter
        # calls onActivate() synchronously on this very first activation,
        # which (via controller.activateReceiver()) does
        # facadeWrapper.updateReceiver(receiverId) - a KeyError unless
        # facadeWrapper.receivers[receiverId] already exists, which is
        # exactly what registerReceiver() creates.
        self.facadeWrapper.registerReceiver(receiverId, "urn:x-nmos:transport:mxl")
        receiver.activateStaged()

    def _onReceiverActivated(self, receiver, receiverId):
        """Fires after every successful activation of this receiver - the
        initial bootstrap one and every later PATCH-triggered one alike,
        since AbstractDevice.activateStaged() reuses the same stored
        callback for the receiver's whole lifetime. No-ops unless this
        receiver came from static config (has known topology) AND its
        transport_params now carry a real mxl_domain_id/mxl_flow_id (never
        true at bootstrap time, since those aren't pre-staged anymore)."""
        topology = self._receiverTopology.get(receiverId)
        if not topology:
            return
        try:
            leg_params = receiver.activeToJson()[__tp__][0]
        except (KeyError, IndexError):
            return
        domain_id = leg_params.get('mxl_domain_id')
        flow_id = leg_params.get('mxl_flow_id')
        if not domain_id or not flow_id:
            # A PATCH clearing mxl_domain_id/mxl_flow_id back to null means
            # this stream is being removed - reverse whatever this
            # receiver's own _syncReceiver() last wired up, if anything.
            # Nothing to reverse for a receiver that was never actually
            # synced (e.g. the very first, still-null bootstrap activation).
            previous = self._receiverActiveFlow.pop(receiverId, None)
            if previous is None:
                return
            endpoint, prev_domain_id, prev_flow_id = previous
            threading.Thread(
                target=self._unsyncReceiver,
                args=(receiverId, endpoint, prev_domain_id, prev_flow_id, topology.get('input')),
                daemon=True,
            ).start()
            return
        # Only endpoints explicitly tagged "protocol": "mxl" are usable
        # here - the subscription URL this feeds is always mxl://..., so
        # any other protocol entry in this receiver's endpoints list
        # (present for some other purpose) must be skipped rather than
        # blindly taking endpoints[0].
        endpoints = [e for e in (topology.get('endpoints') or []) if e.get('protocol') == 'mxl']
        if not endpoints:
            self.logger.writeWarning(
                "Receiver {} activated with a real flow but has no mxl-protocol "
                "endpoint configured in {}".format(receiverId, DC_MXL_RECEIVERS_CONFIG_ENV))
            return
        self._receiverActiveFlow[receiverId] = (endpoints[0], domain_id, flow_id)
        threading.Thread(
            target=self._syncReceiver,
            args=(receiverId, endpoints[0], domain_id, flow_id, topology.get('input')),
            daemon=True,
        ).start()

    def _syncReceiver(self, receiverId, endpoint, domain_id, flow_id, input_index):
        """Runs off the request-handling thread so a PATCH response is
        never delayed by however long these take. Each step is
        independent and best-effort - a failure in one doesn't skip the
        others, and none of them ever raise back into the caller."""
        try:
            Path(domain_id).mkdir(parents=True, exist_ok=True)
            self.logger.writeDebug(
                "Receiver {}: ensured domain directory {} exists".format(receiverId, domain_id))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to create domain directory {}: {}".format(
                    receiverId, domain_id, e))

        try:
            self._upsertFabricsProxySubscription(endpoint, domain_id, flow_id)
            self.logger.writeDebug(
                "Receiver {}: upserted mxl-fabrics-proxy subscription for {} ({})".format(
                    receiverId, endpoint.get('host'), flow_id))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to update mxl-fabrics-proxy config: {}".format(receiverId, e))

        try:
            self._reloadFabricsProxy()
            self.logger.writeDebug("Receiver {}: reloaded mxl-fabrics-proxy".format(receiverId))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to reload mxl-fabrics-proxy: {}".format(receiverId, e))

        if input_index is None:
            return

        try:
            self._generateInputConfig(domain_id, flow_id, input_index)
            self.logger.writeDebug(
                "Receiver {}: regenerated input {} config from {}".format(
                    receiverId, input_index, domain_id))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to regenerate input {} config: {}".format(
                    receiverId, input_index, e))

        try:
            subprocess.run(["dc-input", "restart", str(input_index)],
                            check=True, timeout=60, capture_output=True)
            self.logger.writeDebug(
                "Receiver {}: restarted dc-input {}".format(receiverId, input_index))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to restart dc-input {}: {}".format(
                    receiverId, input_index, e))

    def _unsyncReceiver(self, receiverId, endpoint, domain_id, flow_id, input_index):
        """Reverse of _syncReceiver() - runs when a receiver's PATCH clears
        its mxl_domain_id/mxl_flow_id back to null (removing this stream),
        undoing each of _syncReceiver()'s steps against the (endpoint,
        domain_id, flow_id) it last synced with. Same execution model:
        off the request-handling thread, each step independent and
        best-effort. Never removes the domain directory itself - other
        flows may still be using it."""
        try:
            self._removeFabricsProxySubscription(endpoint, domain_id, flow_id)
            self.logger.writeDebug(
                "Receiver {}: removed mxl-fabrics-proxy subscription for {} ({})".format(
                    receiverId, endpoint.get('host'), flow_id))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to update mxl-fabrics-proxy config: {}".format(receiverId, e))

        try:
            self._reloadFabricsProxy()
            self.logger.writeDebug("Receiver {}: reloaded mxl-fabrics-proxy".format(receiverId))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to reload mxl-fabrics-proxy: {}".format(receiverId, e))

        if input_index is None:
            return

        try:
            self._removeFlowFromInput(domain_id, flow_id, input_index)
            self.logger.writeDebug(
                "Receiver {}: removed flow {} from input {} config".format(
                    receiverId, flow_id, input_index))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to remove flow {} from input {} config: {}".format(
                    receiverId, flow_id, input_index, e))

        try:
            subprocess.run(["dc-input", "restart", str(input_index)],
                            check=True, timeout=60, capture_output=True)
            self.logger.writeDebug(
                "Receiver {}: restarted dc-input {}".format(receiverId, input_index))
        except Exception as e:
            self.logger.writeWarning(
                "Receiver {}: failed to restart dc-input {}: {}".format(
                    receiverId, input_index, e))

    def _linkFlowIntoInput(self, domain_id, flow_id, input_index):
        """Symlinks this receiver's own flow into its input's own flow
        config dir - <flow_config_dir>/<flow_id> -> <domain_id>/<flow_id>.mxl-flow/flow_def.json,
        same convention make-mxl-config.py's own create_flow_symlinks()
        uses (a file symlink named after the flow's uuid, pointing at its
        real flow_def.json). Symlinks accumulate here across separate
        receiver activations (video/audio/data each land their own), which
        is exactly what _detectFlowsUnderInput() below then scans."""
        flow_config_dir = Path("/etc/mxl/flows/input/{}".format(input_index))
        flow_config_dir.mkdir(parents=True, exist_ok=True)
        symlink_path = flow_config_dir / flow_id
        symlink_path.unlink(missing_ok=True)
        symlink_path.symlink_to(
            (Path(domain_id) / "{}.mxl-flow".format(flow_id) / "flow_def.json").resolve())
        return flow_config_dir

    def _detectFlowsUnderInput(self, flow_config_dir):
        """Returns {"-v": path, "-a": path, "-d": path} for whatever
        essence types were found among <flow_config_dir>'s own flow-uuid
        symlinks (each one _linkFlowIntoInput() created, possibly across
        several receiver activations; config.json - written after this
        runs - is skipped by name) - the first flow of each type wins; a
        second flow of the same type is silently ignored rather than
        blocking."""
        found = {}
        for flow_def_path in sorted(Path(flow_config_dir).iterdir()):
            if flow_def_path.name == "config.json" or not flow_def_path.is_file():
                continue
            try:
                flow_def = json.loads(flow_def_path.read_text())
            except (ValueError, OSError):
                continue
            flag = FORMAT_TO_FLAG.get(flow_def.get("format"))
            if flag is None or flag in found:
                continue
            found[flag] = str(flow_def_path)
        return found

    def _waitForFlowDef(self, domain_id, flow_id, timeout=5.0, poll_interval=0.2):
        """mxl-fabrics-proxy's /v1/reload response only means it has
        re-read its subscription config, not that it has already
        established the subscription and pulled real grains from the
        remote source - <domain>/<flow_id>.mxl-flow/flow_def.json shows up
        asynchronously a short while after. Poll briefly rather than
        assume it's already there the instant reload() returns."""
        flow_def_path = Path(domain_id) / "{}.mxl-flow".format(flow_id) / "flow_def.json"
        deadline = time.monotonic() + timeout
        while not flow_def_path.is_file():
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for {} to appear".format(flow_def_path))
            time.sleep(poll_interval)

    def _generateInputConfig(self, domain_id, flow_id, input_index):
        self._waitForFlowDef(domain_id, flow_id)
        flow_config_dir = self._linkFlowIntoInput(domain_id, flow_id, input_index)
        # Detection must run after linking (so this activation's own flow
        # is included) and before calling make-mxl-config.py (which needs
        # concrete flow_def.json paths - it has no discovery of its own).
        found = self._detectFlowsUnderInput(flow_config_dir)
        if "-v" not in found:
            raise RuntimeError("no video flow found under {}".format(flow_config_dir))
        self._writeInputConfig(flow_config_dir, domain_id, found)

    def _writeInputConfig(self, flow_config_dir, domain_id, found):
        """Shared by _generateInputConfig() and _removeFlowFromInput() -
        calls make-mxl-config.py against whatever -v/-a/-d flows were
        found and writes its stdout to config.json."""
        # Full path, not a bare name relying on PATH - unlike dc-input
        # (installed to /usr/sbin/, part of systemd's default PATH),
        # make-mxl-config.py installs to /opt/dreamcatcher/sbin/, which
        # isn't on a systemd service's PATH unless added explicitly.
        command = ["/opt/dreamcatcher/sbin/make-mxl-config.py"]
        for flag in ("-v", "-a", "-d"):
            if flag in found:
                command += [flag, found[flag]]
        # Deliberately no mxl-flow-config-path argument here, even though
        # _linkFlowIntoInput() already uses the exact same symlink
        # convention create_flow_symlinks() would - the -v/-a/-d paths
        # just given ARE those symlinks, so make-mxl-config.py would
        # unlink() each one before resolve()-ing it to find its own
        # target, breaking the very path it was just asked to read.
        # Writing config.json ourselves from stdout avoids that.
        command += [domain_id]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError("make-mxl-config.py failed: {}".format(result.stderr.strip()))
        (flow_config_dir / "config.json").write_text(result.stdout)

    def _removeFlowFromInput(self, domain_id, flow_id, input_index):
        """Reverse of _generateInputConfig()/_linkFlowIntoInput() - unlinks
        this one flow's own symlink, then rebuilds config.json from
        whatever flows remain, or removes it entirely once no video flow
        is left (mirrors mxl-dcstranscode-input.sh's own contract of
        exiting when config.json is missing - an input with no video flow
        shouldn't keep running)."""
        flow_config_dir = Path("/etc/mxl/flows/input/{}".format(input_index))
        (flow_config_dir / flow_id).unlink(missing_ok=True)
        found = self._detectFlowsUnderInput(flow_config_dir)
        if "-v" not in found:
            (flow_config_dir / "config.json").unlink(missing_ok=True)
            return
        self._writeInputConfig(flow_config_dir, domain_id, found)

    def _resolveFabricsProxyConfigPath(self):
        """The real host-side path of mxl-fabrics-proxy's own config file.
        DC_MXL_FABRICS_PROXY_CONFIG_ENV is an explicit override if set;
        otherwise this is discovered directly from the running container
        itself (see _discoverFabricsProxyConfigPath()), so it's always
        correct for however the container actually happens to be started
        rather than needing an env var kept in sync by hand. Falls back
        to DEFAULT_FABRICS_PROXY_CONFIG (logged as a warning) if discovery
        fails for any reason - docker not installed, container not
        running, unexpected inspect output, etc."""
        override = os.environ.get(DC_MXL_FABRICS_PROXY_CONFIG_ENV)
        if override:
            return override
        try:
            return self._discoverFabricsProxyConfigPath()
        except Exception as e:
            self.logger.writeWarning(
                "Could not discover mxl-fabrics-proxy's real config path via docker "
                "inspect, falling back to {}: {}".format(DEFAULT_FABRICS_PROXY_CONFIG, e))
            return DEFAULT_FABRICS_PROXY_CONFIG

    def _discoverFabricsProxyConfigPath(self):
        """Asks docker directly for the real host-side path of the config
        file mxl-fabrics-proxy is actually running with: finds its own
        --config <container-path> arg (from `docker inspect`'s
        Config.Cmd), then resolves that container-side path through
        whichever bind mount covers it, to the real path on this host."""
        container = os.environ.get(DC_MXL_FABRICS_PROXY_CONTAINER_ENV, DEFAULT_FABRICS_PROXY_CONTAINER)
        result = subprocess.run(["docker", "inspect", container],
                                 capture_output=True, text=True, timeout=10, check=True)
        inspect_data = json.loads(result.stdout)[0]

        args = inspect_data.get("Config", {}).get("Cmd") or []
        container_config_path = None
        for i, arg in enumerate(args):
            if arg == "--config" and i + 1 < len(args):
                container_config_path = args[i + 1]
                break
        if container_config_path is None:
            raise RuntimeError("no --config arg found for container {}".format(container))

        for mount in inspect_data.get("Mounts", []):
            destination = mount.get("Destination", "")
            if not destination:
                continue
            if container_config_path == destination:
                return mount["Source"]
            prefix = destination.rstrip("/") + "/"
            if container_config_path.startswith(prefix):
                return str(Path(mount["Source"]) / container_config_path[len(prefix):])

        raise RuntimeError("no bind mount covers {} in container {}".format(
            container_config_path, container))

    def _upsertFabricsProxySubscription(self, endpoint, domain_id, flow_id):
        # The domains:/subscriptions: key is arbitrary per mxl-fabrics-
        # proxy's own docs, but must be used consistently as both (see
        # cross-dc-mxl-relay.md's own note) - derived from this receiver's
        # own local domain path (its basename), not a fixed constant,
        # since mxl_domain_id is a free-form string that need not always
        # be /dev/shm/mxl.
        domain_key = Path(domain_id).name
        config_path = Path(self._resolveFabricsProxyConfigPath())
        if config_path.is_file():
            config = yaml.safe_load(config_path.read_text()) or {}
        else:
            config = {}

        config.setdefault("domains", {}).setdefault(
            domain_key, {"url": "mxl://{}".format(domain_id)})
        entries = config.setdefault("subscriptions", {}).setdefault(domain_key, [])
        source_url = "mxl://{}:{}/{}".format(endpoint["host"], endpoint["port"], domain_key)

        for entry in entries:
            if urlparse(entry.get("url", "")).hostname == endpoint["host"]:
                entry["url"] = source_url
                ids = entry.setdefault("ids", [])
                if flow_id not in ids:
                    ids.append(flow_id)
                break
        else:
            entries.append({"url": source_url, "ids": [flow_id]})

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    def _removeFabricsProxySubscription(self, endpoint, domain_id, flow_id):
        """Reverse of _upsertFabricsProxySubscription() - removes flow_id
        from the matching subscription entry's ids, dropping the entry
        entirely once none are left (mirrors how upsert creates one on
        demand). Leaves the domains: entry alone - other flows may still
        be using that domain."""
        domain_key = Path(domain_id).name
        config_path = Path(self._resolveFabricsProxyConfigPath())
        if not config_path.is_file():
            return
        config = yaml.safe_load(config_path.read_text()) or {}
        entries = config.get("subscriptions", {}).get(domain_key)
        if not entries:
            return

        for entry in entries:
            if urlparse(entry.get("url", "")).hostname == endpoint["host"]:
                ids = entry.get("ids", [])
                if flow_id in ids:
                    ids.remove(flow_id)
                if not ids:
                    entries.remove(entry)
                break

        config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    def _reloadFabricsProxy(self):
        admin_url = os.environ.get(DC_MXL_FABRICS_PROXY_ADMIN_URL_ENV, DEFAULT_FABRICS_PROXY_ADMIN_URL)
        req = urllib.request.Request("{}/v1/reload".format(admin_url.rstrip("/")), method="POST")
        with urllib.request.urlopen(req, timeout=10):
            pass
