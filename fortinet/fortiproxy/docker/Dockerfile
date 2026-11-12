FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

RUN apt-get update -qy \
   && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
   && apt-get install -y --no-install-recommends \
   openvswitch-switch \
   dos2unix \
   curl \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY common /common
COPY launch.py /
COPY $IMAGE /

HEALTHCHECK --interval=30s --timeout=240s --retries=3 CMD ["uv", "run", "/common/fos_healthcheck.py"]

EXPOSE 22 161/udp 830 5000 10000-10099 3443 80 443
