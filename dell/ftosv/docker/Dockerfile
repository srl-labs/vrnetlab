FROM ghcr.io/srl-labs/vrnetlab-base:0.3.0

# Pick up Debian fixes released after vrnetlab-base was built.
RUN apt-get update -qy \
   && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
   && rm -rf /var/lib/apt/lists/*

LABEL maintainer="Kristian Larsson <kristian@spritelink.net>"
LABEL maintainer="Roman Dodin <dodin.roman@gmail.com>"
LABEL maintainer="Stefano Sasso <stesasso@gmail.com>"

ARG IMAGE
COPY $IMAGE* /
COPY *.py /

EXPOSE 22 80 161/udp 443 830 5000 6030 10000-10099 57400
