FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends \
   openvswitch-switch \
   dos2unix \
   curl \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY *.py /
COPY features /features
COPY $IMAGE /

# While FortiOS is still bootstrapping, /fos_healthcheck.py blocks (Docker
# keeps the container "starting" while a probe run is in flight) and only
# returns failure after FOS_HEALTHCHECK_STALL_SECONDS (default 90s) with no
# launcher heartbeat, so a slow-but-progressing boot never leaves "starting"
# and a wedged launcher is reported within ~90s of its last heartbeat.
# One blocked probe spans the whole bootstrap, so --timeout doubles as the
# overall startup grace: a beating launcher must stay in "starting" for
# >240s before the first timeout strike, i.e. it takes >13min of bootstrap
# to flip unhealthy. Wedge detection is independent of --timeout because
# the stall exit (90s) completes the probe long before it.
HEALTHCHECK --interval=30s --timeout=240s --retries=3 CMD ["uv", "run", "/fos_healthcheck.py"]

EXPOSE 22 161/udp 830 5000 10000-10099 3443 80 443
