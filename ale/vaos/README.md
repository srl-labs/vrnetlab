# Alcatel-Lucent Enterprise OmniSwitch vAOS

This is the vrnetlab docker image for the ALE OmniSwitch virtual AOS (vAOS) 8.x.
Validated with vAOS 8.11.162.R01 (OS6870 simulation) and containerlab 0.77.0.

## Building the docker image

To get the vAOS image, contact your ALE representative or ask for it on the
[Spacewalkers community](https://www.spacewalkers.com/).

vAOS comes as a GNS3 appliance made of four files. Copy them into this
directory, renamed with a common `vaos-<version>` prefix:

| GNS3 appliance file           | Rename to                              |
|-------------------------------|----------------------------------------|
| `vAOS8x.qcow2`                | `vaos-8.11.162.R01.qcow2`              |
| `vAOS8x_ramdisk.gz`           | `vaos-8.11.162.R01-ramdisk.gz`         |
| `vAOS8x_bzImage`              | `vaos-8.11.162.R01-bzImage`            |
| `cmm_chassis_info_vAOS8x.tar` | `vaos-8.11.162.R01-chassis_info.tar`   |

Then run `make`. The resulting image is `vrnetlab/ale_vaos:8.11.162.R01`.

## Usage with containerlab

With the `ale_vaos` kind:

```yaml
topology:
  kinds:
    ale_vaos:
      image: vrnetlab/ale_vaos:8.11.162.R01
      env:
        CLAB_MGMT_PASSTHROUGH: "true"
  nodes:
    os1:
      kind: ale_vaos
      startup-config: os1.cfg    # optional
    os2:
      kind: ale_vaos
  links:
    - endpoints: ["os1:1/1/1", "os2:1/1/1"]
```

With containerlab versions that do not have the `ale_vaos` kind yet, use the
`generic_vm` kind and mount the startup configuration:

```yaml
    os1:
      kind: generic_vm
      image: vrnetlab/ale_vaos:8.11.162.R01
      binds:
        - os1.cfg:/startup-config.cfg:ro
```

Default credentials: `admin` / `Switch@123`.

A node needs about 3 minutes to boot and apply its configuration. It reports
healthy once the bootstrap described below is done.

### Interface mapping

| Container interface | vAOS port |
|---------------------|-----------|
| eth1 to eth10       | 1/1/1 to 1/1/10 |
| eth11               | 1/1/29A   |
| eth12               | 1/1/30A   |
| management          | EMP       |

### Bootstrap

Once the switch has booted (`+++ Link 1/1/1 operationally up` on the console),
the launcher logs in on the serial console and:

1. sets the system name and prompt to the node name,
2. configures EMP with the management address (container address in
   mgmt passthrough mode, 10.0.0.15/24 otherwise),
3. adds the static routes from `EMP_ROUTES`,
4. pushes the startup configuration: `/config/startup-config.cfg` (written by
   containerlab from the node `startup-config`) or `/startup-config.cfg` (bind
   mount with the `generic_vm` kind). AOS CLI, one command per line, lines
   starting with `!` are skipped,
5. runs `VAOS_EXTRA_CMDS`, then `write memory flash-synchro`.

Optional environment variables:

| Variable              | Default               | Description |
|-----------------------|-----------------------|-------------|
| `VAOS_USERNAME`       | `--username`, `admin`      | Console login |
| `VAOS_PASSWORD`       | `--password`, `Switch@123` | Console password |
| `EMP_IPV4`            | management address    | EMP address (CIDR) |
| `EMP_ROUTES`          |                       | `prefix=gateway` list, comma separated. A default route (0.0.0.0/0) is not allowed on EMP and is skipped |
| `VAOS_STARTUP_CONFIG` | see step 4 above      | Path of the startup configuration |
| `VAOS_EXTRA_CMDS`     |                       | Extra CLI commands, `;` separated |
| `VAOS_SAVE`           | `true`                | `false` skips `write memory flash-synchro` |
| `VAOS_BOOTSTRAP`      | `true`                | `false` skips the console bootstrap |

### Saving the configuration

With the containerlab `ale_vaos` kind, `containerlab save` creates
`/config/.vaos-save-request`. The launcher then runs `write memory flash-synchro`,
writes `show configuration snapshot` to `/config/startup-config.cfg` and reports
the result in `/config/.vaos-save-done`.

With mgmt passthrough, the serial console is reachable from inside the container:
`docker exec -it <node> telnet 127.0.0.1 5000`.
