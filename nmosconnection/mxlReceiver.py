# Copyright 2017 British Broadcasting Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

import os
import json
import copy

from .abstractDevice import AbstractDevice
from .constants import SCHEMA_LOCAL

__location__ = os.path.realpath(
    os.path.join(os.getcwd(), os.path.dirname(__file__)))

__tp__ = 'transport_params'


class MxlTransportManager:
    """Stand-in for RtpReceiver's SdpManager, required because
    ConnectionManagementAPI.addReceiver() (api.py) unconditionally reads
    receiver.transportManagers[0] regardless of transport type. MXL has no
    SDP-equivalent manifest to ingest - a receiver already knows exactly
    what to subscribe to from its own transport_params (mxl_domain_id,
    mxl_flow_id) - so this always reports a null transport_file, per
    IS-05's own allowance for transport_file's "data"/"type" to be null
    when no transport file applies, and update() is a no-op (PATCHing a
    transport_file onto an MXL receiver is a no-op, not an error - the
    same "don't refuse, there's just nothing to do" stance RtpSender/
    RtpReceiver take when a driver-owned resource is out of their
    control)."""

    def __init__(self, logger, receiver):
        self.logger = logger
        self.receiver = receiver

    def getStagedRequest(self):
        return {"data": None, "type": None}

    def getActiveRequest(self):
        return {"data": None, "type": None}

    def lock(self):
        pass

    def unLock(self):
        pass

    def activateStaged(self):
        pass

    def update(self, updateObject):
        self.logger.writeDebug("MxlTransportManager.update() called - MXL receivers have no transport_file to apply, ignoring")


class MxlReceiver(AbstractDevice):
    """A Receiver that subscribes to a remote flow exposed by an MxlSender
    over mxl-fabrics-proxy, per AMWA BCP-007-03 (NMOS With MXL) - a pull
    model, where this receiver connects out using the remote sender's own
    mxl_domain_id/mxl_flow_id (unlike RtpReceiver, there's no local
    interface_ip/multicast_ip - the remote endpoint is always explicit)."""

    def __init__(self, logger, legs=1):
        super(MxlReceiver, self).__init__(logger)

        if int(legs) < 1 or int(legs) > 2:
            raise ValueError("Reciever may only support 1 or 2 legs")

        self.schemaPath = SCHEMA_LOCAL

        self.legs = legs
        self.transportManagers = []
        self.staged[__tp__] = list(range(legs))
        self.staged[__tp__][0] = {}
        if legs == 2:
            self.staged[__tp__][1] = {}

        # Set collection of parameters
        self.generalParams = ['mxl_domain_id', 'mxl_flow_id']

        # Set up default values as per spec.
        for leg in range(0, legs):
            self.transportManagers.append(MxlTransportManager(self.logger, self))
            self.staged[__tp__][leg]['mxl_domain_id'] = None
            self.staged[__tp__][leg]['mxl_flow_id'] = None
        self.staged['sender_id'] = None

        self._initConstraints()

    def _initConstraints(self):
        self.constraints = []
        for leg in range(0, self.legs):
            self.constraints.append({})
            for param in self.generalParams:
                self.constraints[leg][param] = {}

    def resolveParameters(self, parameterSet):
        """MXL receiver parameters (mxl_domain_id/mxl_flow_id) identify a
        specific remote sender to subscribe to and are never "auto" -
        there is nothing to resolve, so this just returns a copy
        unchanged (mirrors RtpSender/RtpReceiver's resolveParameters()
        shape, minus the "auto" lookup loop since it would never match)."""
        return copy.deepcopy(parameterSet)

    def _assembleJsonDescription(self, params):
        """Assemble a dictionary only of parameters required currently"""
        toReturn = copy.deepcopy(params)
        toReturn.pop('receiver_id')
        return toReturn

    def getParamsSchema(self, leg=0):
        """Get the schema of the transport params"""
        schema = self.schemaPath + 'v1.0_receiver_transport_params_mxl.json'
        try:
            schemaPath = os.path.join(__location__, schema)
            with open(schemaPath) as json_data:
                obj = json.loads(json_data.read())
        except EnvironmentError:
            raise IOError('failed to load schema file at: {}'.format(schemaPath))
        params = obj['items']['properties']
        # Merge in extra requirements required by constraints
        for key, entry in params.items():
            if key in self.constraints[leg]:
                entry.update(self.constraints[leg][key])
        obj['items']['properties'] = params
        return obj

    def getConstraints(self):
        return copy.deepcopy(self.constraints)

    def getActiveSenderID(self):
        return self.active['sender_id']

    def getTransportType(self):
        return "mxl"

    def _setTp(self, value, field, leg):
        q = [{}, {}]
        q[leg][field] = value
        return self.patch(q)
