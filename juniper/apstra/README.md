# Juniper Apstra — vrnetlab / srl-labs container

Containerizes the Juniper Apstra KVM appliance using the
[srl-labs/vrnetlab](https://github.com/srl-labs/vrnetlab) framework.
Apstra is a management and orchestration appliance — it has no data-plane
interfaces and is managed exclusively through its Web UI and REST API.

## Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 16 GB | 32 GB |
| vCPU | 4 | 8 |
| Disk (overlay) | ~200 MB initial, grows with config | — |

## How to obtain the image

Download the Apstra KVM qcow2 image (`aos_server_<version>.qcow2`) from the
[Juniper support portal](https://support.juniper.net/support/downloads/).
A Juniper account is required.

## Build instructions

```bash
# 1. Place the qcow2 in the vrnetlab/juniper/apstra/ directory
cp /path/to/aos_server_6.1.1-70.qcow2 vrnetlab/juniper/apstra/

# 2. Build
cd vrnetlab/juniper/apstra/
make

# Resulting image tag: vrnetlab/juniper_apstra:6.1.1-70

# 3. (Optional) push to a private registry
DOCKER_REGISTRY=myregistry.example.com:5000/vrnetlab make docker-push
```

## Test version extraction before building

```bash
make version-test IMAGE=aos_server_6.1.1-70.qcow2
# Expected output: 6.1.1-70
```

## Managing Apstra nodes

Apstra nodes launched with containerlab can be managed via the following
interfaces:

**Web UI / REST API**
```
https://<mgmt-ip>    # primary interface — all Apstra configuration
http://<mgmt-ip>     # redirects to HTTPS
```

**SSH CLI**
```bash
ssh admin@<mgmt-ip>
```

**Serial console** (for debugging, while container is running)
```bash
# from the container host directly
telnet <container-name> 5000

# or from inside the container
docker exec -it <container-name> bash
telnet localhost 5000
```

Default credentials: `admin` / `admin`
Apstra will prompt you to change the password on first login.

## Containerlab topology example

```yaml
# apstra-lab.clab.yaml
name: apstra-lab

mgmt:
  network: apstra-mgmt
  ipv4-subnet: 172.20.20.0/24
  ipv4-gw: 172.20.20.1

topology:
  nodes:

    apstra:
      kind: generic_vm
      image: vrnetlab/juniper_apstra:6.1.1-70
      mgmt-ipv4: 172.20.20.10
      env:
        QEMU_MEMORY: "16384"   # 16 GB minimum; increase to 32768 for production
        QEMU_SMP: "4"          # vCPU count
      ports:
        - "22:22"              # SSH CLI
        - "80:80"              # HTTP (redirects to HTTPS)
        - "443:443"            # Web UI + REST API

    switch1:
      kind: juniper_vjunosswitch
      image: vrnetlab/juniper_vjunosswitch:<version>
      mgmt-ipv4: 172.20.20.11

    switch2:
      kind: juniper_vjunosswitch
      image: vrnetlab/juniper_vjunosswitch:<version>
      mgmt-ipv4: 172.20.20.12

  links: []
```

Deploy:
```bash
sudo containerlab deploy -t apstra-lab.clab.yaml
```

> **Note:** Boot time is 2-3 minutes until the Container is marked `healthy`.
> 
> Monitor progress with `docker logs -f clab-apstra-lab-apstra`.

Access the device using `ssh admin@<mgmt-ip>` once the container is healthy in order to perform initial configuration.

## Persistent VM state

The `generic_vm` kind automatically bind-mounts
`clab-<labname>/<nodename>/config/` to `/config` inside the container.
`launch.py` detects this mount and creates the QEMU overlay disk there:

```
clab-apstra-lab/
└── apstra/
    └── config/
        └── apstra_overlay.qcow2   ← all VM writes go here
```

This means all Apstra configuration survives `clab destroy` and `clab deploy`
cycles. The original qcow2 baked into the Docker image is never modified.

To start fresh, delete the overlay before redeploying:
```bash
rm clab-apstra-lab/apstra/config/apstra_overlay.qcow2
sudo containerlab deploy -t apstra-lab.clab.yaml
```

## Networking

Apstra uses **host-forwarded management mode** (QEMU user-mode networking).
The VM receives `10.0.0.15/24` internally from QEMU's built-in DHCP server.
All traffic is NAT'd through the container's `eth0` (`172.20.20.10`).

The following ports are forwarded inbound from the container IP into the VM:

| Port | Protocol | Description |
|---|---|---|
| 22 | TCP | SSH |
| 80 | TCP | HTTP (redirects to HTTPS) |
| 443 | TCP | Web UI + REST API |
| 161 | UDP | SNMP |
| 830 | TCP | NETCONF |
| 29730–29739 | TCP | Agent binary protocol (Sysdb) |

> **Note:** ICMP (ping) to the Apstra container IP is not forwarded into the
> VM in host-forwarded mode. This is expected behaviour — use SSH or the
> Web UI to verify connectivity instead.

## Debugging

**Container logs** (boot progress from launch.py):
```bash
docker logs -f clab-apstra-lab-apstra
```

**Health status**:
```bash
docker inspect --format='{{.State.Health.Status}}' clab-apstra-lab-apstra
```
Expected progression: `starting` → (2–3 min) → `healthy`

**Verify persistent overlay is being used**:
```bash
docker exec clab-apstra-lab-apstra \
    cat /proc/$(docker exec clab-apstra-lab-apstra pgrep qemu)/cmdline \
    | tr '\0' '\n' | grep overlay
# Expected: if=ide,file=/config/apstra_overlay.qcow2
```

**Verify overlay size is growing** (confirms VM writes are going to the
persistent overlay and not an ephemeral layer):
```bash
qemu-img info clab-apstra-lab/apstra/config/apstra_overlay.qcow2
```

## Interface naming

Apstra has no data-plane interfaces. Only the management interface is
present inside the VM (`eth0` / `ens3` / `enp0s3` depending on the kernel
version). Data interfaces must not be defined in the topology file.

## Known issues and limitations

- **ICMP behaviour**: Pinging the Apstra container IP (`172.20.20.10`) works
  from the containerlab host and other nodes because the container's network
  stack responds to ICMP directly. The QEMU VM itself (`10.0.0.15` internally)
  does not respond to ping since ICMP is not forwarded by QEMU user-mode
  networking — but this is not relevant for normal lab use.
- **Device OS images not in overlay**: Apstra device OS images uploaded
  through the UI are stored separately and are not captured in the QEMU
  overlay. They must be re-uploaded after a fresh deploy.
- **Nested virtualisation required**: Apstra requires KVM hardware
  acceleration. It cannot run inside a VM that does not expose `/dev/kvm`.
