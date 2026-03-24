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

## Containerlab topology

Pass-through management mode (`CLAB_MGMT_PASSTHROUGH: "true"`) is the **only
recommended mode** for running Apstra in containerlab. It bridges the
container's `eth0` directly into the Apstra VM so the VM receives the real
containerlab management IP (`172.20.20.10`) rather than an internal QEMU NAT
address. This is required for correct operation with both onbox and offbox
device agents — see [Management networking](#management-networking) for the
full explanation.

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
        QEMU_MEMORY: "16384"              # 16 GB minimum; increase to 32768 for production
        QEMU_SMP: "4"                     # vCPU count
        CLAB_MGMT_PASSTHROUGH: "true"     # recommended — bridges eth0 directly into the VM
      ports:
        - "22:22"                         # SSH CLI
        - "80:80"                         # HTTP (redirects to HTTPS)
        - "443:443"                       # Web UI + REST API

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

## First boot configuration

> **Important:** On first boot the Apstra VM does not configure its
> management interface automatically. You must connect to the serial console
> and complete the `aos_config` first-boot wizard before the Web UI or SSH
> are accessible.

### Step 1 — Wait for the container to become healthy

```bash
docker logs -f clab-apstra-lab-apstra
# Wait until you see the login prompt in the logs (2–3 minutes)

docker inspect --format='{{.State.Health.Status}}' clab-apstra-lab-apstra
# Wait for: healthy
```

### Step 2 — Connect to the serial console

```bash
# from the container host
telnet clab-apstra-lab-apstra 5000

# or from inside the container
docker exec -it clab-apstra-lab-apstra bash
telnet localhost 5000
```

The default credentials for the Apstra console are `admin` / `admin`.
Press Enter if you see a blank screen — the login prompt may not appear
automatically on the serial console.

### Step 3 — Complete the aos_config wizard

The first time you boot the Apstra server VM, a configuration tool opens
to assist you with basic settings. You can open this tool at any time with
the command `aos_config`.

Work through the wizard in this order:

**1. Change local credentials (required)**

Select **Local credentials** and follow the prompts to change the default
administrator password. The password must be at least 14 characters.

**2. Change Web UI credentials (required)**

Select **WebUI credentials** and change the default password for the
Apstra GUI user `admin`. The Apstra service must be running to change
the GUI password.

**3. Configure networking (required for pass-through mode)**

The network is configured to use DHCP by default. To assign a static IP
address instead, select **Network**, change it to **Manual**, and provide
the IP address in CIDR format (for example `172.20.20.10/24`), the gateway
(`172.20.20.1`), and DNS server.

Set the static IP to match the `mgmt-ipv4` address assigned in your
topology file (`172.20.20.10/24` in the example above).

**4. Start Apstra service (required)**

Apstra service is stopped by default. Select **AOS service** and select
**Start**. Starting service from this configuration tool invokes
`/etc/init.d/aos`, which is the equivalent of running `service aos start`.

**5. Exit the wizard**

To exit the configuration tool and return to the CLI, select **Cancel**
from the main menu. To open this tool again in the future, run the command
`aos_config`.

### Step 4 — Access the Web UI

Once the Apstra service is running, open a browser and navigate to:

```
https://172.20.20.10
```

> **Note:** Apstra uses a self-signed certificate by default. Accept the
> browser security warning to proceed. The Web UI credentials are the ones
> set in Step 3.2 above.

### Subsequent boots

After the first-boot wizard has been completed and the persistent overlay is
in place (`clab-apstra-lab/apstra/config/apstra_overlay.qcow2`), all
configuration — including network settings, passwords, and Apstra service
state — is preserved across `clab destroy` and `clab deploy` cycles. The
`aos_config` wizard only needs to be completed once.

## Management networking

Pass-through mode (`CLAB_MGMT_PASSTHROUGH: "true"`) is the only recommended
networking mode for Apstra in containerlab.

In the default host-forwarded mode QEMU's user-mode networking gives the
Apstra VM the internal address `10.0.0.15/24`. All traffic is NAT'd through
the container's `eth0`. While basic connectivity works, this mode causes
problems with onbox/offbox device agents: when Apstra registers an agent it
sends the agent a callback URL containing its own management IP. In
host-forwarded mode this URL contains `10.0.0.15` — an address that is
unreachable by any other node on the containerlab management network. The
agent cannot connect back to Apstra and registration fails.

Pass-through mode eliminates this problem entirely by giving the Apstra VM
the real containerlab management IP directly. It works correctly with both
onbox and offbox agents and avoids all NAT-related edge cases.

| | Host-forwarded | Pass-through (recommended) |
|---|---|---|
| Apstra VM IP | `10.0.0.15` (internal) | `172.20.20.10` (real clab IP) |
| Onbox/Offbox agents | ❌ Callback URL unreachable | ✅ Works |
| Static IP via `aos_config` | Not needed (NAT hides it) | Required on first boot |
| Inbound ping | Container responds | VM responds |

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

All Apstra configuration — including network settings, passwords, blueprints,
and device profiles — survives `clab destroy` and `clab deploy` cycles. The
original qcow2 baked into the Docker image is never modified.

To start completely fresh, delete the overlay before redeploying:

```bash
rm clab-apstra-lab/apstra/config/apstra_overlay.qcow2
sudo containerlab deploy -t apstra-lab.clab.yaml
# Note: the first-boot aos_config wizard must be completed again
```

## Managing Apstra nodes

**Web UI / REST API**
```
https://<mgmt-ip>    # primary interface — all Apstra configuration
http://<mgmt-ip>     # redirects to HTTPS
```

**SSH**
```bash
ssh admin@<mgmt-ip>
```

**Serial console** (first boot wizard and debugging)
```bash
# from the container host
telnet <container-name> 5000

# or from inside the container
docker exec -it <container-name> bash
telnet localhost 5000
```

## Debugging

**Container logs**:
```bash
docker logs -f clab-apstra-lab-apstra
```

**Health status**:
```bash
docker inspect --format='{{.State.Health.Status}}' clab-apstra-lab-apstra
# Expected: starting → (2–3 min) → healthy
```

**Verify persistent overlay is being used**:
```bash
docker exec clab-apstra-lab-apstra \
    cat /proc/$(docker exec clab-apstra-lab-apstra pgrep qemu)/cmdline \
    | tr '\0' '\n' | grep overlay
# Expected: if=ide,file=/config/apstra_overlay.qcow2
```

**Verify overlay size is growing**:
```bash
qemu-img info clab-apstra-lab/apstra/config/apstra_overlay.qcow2
```

## Interface naming

Apstra has no data-plane interfaces. Only the management interface is present
inside the VM (`eth0` / `ens3` / `enp0s3` depending on the kernel version).
Data interfaces must not be defined in the topology file.

## Known issues and limitations

- **First boot requires manual configuration**: The Apstra VM does not
  auto-configure its management interface on first boot. The `aos_config`
  wizard must be completed via the serial console before the Web UI or SSH
  are accessible. See [First boot configuration](#first-boot-configuration).
- **Device OS images not in overlay**: Apstra device OS images uploaded
  through the Web UI are stored separately and are not captured in the QEMU
  overlay. They must be re-uploaded after deploying from a fresh overlay.
- **Nested virtualisation required**: Apstra requires KVM hardware
  acceleration (`/dev/kvm`). It cannot run inside a VM that does not expose
  KVM to guest workloads.