# Cisco vIOS

This is the vrnetlab docker image for Cisco vIOS router and vIOSL2 switch.

## Justification

Cisco vIOS is a virtual router and vIOSL2 is a virtual switch that can be used for testing and development purposes.
They are older than IOS XE and IOS XR (running only 15.x IOS version), however, they have several advantages:

- Small memory footprint (512MB for router, 768MB for switch vs 4GB+ for IOS XE/XR). With KSM enabled, the memory usage can be even lower.
- Easy to run on a laptop or a small server with limited resources for education purposes.
- Good for scalability testing of applications, when you don't need all new features of IOS XE/XR.

## Building the docker image

Qemu disk image can be obtained from Cisco Modeling Labs (CML).
More information about Cisco vIOS:
<https://developer.cisco.com/docs/modeling-labs/iosv/#iosv>

Once you extract disk image, format the name to one of the following formats:

**For vIOS Router:**
`cisco_vios-[VERSION].qcow2`
Where `[VERSION]` is the desired version of the image, for example `15.6.3M1` or `159-3.M10`.

**For vIOSL2 Switch:**
`cisco_viosl2-[VERSION].qcow2` or `vios_l2-*_[YYYYMMDD].qcow2`
Where `[VERSION]` is the desired version of the image, for example `15.2`.
For CML images with naming like `vios_l2-adventerprisek9-m.ssa.high_iron_20200929.qcow2`, the version will be extracted as the date `20200929`.

Finally, you can build the docker image with the `make docker-image` command.

The resulting images will be tagged as:
- Router: `vrnetlab/cisco_vios:159-3.M10`
- Switch: `vrnetlab/cisco_vios:L2-20200929` (note the "L2-" prefix)

Tested with versions:

- vIOS Router: 15.9.3M6 / 159-3.M10
- vIOSL2 Switch: L2-15.2 / L2-20200929

## System requirements

**vIOS Router:**
- CPU: 1 core
- RAM: 512MB
- Disk: <1GB

**vIOSL2 Switch:**
- CPU: 1 core
- RAM: 768MB
- Disk: <1GB

## Network interfaces

Both vIOS router and vIOSL2 switch support up to 16 GigabitEthernet interfaces.

- The first interface `GigaEthernet0/0` is used as the management interface (it is placed in separated VRF).
- The rest of the interfaces are numbered from `GigaEthernet0/1` and are used as data interfaces.
  They are mapped to the docker container interfaces `eth1`, `eth2`, etc.

## Management plane

The following protocols are enabled on the management interface:

- CLI SSH on port 22
- NETCONF via SSH on port 22 (the same credentials are used as for CLI SSH)
- SNMPv2c on port 161 (`public` used as community string)

## Environment variables

| ID              | Description                   | Default    |
|-----------------|-------------------------------|------------|
| USERNAME        | SSH username                  | admin      |
| PASSWORD        | SSH password                  | admin      |
| HOSTNAME        | device hostname               | vios       |
| DEVICE_TYPE     | device type (router or switch)| auto-detected from image filename |
| TRACE           | enable trace logging          | false      |
| CONNECTION_MODE | interface connection mode     | tc         |

**Note:** Images built from `viosl2` or `vios_l2` filenames automatically default to switch mode. You can override this by explicitly setting `DEVICE_TYPE=router` if needed.

## Configuration persistence

The startup configuration can be provided by mounting a file to `/config/startup-config.cfg`.
The changes done in the router configuration during runtime are not automatically persisted outside
the container - after stopping the container, the content of the flash/NVRAM is lost.
User is responsible for persistence of the changes, for example, by copying the configuration
to mounted startup-configuration file.

## Sample containerlab topology

**vIOS Router:**
```yaml
name: vios-lab

topology:
  kinds:
    linux:
      image: vrnetlab/cisco_vios:15.9.3M6
  nodes:
    vios1:
      kind: linux
      binds:
        - vios1.cfg:/config/startup-config.cfg
      env:
        HOSTNAME: router1
    vios2:
      kind: linux
      binds:
        - vios2.cfg:/config/startup-config.cfg
      env:
        HOSTNAME: router2
  links:
    - endpoints: ["vios1:eth1","vios2:eth1"]
```

**vIOSL2 Switch:**
```yaml
name: viosl2-lab

topology:
  kinds:
    linux:
      image: vrnetlab/cisco_vios:L2-20200929
  nodes:
    switch1:
      kind: linux
      binds:
        - switch1.cfg:/config/startup-config.cfg
      env:
        HOSTNAME: switch1
        # DEVICE_TYPE: switch is optional - auto-detected from image
    switch2:
      kind: linux
      binds:
        - switch2.cfg:/config/startup-config.cfg
      env:
        HOSTNAME: switch2
        # DEVICE_TYPE: switch is optional - auto-detected from image
  links:
    - endpoints: ["switch1:eth1","switch2:eth1"]
```
