# Fortinet Fortigate

Support for the Fortinet Fortigate launched by containerlab.

## Building the docker image

Add your qcow2 image to the root of this folder.
Naming format: fortios-vX.Y.Z.qcow2

`make`

## Running the docker image manually

If you need to run the image without using containerlab:

`make docker-run-fortigate`

## Credentials

The launcher logs in with `--username` / `--password`, defaulting to
`admin` / `admin`.

FortiOS forces a password change on the first login, and from 7.6 it also
ships a password policy enabled by default that requires at least 12
characters including an upper case letter, a lower case letter, a digit and a
non-alphanumeric character. A weak password such as the `admin` default cannot
satisfy that, so the launcher completes the forced change with a compliant
temporary password, disables the password policy, and then applies the
requested password. Disabling the policy is what allows `admin` / `admin` to
keep working on 7.6 and later.

## Tested versions

* Fortigate 7.0.14 KVM
* Fortigate 7.4.12 KVM
* Fortigate 7.6.7 KVM
* Fortigate 8.0.0 KVM

These were all tested without a licence installed. An unlicensed FortiGate-VM
boots, accepts configuration, and serves SSH and the GUI; it reports
`License Status: Invalid` and FortiGuard-dependent features are unavailable.
