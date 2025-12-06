FROM public.ecr.aws/docker/library/debian:trixie-slim
LABEL org.opencontainers.image.authors="roman@dodin.dev"

COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /uvx /bin/

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qy \
   && apt-get upgrade -qy \
   && apt-get install -y \
   bridge-utils \
   iproute2 \
   python3-ipy \
   socat \
   qemu-kvm \
   qemu-system-x86 \
   qemu-efi-aarch64 \
   tcpdump \
   ssh \
   inetutils-ping \
   dnsutils \
   iptables \
   nftables \
   telnet \
   ftp \
   && rm -rf /var/lib/apt/lists/*

# use uv to install python 3.12. trixie ships with 3.13, which has no telnet package
RUN uv python install 3.12
RUN uv python pin 3.12

ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 161/udp 830 5000 5678 8291 10000-10099
HEALTHCHECK CMD ["uv", "run", "/healthcheck.py"]
ENTRYPOINT ["uv", "run", "/launch.py"]
