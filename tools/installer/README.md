# Fedora 44 Gaokun network installer USB image

The installer is a writable GPT/FAT/Btrfs `.img`, compressed as `.img.xz` for
transport and published as a single GitHub release asset (under 2 GiB). Write it
to a USB drive and boot it on Gaokun3. The graphical Anaconda installer installs
a fresh Workstation system: Fedora packages come from the Fedora 44 and updates
mirrors, and the four Gaokun RPMs come from a local repository on the media. The
default kernel payload is EL2. UEFI Secure Boot must be disabled for the unsigned
Gaokun boot payloads.

The live root only carries what the installer session needs: the Gaokun kernel,
firmware and platform packages, GNOME Shell and NetworkManager for Wi-Fi setup,
storage/boot tools and Anaconda Web UI with Firefox (`LIVE_PACKAGES` in
`build-media.py`). It does not contain the Workstation target payload.

## Ownership

- Anaconda owns disk selection, partitioning, formatting, optional LUKS,
  `fstab`/`crypttab`, and initial systemd-boot installation (`bootloader --sdboot`).
- The four Gaokun RPMs supply the EL2 kernel/modules, firmware, and platform
  configuration. The platform package provides `kernel-setup`, static dracut
  rules and EL2 EFI assets. Kernel `%posttrans` calls that helper on installation
  and upgrades. It derives first-install parameters from the target fstab,
  generates one initramfs, and passes that image to `kernel-install`.
  Platform release 5 preloads QRTR before udev coldplug, preventing an ath11k
  asynchronous-probe/module-load deadlock that blocks USB-root discovery.
- `%post --nochroot` validates the target boot files and applies the target
  network/SSH defaults. It does not partition disks, install systemd-boot or
  regenerate another set of boot files.
- Fedora supplies unmodified Anaconda and sdubby. The media is designed for a
  normal single-Linux installation alongside Windows; it does not add a custom
  multi-Linux BLS merge policy.

There is no machine-specific `test-target.json`, fixed serial/partition UUID,
old boot token or required partition size. The running media is identified at
runtime and checked against the completed target. All formatting decisions are
made in Anaconda. Reuse an existing Windows ESP without formatting it.

New layouts should have sufficient ESP space (1 GiB is a useful default). For an
existing ESP, the RPM helper checks the bytes needed for new boot payloads and
retains headroom. It does not delete old operating-system files to make room.

## Build

Use the **Build Installer - Fedora Gaokun3 USB image** workflow. It first builds
a matching RPM release, verifies release asset digests, then composes on a native
aarch64 runner. The result is an Actions artifact and a GitHub release containing
`.img.xz`, checksums and the Gaokun RPM inventory. The release step fails if an
asset reaches GitHub's 2 GiB limit. The ordinary desktop-image workflow is
unchanged. For image-only retries, `package_release_tag` can reuse the exact
successful RPM release; its kernel tag must match the requested image version.

For a dedicated local Fedora 44 aarch64 build environment:

```bash
# Download the exact release produced from this branch (new boot integration required).
python3 tools/installer/download-rpms.py --repository OWNER/linux-gaokun-buildbot \
  --tag PACKAGE_RELEASE_TAG --output /build/gaokun-rpms

sudo env GAOKUN_DIR="$PWD" WORKDIR=/build/installer-work \
  PACKAGE_RPMS_DIR=/build/gaokun-rpms ARTIFACT_DIR=/build/artifacts \
  IMAGE_SIZE=6G bash scripts/ci/52_build_installer_image.sh
```

Install the build dependencies listed in the workflow first. Container builders
need loop/mount support and `/dev` access; use a dedicated builder. The script
formats only its newly-created regular image through the loop device it allocated,
uses an isolated mount directory and refuses to overwrite an existing image.
A 6 GiB image needs a USB drive with at least that actual byte capacity (8 GB media).

`build-media.py` checks that the Kickstart target selection, including weak
dependencies, resolves against the Fedora repositories plus the four local Gaokun
RPMs, then installs `LIVE_PACKAGES` into the USB root. Installer-only packages and
policies stay in the USB root; they are not target packages. The target is
resolved again against the mirrors at install time, so it receives current
Fedora updates.

The USB uses a password-locked `installer` account with automatic GNOME login.
Installation needs a network connection. When NetworkManager is not connected
within 10 seconds, the launcher opens GNOME Wi-Fi settings and waits up to 15
minutes before starting Anaconda. Anaconda Web UI opens automatically; the desktop icon uses the same launcher,
with background media validation and a single-instance lock. DNF Anaconda owns
the browser lifecycle; the launcher does not invoke Live-copy `liveinst`, which
would open a second viewer. The configured browser launcher uses Fedora's
packaged Live Firefox theme to hide browser chrome, in a fresh per-launch
runtime profile; it does not maintain a separate CSS theme. There are no custom
pre-install dialogs. Its local administrator policy is confined to the media.
SSH is disabled by default. The target account is created through GNOME Initial
Setup; configure its Wi-Fi and SSH there or after login. Live NetworkManager
profiles are not inherited and target SSH remains disabled.

## Validation

```bash
GAOKUN_KERNEL_SRC=/path/to/pristine-linux-7.2.9 \
GAOKUN_UPSTREAM_DTS=/path/to/pristine-linux-7.2.9/arch/arm64/boot/dts/qcom/sc8280xp-huawei-gaokun3.dts \
GAOKUN_UPSTREAM_CPUINFO=/path/to/pristine-linux-7.2.9/arch/arm64/kernel/cpuinfo.c \
python3 -B -m unittest discover -s tests -v
```

The builder validates the rendered Fedora 44 Kickstart and the target package solve.
Post-install validation checks the selected root/LUKS arguments, kernel/DTB hashes,
initramfs contents and the seven bootloader/EL2 EFI assets. It accepts an
unencrypted root when that is what the user chose.

The new composed installer image and native Anaconda SDBOOT path require a full
hardware installation run before release. Prior runtime tests validated the
hardware kernel/platform payloads, not this new installer compose.
