FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0
LABEL org.opencontainers.image.authors="roman@dodin.dev"

RUN apt-get update -qy \
   && apt-get install -y --no-install-recommends \
   procps \
   && rm -rf /var/lib/apt/lists/*

COPY vmx /vmx
COPY *.py /

EXPOSE 22 161/udp 830 5000 10000-10099 57400
