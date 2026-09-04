# Running the tests

Always run from **this directory** (`tests/`), not the repo root - several
tests point `schemaPath` at `../share/ipp-connectionmanagement/schemas/`,
which only resolves correctly with `tests/` as the working directory.

## Full suite (needs the real `nmoscommon`)

Most of these tests exercise `nmosconnection`'s Flask/`nmoscommon`-based
API layer (`api.py`, `nmosDriver.py`, `service.py`), so they need the real
`nmoscommon` package - which itself needs an old `werkzeug`/`flask` pin
chain that doesn't `pip install` cleanly against a modern Python. Where
that's already set up (a venv built by `install.sh`, e.g. on a deployed
box under `/opt/dreamcatcher/venv/nmos-connection-management`):

```sh
<venv>/bin/python3 -m unittest discover -s . -v
```

or, from the repo root, via `tox` (also runs coverage, per `tox.ini`):

```sh
cd ..
tox
```

## MXL-only quick path (no `nmoscommon` needed)

The MXL device classes (`mxlSender.py`/`mxlReceiver.py`) only depend on
`jsonschema`/`six`, not `nmoscommon` - `testCM.py`'s
`TestMxlSenderBackend`/`TestMxlReceiverBackend` classes can run standalone
using the fake `nmoscommon.logger.Logger` in `stubs/` (see that file's
own comment for what it is and isn't a substitute for):

```sh
pip install -r requirements.txt
PYTHONPATH=stubs:.. python3 -m unittest \
    testCM.TestMxlSenderBackend testCM.TestMxlReceiverBackend -v
```

Only put `stubs` on `PYTHONPATH` for this kind of ad hoc run - never in an
environment where the real `nmoscommon` is installed (it would silently
shadow it), and it won't help the other test modules here
(`testRoutes.py`, `testRtpReceiver.py`, `testSdpManager.py`, ...), which
need much more of `nmoscommon` than just `logger`.
