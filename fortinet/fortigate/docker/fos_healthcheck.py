#!/usr/bin/env python3
"""Docker healthcheck probe for the FortiGate vrnetlab container.

The launcher appends one byte to the heartbeat file on every main-loop
iteration. While FortiOS is still in its initial bootstrap ("/health"
contains "1 starting") this probe blocks waiting for those heartbeat
bytes instead of returning. Docker keeps a container in the "starting"
health state for as long as a probe run has not completed, so a
bootstrap that keeps making progress never flaps the container to
"unhealthy" -- the starting -> unhealthy -> healthy blip caused by the
30 s/3-strike default timing during a multi-minute bootstrap.

If no new heartbeat byte arrives within STALL_SECONDS the launcher is
considered wedged and the probe exits 1; three consecutive stalled
probe runs (HEALTHCHECK --retries) then mark the container unhealthy.

Once the first bootstrap has completed, the launcher reports later
failures as "1 VM failed - restarting" instead of "1 starting". From
then on this probe defers to the classic vrnetlab behaviour: exit with
the status recorded in the health file immediately, so a VM that dies
after startup flips to unhealthy on schedule (3 x interval).

A missing health file is treated like "1 starting": the launcher has
not reached its main loop yet, so the stall clock applies.
"""

import os
import sys
import time

HEALTH_FILE = os.environ.get("FOS_HEALTH_FILE", "/health")
HEARTBEAT_FILE = os.environ.get("FOS_HEARTBEAT_FILE", "/healthbeat")
STALL_SECONDS = float(os.environ.get("FOS_HEALTHCHECK_STALL_SECONDS", "90"))

# How often a blocking probe re-checks the heartbeat file. Only the
# stall window accuracy depends on this; the daemon never sees it.
POLL_SECONDS = 0.5


def _read_health(health_file):
    """Return (exit_code, message), or None if it cannot be read.

    None covers a missing file and a truncated read (the launcher
    truncates-then-writes the file, so a probe racing that write can see
    empty or partial content). Both mean "state not known yet" -- the
    caller keeps blocking on the heartbeat rather than reporting a
    failure, so a torn read never produces a spurious unhealthy strike.
    """
    try:
        with open(health_file, "r") as health_handle:
            content = health_handle.read()
    except FileNotFoundError:
        return None
    parts = content.strip().split(" ", 1)
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), parts[1])
    except ValueError:
        return None


def _heartbeat_size(heartbeat_file):
    try:
        return os.stat(heartbeat_file).st_size
    except OSError:
        return 0


def run(
    health_file=HEALTH_FILE,
    heartbeat_file=HEARTBEAT_FILE,
    stall_seconds=STALL_SECONDS,
    *,
    sleep=time.sleep,
    monotonic=time.monotonic,
    poll_seconds=POLL_SECONDS,
):
    """Return the exit code the probe should terminate with."""
    while True:
        status = _read_health(health_file)

        if status is not None:
            exit_code, message = status
            if exit_code == 0:
                return 0
            # Anything the launcher reports besides the initial bootstrap
            # (e.g. "VM failed - restarting") is a real failure the moment
            # it is observed; hand it to Docker right away.
            if not (exit_code == 1 and message == "starting"):
                return 1

        # Still starting (or the launcher has not written /health yet):
        # stay silent until the heartbeat advances or progress stalls.
        seen = _heartbeat_size(heartbeat_file)
        stall_start = monotonic()
        while monotonic() - stall_start < stall_seconds:
            sleep(poll_seconds)
            if _heartbeat_size(heartbeat_file) != seen:
                break
        else:
            return 1


if __name__ == "__main__":
    sys.exit(run())
