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


class MxlSender(AbstractDevice):
    """A Sender exposing a local MXL domain over the network via
    mxl-fabrics-proxy, per AMWA BCP-007-03 (NMOS With MXL) - a pull
    model, where receivers subscribe using this sender's own
    mxl_domain_id/mxl_flow_id rather than the sender pushing to a
    destination (unlike RtpSender, there is no destination_ip/
    destination_port here)."""

    def __init__(self, logger, legs=1):
        super(MxlSender, self).__init__(logger)

        if int(legs) < 1 or int(legs) > 2:
            raise ValueError("Reciever may only support 1 or 2 legs")

        self.schemaPath = SCHEMA_LOCAL

        self.legs = legs
        self.staged[__tp__] = list(range(legs))
        self.staged[__tp__][0] = {}
        if legs == 2:
            self.staged[__tp__][1] = {}

        # Set collection of parameters
        self.generalParams = ['mxl_domain_id', 'mxl_flow_id']

        # Set up default values as per spec.
        for leg in range(0, legs):
            self.staged[__tp__][leg]['mxl_domain_id'] = None
            self.staged[__tp__][leg]['mxl_flow_id'] = None
        self.staged['receiver_id'] = None
        self.staged['master_enable'] = False
        self.transportFile = ""

        self._initConstraints()
        self.activateStaged()

    def _initConstraints(self):
        self.constraints = []
        for leg in range(0, self.legs):
            self.constraints.append({})
            for param in self.generalParams:
                self.constraints[leg][param] = {}

    def resolveParameters(self, parameterSet):
        """mxl_domain_id/mxl_flow_id identify a specific local domain/flow
        and are never resolved by this layer (mirrors RtpSender/
        RtpReceiver's resolveParameters() shape, minus the "auto" lookup
        loop since nothing here is auto-resolved)."""
        return copy.deepcopy(parameterSet)

    def _assembleJsonDescription(self, params):
        """Assemble a dictionary only of parameters required currently"""
        toReturn = copy.deepcopy(params)
        toReturn.pop('sender_id')
        return toReturn

    def getParamsSchema(self, leg=0):
        """Get the schema of the transport params"""
        schema = self.schemaPath + 'v1.0_sender_transport_params_mxl.json'
        try:
            schemaPath = os.path.join(__location__, schema)
            with open(schemaPath) as json_data:
                obj = json.loads(json_data.read())
        except EnvironmentError:
            raise IOError('failed to load schema file')
        params = obj['items']['properties']
        # Merge in extra requirements required by constraints
        for key, entry in params.items():
            if key in self.constraints[leg]:
                entry.update(self.constraints[leg][key])
        obj['items']['properties'] = params
        return obj

    def getConstraints(self):
        return copy.deepcopy(self.constraints)

    def getActiveTransportFileURL(self):
        return self.transportFile

    def getActiveReceiverID(self):
        return self.active['receiver_id']

    def getTransportType(self):
        return "mxl"

    def _setTp(self, value, field, leg):
        q = {__tp__: [{}, {}]}
        q[__tp__][leg][field] = value
        return self.patch(q)
