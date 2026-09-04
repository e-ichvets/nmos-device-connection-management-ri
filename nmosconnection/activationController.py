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


class activationController:
    """Bridges a device's own activation callback to the IS-04 facade -
    shared by both nmosDriver.py's RTP path and dcMxlDriver.py's MXL
    path, so it lives in its own module rather than either one (avoids a
    circular import between the two)."""

    def __init__(self, portId, port, facadeWrapper, fileFactory=None):
        self.portId = portId
        self.port = port
        self.facadeWrapper = facadeWrapper
        self.fileFactory = fileFactory

    def activateSender(self):
        if self.fileFactory is not None:
            self.fileFactory.activateCallback()
        self.facadeWrapper.updateSender(self.portId)

    def activateReceiver(self):
        self.facadeWrapper.updateReceiver(self.portId)
