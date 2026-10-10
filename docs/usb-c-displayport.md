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

## Physical unplug observation on release 10

With the monitor on DRM DP-2, a recorded physical unplug removed
`port1-partner` from `/sys/class/typec`, while DP-2 remained
`connected`/`enabled` and `DP1 Jack` remained on. The laptop desktop was
responsive, but applications remained on the nonexistent external desktop.
The bounded DRM-audio/jack trace recorded no disconnect notification.

Temporarily forcing the unplugged connector off made Mutter return to a
single internal display and made `DP1 Jack` report off. This is a per-boot
recovery, not a successful hotplug acceptance test; automatic detection must
be restored before the next connected test.

The release-10 source only calls `drm_aux_hpd_bridge_notify()` when the
**new** SVID is DisplayPort. Leaving DP can therefore skip the disconnect
notification. The release-11 patch instead derives HPD from both the current
SVID and HPD level, and drops previously asserted HPD before changing the mux.
The regression harness explicitly tests port 1 leaving DP with a stale HPD
bit, only the other port in the update mask, notification before mux teardown,
and no changes to the still-connected port 0. Hardware validation of this
patch remains required; the observed test ran the original release-10 driver.

## GDM/session handoff is a separate failure

A later trace on Mutter 50.5 identified the exact login/greeter rejection:
`drm_atomic_plane_check()` reports **switching CRTC directly** for primary
planes 43 and 49. The former session can exit normally while the new greeter
or desktop fails to draw. In a recorded retry, both CRTCs became disabled
and were then enabled successfully without a physical unplug.

The selected compatibility approach is now the Gaokun3-only DPU patch
`patches/others/0011-drm-msm-dpu-pin-Gaokun3-primary-planes.patch`. It gives
primary plane *i* only CRTC *i* in `possible_crtcs`, matching the driver's
existing `dpu_crtc_init()` pairing. Both virtual and physical plane creation
use the same restriction. Other boards, cursor/overlay planes, encoder
routing, CRTC count and virtual SSPP allocation retain their existing behavior.
DRM's safety check remains intact; the system Mutter package is unchanged.

Native C tests cover 1–8 CRTCs, exact board matching, non-primary masks and
cross-CRTC exclusion. The full standard/EL2 series applies to pristine Linux
7.2.9. Hardware acceptance still requires connected startup, GDM/user handoff,
both USB-C ports, mirror/extended layouts, hotplug and suspend/resume.

The source-only Mutter alternative remains documented in
[`packaging/mutter/README.md`](../packaging/mutter/README.md) as reference; it
is not applied by kernel/package workflows.

### Release-12 isolated test build

The RPM workflow accepts `test_suffix=-dp12` to produce separate kernel/module
namespaces (`7.2.9-dp12-gaokun3+` and `7.2.9-dp12-gaokun3-el2+`, with the SCM
suffix determined by Kbuild). This uses a source `localversion` file and keeps
the audited distro/board Kconfig unchanged. Test builds are marked prerelease.
The kernel, modules and devel RPM Release is 12; the UCSI fix is retained.
The workflow also uploads Kbuild's already-generated compressed EFI images
(`vmlinuz-*.efi`) with SHA-256 files for small-ESP evaluation. Normal RPM boot
installation still uses the uncompressed Image; the EFI alternative needs
hardware validation before selecting it.

A distinct namespace avoids path collisions with the original release-10
kernel. Installation must still preserve the original packages and boot files
and pass ESP capacity checks. Builds/uploads do not install anything. The
current user's installation/reboot hold remains in force.

DP audio remains an isolated configuration test with a separate DTB sound model
and additive topology/UCM files. Release-12's normal DTB does not yet include
those sound links. Before any test boot, generate the audio DTB from the **new
kernel's** matching DTB, preserving its EL2 reservations, and reuse the verified
audio firmware overlay. Do not reuse an old complete DTB over a new kernel's
board changes. The preparation tool and preserved, non-auto-installed UCM
payload are documented in [`tools/audio/dp-audio/README.md`](../tools/audio/dp-audio/README.md).

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
