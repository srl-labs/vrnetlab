FROM --platform=$BUILDPLATFORM public.ecr.aws/docker/library/golang:1.26-bookworm AS iouyap
RUN git clone https://github.com/clab-labs/iouyap-go /src
WORKDIR /src

ARG BUILDARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH=$BUILDARCH GOAMD64=v1 go build -trimpath -ldflags="-s -w" -o /iouyap .

FROM public.ecr.aws/docker/library/debian:bookworm-slim

# Install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    iproute2 \
    iputils-ping \
    net-tools \
    sudo \
    curl \
    ca-certificates \
    gnupg \
    && apt-get clean

COPY --from=iouyap /iouyap /usr/bin/iouyap

# Copy the entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set working directory
WORKDIR /iol

# Add the IOL image and config files
COPY *.bin /iol/iol.bin

# Make the IOL image and script executable
RUN chmod +x /iol/iol.bin

ENTRYPOINT ["/entrypoint.sh"]
