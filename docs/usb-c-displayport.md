# Gaokun USB-C DisplayPort recovery

The kernel RPM release **11** includes
`patches/others/0004-usb-typec-ucsi-recover-Gaokun-initialization-and-DP.patch`.
Both the CI and local build scripts apply it to the upstream UCSI driver.
`drivers/gaokun-ec/ucsi_huawei_gaokun.c` is its source mirror; the patch-series
regression test verifies byte-for-byte equality after application.

## Failure and fix

On the tested release-10 EL2 kernel, initialization failed with:

```text
con1: failed to register alt modes
error -ETIMEDOUT: PPM init failed
ucsi connector is not initialized yet
```

The EC nevertheless reported this sideband snapshot:

```text
02 02 08 13 04 00 79 00
```

Port 0 was in four-lane DP mode with pin assignment C and HPD asserted.
The pending-update mask only contained port 1. Thus the mask is not a
connected-port bitmap and cannot select which states to replay at startup.
Replaying the state and delivering HPD to the existing display chain recovered
an M27P20 monitor at 3840x2160, 60 Hz, 30 bpp, four HBR3 lanes. Its 384-byte
EDID passed all three block checksums.

The permanent driver changes are:

- Establish and validate the fixed port topology from the device tree before
  any connector callbacks; defer EC communication until UCSI initialization.
- Queue sideband work from EC notifications. Never wait for a USB event or
  acquire the UCSI PPM mutex in the EC IRQ notifier: the same IRQ thread must
  deliver UCSI command completions.
- Wait for the asynchronous core initialization work before accessing
  `connector[]`. `ucsi_register()` returning zero alone is insufficient.
- Retry failed initialization three times, ten seconds apart, within the
  existing auxiliary device. Keep the DRM HPD bridge objects attached.
- Read and apply **all** port states after initialization and on subsequent
  sideband synchronization. ACK every pending port, including early events.
- Apply orientation before mux/HPD, propagate disconnects after leaving DP,
  suppress unchanged HPD notifications, and retry failed reads/mux/ACKs.
- Stop registration work, unregister the notifier, then stop sideband work
  before destroying UCSI state. Managed mux references cover probe failures.

The earlier, unbuilt local driver mirror has been replaced with this
upstream-based implementation. Merely copying the former mirror into a build
would not fix its asynchronous-readiness and initial-update-mask assumptions.

## Regression checks

```sh
python3 -m unittest discover -s tests -p test_ucsi_gaokun.py -v
GAOKUN_KERNEL_SRC=/path/to/pristine/linux-7.2.9 \
  python3 -m unittest discover -s tests -p test_kernel_patches.py -v
python3 -m unittest discover -s tests
```

The native C harness executes the driver's apply/sync/registration workers,
covering a preconnected monitor absent from the update mask, early events,
asynchronous failure and recovery, role-switch deferral, retry exhaustion,
reversed reconnect, disconnect, unchanged HPD, HPD IRQ, simultaneous updates,
and I/O/mux/ACK failures. The full patch test checks standard and EL2 series.

Hardware acceptance for a newly booted kernel includes a monitor attached at
boot, hotplug on both ports in both plug orientations, and suspend/resume.
Check `/sys/class/typec`, DRM connector `status`, `enabled`, EDID and kernel
logs; use the DRM DP debug information to verify the active mode and link.
Do not use auxiliary-driver unbind/bind as a persistent recovery mechanism:
it unplugs bridges already referenced by the live DRM chain.
