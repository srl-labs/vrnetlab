# vrnetlab / NetApp Simulate ONTAP

This is the vrnetlab docker image for NetApp Simulate ONTAP (the "vsim"),
the ONTAP simulator NetApp distributes to customers and partners.

## Building the docker image

Download the simulator OVA from the [NetApp Support Site](https://mysupport.netapp.com/)
(Tools > Simulate ONTAP; a customer or partner login is required), copy it to
this directory and run `make`:

```
cp /path/to/vsim-netapp-DOT9.17.1-cm_nodar.ova .
make
```

The build unpacks the OVA, converts its four VMDK disks to qcow2 and produces
`vrnetlab/netapp_ontap:<version>`, where the version is taken from the file
name (`vsim-netapp-DOT9.17.1-cm_nodar.ova` -> `9.17.1`).

Tested with:

* vsim-netapp-DOT9.17.1-cm_nodar.ova

## Usage

```
docker run -d --privileged --name ontap1 vrnetlab/netapp_ontap:9.17.1 --username admin --password Netapp123 --hostname cluster1
```

On the first boot the launcher interrupts the simulator boot loader to make
the serial port the primary console, boots ONTAP and drives the cluster setup
wizard to create a single-node cluster. This takes 5-8 minutes. After that:

* the cluster management LIF holds the management address (reachable with
  ssh, https / System Manager and the REST API on the container's address),
* the node management LIF holds the next address of the management subnet,
* every simulated disk is assigned to the node (`storage disk assign -all`),
* the serial console is available on TCP port 5000.

The password must satisfy the ONTAP password policy: at least 8 characters
with letters and digits, and it must not contain the username. If a username
other than `admin` is given, an extra cluster administrator with that name is
created (`admin` always exists).

### Startup config

Lines of a file mounted at `/config/startup-config.cfg` (containerlab's
`startup-config`) are sent to the cluster shell after the cluster setup, one
command per line. This is the place for feature license keys and any
day-one configuration:

```
system license add -license-code XXXXXXXXXXXXXXXXXXXXXXXXXXXX
vserver create -vserver svm1 -rootvolume svm1_root -aggregate aggr1
```

### Interface groups (LACP)

ONTAP gives an interface group (ifgrp) the MAC address of `e0a` and relies
on the member NICs accepting frames for that address through their unicast
filter, which the emulated e1000 NICs do not (the same limitation exists on
VMware: multimode and LACP ifgroups stopped passing traffic on the simulator
with ONTAP 8.3). List the ports you will put into ifgroups in
`ONTAP_LAG_PORTS` and the launcher gives those NICs e0a's MAC address, so
`multimode_lacp` ifgroups work:

```yaml
      env:
        ONTAP_LAG_PORTS: "e0d,e0e,e0f,e0g"
```

### Persistent disks

The simulator's disk overlays live in the container by default and go
away with it. Mount a directory at `/persist` (containerlab `binds:`) and
the overlays are created there instead, so a re-created container boots
the same cluster with its configuration and volumes. Halt ONTAP before the
container is removed (`system node halt -node * -ignore-quorum-warnings
true -skip-lif-migration-before-shutdown true`; the simulator stops at its
boot loader): a container killed under a running ONTAP can lose state that
was not yet committed to disk.

```yaml
      binds:
        - nodes/ontap1:/persist
```

### Multi-node clusters

Connect `eth1` and `eth2` of the nodes to each other (a two-node switchless
cluster) or to a cluster switch, and set these environment variables:

* `ONTAP_CLUSTER_LIFS` - the addresses of this node's two cluster LIFs on
  e0a/e0b (comma separated, 169.254.0.0/16), e.g. `169.254.1.1,169.254.1.2`.
  Pinning them makes the cluster network predictable; without it the
  simulator generates random link-local addresses.
* `ONTAP_NODE_INDEX` - `1`, `2`, ... on every node but the first. A member
  node only configures its node management LIF and waits; the first node
  (index 0, the default) creates the cluster and adds the members.
* `ONTAP_ADD_NODES` - on the first node: the first cluster LIF address of
  every member (comma separated). The first node waits for each member to
  appear on the cluster network and runs `cluster add-node` for it.
* `ONTAP_CLUSTER_NAME` - the cluster name (default: the hostname of the first
  node); the nodes are named `<cluster>-01`, `<cluster>-02`, ...
* `ONTAP_SYSID` / `ONTAP_SERIAL` - every simulator OVA ships with the same
  system id and serial number, so each member needs the values NetApp
  provides with the simulator licenses, e.g. `4034389062` and
  `4034389-06-2`. They are set in the boot loader (`bootarg.nvram.sysid`,
  `SYS_SERIAL_NUM`).

Every LIF address must be unique in the cluster, so in the default
host-forwarded management mode the first node uses 10.0.0.15 (cluster
management, forwarded from the container address) and 10.0.0.16 (node
management), and member node *n* uses 10.0.0.16+*n* for its node management
LIF, which is what its container address forwards to.

containerlab example:

```yaml
  nodes:
    n1:
      kind: generic_vm
      image: vrnetlab/netapp_ontap:9.17.1
      env:
        USERNAME: admin
        PASSWORD: Netapp123
        ONTAP_CLUSTER_LIFS: "169.254.1.1,169.254.1.2"
        ONTAP_ADD_NODES: "169.254.2.1"
    n2:
      kind: generic_vm
      image: vrnetlab/netapp_ontap:9.17.1
      env:
        USERNAME: admin
        PASSWORD: Netapp123
        ONTAP_NODE_INDEX: "1"
        ONTAP_SYSID: "4034389062"
        ONTAP_SERIAL: "4034389-06-2"
        ONTAP_CLUSTER_LIFS: "169.254.2.1,169.254.2.2"
  links:
    - endpoints: ["n1:eth1", "n2:eth1"]
    - endpoints: ["n1:eth2", "n2:eth2"]
```

The simulator has no HA pair: a two-node cluster gets no storage failover
(the wizard reports "SFO will not be enabled on a non-HA system").

## Interface mapping

| Container interface | ONTAP port | Role                       |
|---------------------|------------|----------------------------|
| eth0                | e0c        | node / cluster management  |
| eth1                | e0a        | cluster interconnect       |
| eth2                | e0b        | cluster interconnect       |
| eth3                | e0d        | data                       |
| eth4                | e0e        | data                       |
| ethN                | e0(N+1)    | data                       |

The management NIC is pinned to the third PCI slot so that ONTAP names it
`e0c`, which is where the simulator expects the node management LIF. `eth1`
and `eth2` are the cluster ports (connect them node to node, or to a cluster
switch, for a multi-node cluster).

## System requirements

CPU: 2 cores
RAM: 6 GB (NetApp's OVA default; override with `QEMU_MEMORY`). Under a real
workload (several NFS volumes plus iSCSI LUNs served to a Kubernetes cluster,
a SnapMirror baseline) a 6 GB node logs `wafl.memory.statusVeryLowMemory` and
can stop answering after an hour; `QEMU_MEMORY=8192` fixed that in testing.
DISK: 1.5 GB for the image; the simulated disk shelf is a sparse 230 GB disk
