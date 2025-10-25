# Cisco Catalyst 9000V / Catalyst 9800-CL Wireless Controller

This is the vrnetlab image for the Cisco Catalyst 9000v (cat9kv, c9000v) and Cisco Catalyst 9800-CL Wireless Controller (c9800cl).

The Cat9kv emulates two types of ASICs that are found in the common Catalyst 9000 hardware platforms, either:

- UADP (Cisco Unified Access Data Plane)
- Cisco Silicon One Q200 (referred to as Q200 for short)

The Q200 is a newer ASIC, however doen't support as many features as the UADP ASIC emulation.

> Insufficient RAM will not allow the node to boot correctly.

Eight interfaces will always appear regardless if you have defined any links in the `*.clab.yaml` topology file. The Cat9kv requires 8 interfaces at minimum to boot, so dummy interfaces are created if there are an insufficient amount of interfaces (links) defined.

## Building the image

Copy the Cat9kv or C9800-CL .qcow2 file in this directory and you can perform `make docker-image`. On average the image takes approxmiately ~4 minutes to build as an initial install process occurs.

The build process automatically detects whether you're building a cat9kv or c9800cl image based on the filename:
- Files containing "c9800" build as `cisco_c9800cl:VERSION`
- Other files build as `cisco_cat9kv:VERSION`

For Cat9kv, the UADP and Q200 use the same .qcow2 image. The default image created is the UADP image.

> It is possible to tag the UADP and Q200 builds separately by prefixing the version in the filename. For example: `cat9kv_prd.Q200-x.y.z.qcow2` or `cat9kv_prd.UADP-x.y.z.qcow2`

To configure the Q200 image or enable a higher throughput dataplane for UADP; you must supply the relevant `vswitch.xml` file. You can place that file in this directory and build the image.

> You can obtain a `vswitch.xml` file from the relevant CML node definiton file.

Known working versions:

**Cat9kv:**
- cat9kv-prd-17.12.01prd9.qcow2 (UADP & Q200)

**C9800-CL:**
- The C9800-CL uses the same IOS-XE base as Cat9kv and should work with versions 17.x and newer
- Example filename: C9800-CL-universalk9.17.15.04b.qcow2

## Usage

You can define the image easily and use it in a topology. As mentioned earlier no links are required to be defined.

```yaml
# topology.clab.yaml for Cat9kv
name: mylab
topology:
  nodes:
    cat9kv:
      kind: cisco_cat9kv
      image: vrnetlab/cisco_cat9kv:<tag>

# topology.clab.yaml for C9800-CL
name: mylab
topology:
  nodes:
    c9800cl:
      kind: cisco_c9800cl
      image: vrnetlab/cisco_c9800cl:<tag>
```

You can also supply a vswitch.xml file using `binds` (for Cat9kv only). Below is an example topology file.

```yaml
# topology.clab.yaml
name: mylab
topology:
  nodes:
    cat9kv:
      kind: cisco_cat9kv
      image: vrnetlab/cisco_cat9kv:<tag>
      binds:
        - /path/to/vswitch.xml:/vswitch.xml
```

**Note:** The C9800-CL is a wireless LAN controller and does not require vswitch.xml configuration.

### Interface naming

Currently a maximum of 8 data-plane interfaces are supported. 9 interfaces total if including the management interface.

- `eth0` - Node management interface
- `eth1` - First dataplane interface (GigabitEthernet1/0/1).
- `ethX` - Subsequent dataplane interfaces will count onwards from 1. For example, the third dataplane interface will be `eth3`

You can also use interface aliases of `GigabitEthernet1/0/x` or `Gi1/0/x`

### Environment Variables

| Environment Variable  | Default       |
| --------------------- | ------------- |
| VCPU                  | 4             |
| RAM                   | 18432         |

### Example

```yaml
name: my-example-lab
topology:
  nodes:
    my-cat9kv:
      kind: cisco_cat9kv
      image: vrnetlab/vr-cat9kv:17.12.01
    env:
        VCPU: 6
        RAM: 12288
```

## System requirements

|           | Cat9kv UADP (Default) | Cat9kv Q200 | C9800-CL |
| --------- | --------------------- | ----------- | -------- |
| vCPU      | 4                     | 4           | 4        |
| RAM (MB)  | 18432                 | 12288       | 4096     |
| Disk (GB) | 4                     | 4           | 4        |
