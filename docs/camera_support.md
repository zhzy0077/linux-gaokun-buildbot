# Gaokun camera support

The board DTS now selects the HI846 front sensor at `2-0020` and the OV13B10
rear-camera variant at `1-0036`. Both use Qualcomm CAMSS and libcamera's simple
pipeline/software ISP.

## Investigation and hardware status

On the investigated device, kernel `7.2.9-gaokun3-el2+` identified the HI846
(`chip id 08 46`) but waited indefinitely for the old rear `camera@10` endpoint.
`/sys/kernel/debug/v4l2-async/pending_async_subdevices` listed `1-0010`; that
S5K3L6XX node had no driver. CAMSS creates the sensor links and subdevice nodes
only after every configured sensor binds, so the camera app could enumerate
neither camera.

The device's Windows rear-camera extension package contains both
`com.qti.sensormodule.ofilm_ov13b10.bin` and
`com.qti.sensormodule.lijing_s5k3l6.bin`. Its active resource table is
`CAMS_RES_QRD.bin`. Decoding that table confirmed the following wiring:

| Function | Resource |
| --- | --- |
| Analog supply | LDO2_B, 2.8 V |
| I/O supply | LDO2_C, 1.8 V |
| Core supply enable | GPIO92 |
| Reset | GPIO7, active low |
| Focus motor supply | LDO7_B, 2.8 V |
| Sensor clock | MCLK4 |

[Vahiru's hardware investigation, section 106](https://github.com/vahiru/gaokun-android/blob/main/docs/stage4-findings.md#106)
identified an OV13B10 at address `0x36` on another Gaokun unit and demonstrated
rear frame capture and application enumeration. The Windows package supports
multiple module variants; package filenames and its default
`SCFG_REAR_QRD.bin` entry alone do not identify the installed module.

### On-device validation (2026-10-07)

A separate test boot reused the running kernel with the patched DTB and an
external OV13B10 module built for its exact config/release. Results:

- Rear sensor identified as OV13B10, chip ID `0x560d42`.
- HI846, OV13B10 and DW9714 all bound; no pending async dependencies.
- CAMSS registered 47 subdevice nodes.
- libcamera and PipeWire enumerated both front and rear cameras.
- Each camera completed five 640x480 ABGR8888 frames through `cam`, exit 0;
  frames were discarded rather than saved.

A subsequent Camera-app opening failed with `titan_top_gdsc status stuck at
'off'` and `Failed to power up pipeline: -110`. The idle clock summary showed
CAMNOC AXI still at 150 MHz under PLL0 while PLL0 was disabled. This matches
[Vahiru's restart investigation, section 105](https://github.com/vahiru/gaokun-android/blob/main/docs/stage4-findings.md#105):
CAMNOC/fast-AHB/slow-AHB need shared RCG operations to park on XO when disabled.
The three-operation fix is carried as `others/0010`. A second test boot loaded
that camera-clock module from an initramfs addon; its loaded build ID matched
the compiled artifact. The Camera app started streams three times without a
power-domain timeout, and the user confirmed the app was working.

## Changes carried here

- `dts/sc8280xp-huawei-gaokun3-camera.dtsi`: OV13B10 at `0x36`, correct supply
  assignments, GPIO92 core regulator, reciprocal CSIPHY0 connection and privacy
  LED. MCLK is 19.2 MHz with a 560 MHz link frequency, matching the upstream
  OV13B10 register tables. Windows uses 24 MHz with its own register tables.
- `patches/others/0009-media-i2c-ov13b10-support-Gaokun-camera-power.patch`:
  device-tree matching and the board power sequence. Reset is asserted before
  enabling the supplies; it is released after 1 ms, followed by a 10 ms delay
  before enabling MCLK. The driver requests 2.8 V on the shared L2B rail while
  powered and restores the 1.8–2.8 V idle range on power-off or enable failure.
  Board-specific behavior is gated by `huawei,gaokun3`; existing ACPI devices
  retain their power sequence.
- `patches/others/0010-clk-qcom-camcc-sc8280xp-park-camera-clocks.patch`:
  shared RCG operations for CAMNOC AXI, fast AHB and slow AHB, so camera
  power-domain handshakes have an active oscillator after clocks are disabled.
- Both kernel config paths enable `CONFIG_VIDEO_OV13B10=m`, alongside HI846
  and DW9714. The import patch mirrors the board DTS and defconfig.

The adaptation is based on Vahiru's
[0032 DTS patch](https://github.com/vahiru/gaokun-android/blob/main/patches/0032-arm64-dts-gaokun3-camera-rear-ov13b10-with-board-rails.patch)
and
[0034 driver patch](https://github.com/vahiru/gaokun-android/blob/main/patches/0034-media-i2c-ov13b10-of-match-and-gaokun3-power-sequence.patch).

## Build and verify

Build a fresh kernel tree with the complete project patch series and the
updated config, then install matching kernel modules and DTB. Standard and EL2
builds both consume these changes. Use a distinct kernel release/test boot entry
so the existing working boot remains available.

After booting the test kernel:

```sh
uname -r
sudo modprobe ov13b10
sudo modprobe dw9714
journalctl -k -b --no-pager | grep -E 'hi846|ov13b10|camss'
readlink /sys/bus/i2c/devices/1-0036/driver
readlink /sys/bus/i2c/devices/1-000c/driver
readlink /sys/bus/i2c/devices/2-0020/driver
sudo cat /sys/kernel/debug/v4l2-async/pending_async_subdevices
ls /dev/v4l-subdev*
cam --list  # supplied by libcamera-tools
```

Required outcomes:

1. OV13B10 logs `chip id 0x560d42 identified`; HI846 also binds.
2. DW9714 binds at `1-000c`, satisfying the rear sensor's `lens-focus` dependency.
3. The async notifier has no pending sensor/lens entries and subdevice nodes exist.
4. libcamera lists both cameras. Test each in the camera app, including switching
   front → rear → front. Check the image, display and privacy indicator while
   each streams.

If the rear ID read fails, identify the installed module using the verified
power sequence and targeted chip-ID reads before changing the sensor model.
To build a front-only recovery DTB, remove CAMSS `port@0` and the rear sensor's
`port` connection, then disable the rear node. Disconnect both endpoint
references: CAMSS iterates graph endpoints regardless of sensor availability.

Image-quality work is separate from sensor discovery. Sensor helpers, tuning
and autofocus support should be assessed after both cameras bind and capture
frames; Vahiru carries additional libcamera helper/tuning changes.

## Automated checks

```sh
python3 -m unittest discover -s tests -p 'test_camera_support.py' -v
GAOKUN_KERNEL_SRC=/path/to/pristine/linux-7.2.9 \
  python3 -m unittest discover -s tests -p 'test_kernel_patches.py' -v
```

Camera tests cover wiring, graph connections, config/import synchronization,
OF matching, board scoping and power-enable failure cleanup. A C compiler is
required for the power-sequence harness. Compile the patched OV13B10 object and
both standard/EL2 board DTBs against Linux 7.2.9 before deployment.
