FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends \
   openvswitch-switch \
   dos2unix \
   curl \
   && rm -rf /var/lib/apt/lists/*

ARG IMAGE
COPY fos /fos
COPY launch.py /
COPY $IMAGE /

HEALTHCHECK --interval=30s --timeout=240s --retries=3 CMD ["uv", "run", "/fos/fos_healthcheck.py"]

EXPOSE 22 161/udp 830 5000 10000-10099 3443 80 443
