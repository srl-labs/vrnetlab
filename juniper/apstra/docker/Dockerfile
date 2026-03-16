# ------------------------------------------------------------------------------
# Juniper Apstra - vrnetlab / srl-labs Dockerfile
#
# Inherits from the shared vrnetlab-base image which already contains:
#   - QEMU / KVM
#   - iproute2, bridge-utils, tcpdump, ethtool (networking tools)
#   - Python 3 with uv
#   - common/vrnetlab.py  (VM + VR base classes)
#   - common/healthcheck.py
# ------------------------------------------------------------------------------
FROM ghcr.io/srl-labs/vrnetlab-base:0.1.0

# ---- build-time arguments (injected by the Makefile) -------------------------
ARG VERSION
ARG IMAGE

# Make the version available at runtime (e.g. for debugging inside the container)
ENV VERSION=${VERSION}

# ---- copy the Apstra disk image and our launcher into the container ----------
# The Makefile copies the qcow2 into docker/ as part of docker-build-common,
# then Docker COPY picks it up from that build context.
COPY ${IMAGE} /
COPY *.py     /
RUN chmod +x /launch.py /healthcheck.py

# ---- expose management ports -------------------------------------------------
# 22    - SSH (for CLI access / Scrapli / Netmiko)
# 443   - Apstra Web UI (HTTPS)
# 8080  - Apstra Web UI (HTTP redirect / API)
# 5000  - QEMU serial console (telnet, internal use / debugging)
# 10000-10099 - Reserved for Containerlab port-forwarding
EXPOSE 22 80 443 5000 10000-10099

# ---- health check ------------------------------------------------------------
# Generous start-period: Apstra can take 8-10 minutes on first boot.
# The healthcheck.py script reads /health written by the VR main loop.
HEALTHCHECK \
    --interval=30s  \
    --timeout=10s   \
    --start-period=600s \
    --retries=5     \
    CMD ["/healthcheck.py"]

# ---- entry point -------------------------------------------------------------
ENTRYPOINT ["/launch.py"]
