# Minimal stand-in for nmoscommon.logger.Logger, for running testCM.py's
# MXL-only test classes (TestMxlSenderBackend/TestMxlReceiverBackend)
# without installing the real nmoscommon package - nmosconnection's own
# MXL code (mxlSender.py/mxlReceiver.py/abstractDevice.py) only ever
# calls write*() on whatever logger it's given, so a no-op print-based
# stand-in is enough to satisfy `from nmoscommon.logger import Logger`
# and exercise the real MXL logic.
#
# NOT a substitute for the real nmoscommon in general - other test modules
# in this directory (testRoutes.py, testRtpReceiver.py, testSdpManager.py,
# etc.) and nmosconnection/nmosDriver.py/api.py/service.py need much more
# of it (webapi, httpserver, auth, timestamp...). Only put this directory
# on sys.path deliberately (see tests/README.md) - never add it to
# PYTHONPATH in an environment where the real nmoscommon is installed, or
# it will silently shadow it.


class Logger(object):

    def __init__(self, *args, **kwargs):
        self.name = args[0] if args else kwargs.get('name', 'stub')

    def _write(self, level, msg):
        print("[{}] {}: {}".format(level, self.name, msg))

    def writeDebug(self, msg):
        self._write("DEBUG", msg)

    def writeInfo(self, msg):
        self._write("INFO", msg)

    def writeWarning(self, msg):
        self._write("WARNING", msg)

    def writeError(self, msg):
        self._write("ERROR", msg)

    def writeFatal(self, msg):
        self._write("FATAL", msg)
