#!/usr/bin/env bash
# Native Fedora aarch64 builder. Only a newly-created regular .img is formatted.
set -euo pipefail
: "${GAOKUN_DIR:?missing GAOKUN_DIR}"
: "${WORKDIR:?missing WORKDIR}"
: "${PACKAGE_RPMS_DIR:?missing PACKAGE_RPMS_DIR}"
: "${ARTIFACT_DIR:?missing ARTIFACT_DIR}"
[[ $(id -u) == 0 && $(uname -m) == aarch64 ]] || { echo 'Use a dedicated root aarch64 builder' >&2; exit 1; }
FEDORA_RELEASE="${FEDORA_RELEASE:-44}"
[[ $FEDORA_RELEASE == 44 ]] || exit 1
IMAGE_SIZE="${IMAGE_SIZE:-14G}"
[[ $IMAGE_SIZE =~ ^[1-9][0-9]*[GM]$ ]] || exit 1
mkdir -p "$WORKDIR" "$ARTIFACT_DIR"
WORKDIR=$(realpath "$WORKDIR")
ARTIFACT_DIR=$(realpath "$ARTIFACT_DIR")
ROOTFS_DIR="$WORKDIR/rootfs"
exec 8>"$WORKDIR/build.lock"
flock -n 8 || { echo 'Build workspace is already in use' >&2; exit 1; }
python3 "$GAOKUN_DIR/tools/installer/build-media.py" --repo "$GAOKUN_DIR" \
    --package-dir "$PACKAGE_RPMS_DIR" --work "$WORKDIR" --rootfs "$ROOTFS_DIR" --fedora "$FEDORA_RELEASE"
KREL=$(<"$WORKDIR/kernel-release.txt")
[[ $KREL =~ ^[0-9a-zA-Z._+-]+-gaokun3-el2\+?$ ]] || exit 1
IMAGE="$ARTIFACT_DIR/fedora-${FEDORA_RELEASE}-gaokun3-installer-aarch64.img"
[[ ! -e $IMAGE && ! -L $IMAGE ]] || { echo 'Refusing to overwrite an existing image' >&2; exit 1; }
MNT=$(mktemp -d "$WORKDIR/mount.XXXXXX")
LOOP=
cleanup() {
    set +e
    if mountpoint -q "$MNT"; then umount -R "$MNT"; fi
    if [[ -n $LOOP ]]; then losetup -d "$LOOP"; fi
    rmdir "$MNT" 2>/dev/null
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
truncate -s "$IMAGE_SIZE" "$IMAGE"
parted -s "$IMAGE" mklabel gpt
parted -s "$IMAGE" mkpart ESP fat32 1MiB 1025MiB
parted -s "$IMAGE" set 1 esp on
parted -s "$IMAGE" mkpart installer btrfs 1025MiB 100%
LOOP=$(losetup --find --show --partscan "$IMAGE")
[[ $LOOP =~ ^/dev/loop[0-9]+$ ]] || exit 1
[[ $(realpath "$(losetup --noheadings --output BACK-FILE "$LOOP")") == "$IMAGE" ]] || exit 1
for attempt in {1..50}; do
    [[ -b ${LOOP}p1 && -b ${LOOP}p2 ]] && break
    sleep .1
done
[[ -b ${LOOP}p1 && -b ${LOOP}p2 ]] || { echo 'Loop partition devices missing; bind /dev into the builder' >&2; exit 1; }
mkfs.vfat -F32 -n GAOKUN_EFI "${LOOP}p1"
mkfs.btrfs -f -L GAOKUN_INSTALL "${LOOP}p2"
EFI_UUID=$(blkid -s UUID -o value "${LOOP}p1")
ROOT_UUID=$(blkid -s UUID -o value "${LOOP}p2")
mount "${LOOP}p2" "$MNT"
btrfs subvolume create "$MNT/@"
umount "$MNT"
mount -o subvol=@,compress=zstd:1 "${LOOP}p2" "$MNT"
rsync -aHAX "$ROOTFS_DIR/" "$MNT/"
mkdir -p "$MNT/boot/efi" "$MNT/dev" "$MNT/proc" "$MNT/sys" "$MNT/run"
mount -o umask=0077 "${LOOP}p1" "$MNT/boot/efi"
printf 'UUID=%s / btrfs subvol=@,compress=zstd:1 0 0\nUUID=%s /boot/efi vfat defaults,umask=0077 0 2\n' \
    "$ROOT_UUID" "$EFI_UUID" > "$MNT/etc/fstab"
mount --rbind /dev "$MNT/dev"
mount --make-rslave "$MNT/dev"
mount -t proc proc "$MNT/proc"
mount -t sysfs -o ro sysfs "$MNT/sys"
mount -t tmpfs tmpfs "$MNT/run"
chroot "$MNT" /usr/bin/env KREL="$KREL" /bin/bash -euo pipefail <<'CHROOT'
useradd -m -s /bin/bash -G wheel installer
usermod -L installer
usermod -L root
mkdir -p /etc/sudoers.d /etc/gdm /home/installer/.config/autostart
printf 'installer ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/gaokun-installer-live
chmod 0440 /etc/sudoers.d/gaokun-installer-live
printf '[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=installer\n' > /etc/gdm/custom.conf
cp /usr/share/applications/gaokun-install.desktop /home/installer/.config/autostart/
printf 'yes\n' > /home/installer/.config/gnome-initial-setup-done
chown -R installer:installer /home/installer
# Installer identity/secrets are never copied by the DNF target payload.
rm -f /etc/machine-id /var/lib/dbus/machine-id /etc/ssh/ssh_host_*
systemd-machine-id-setup
mkdir -p /var/lib/dbus
ln -s /etc/machine-id /var/lib/dbus/machine-id
rm -f /etc/kernel/cmdline /etc/kernel/entry-token
systemctl enable gdm NetworkManager
systemctl disable sshd.service || true
systemctl set-default graphical.target
/usr/libexec/gaokun3/kernel-setup "$KREL" qcom/sc8280xp-huawei-gaokun3-el2.dtb
bootctl --no-variables --esp-path=/boot/efi install
# Installation media are cloned; never ship a shared bootloader/OS random seed.
rm -f /boot/efi/loader/random-seed /var/lib/systemd/random-seed
printf 'default %s-%s.conf\ntimeout 5\nconsole-mode keep\neditor no\n' "$(</etc/machine-id)" "$KREL" > /boot/efi/loader/loader.conf
CHROOT
# Label the offline root, never recurse into bind mounts from the build host.
setfiles -F -r "$MNT" -e "$MNT/dev" -e "$MNT/proc" -e "$MNT/sys" -e "$MNT/run" \
    -e "$MNT/boot/efi" "$MNT/etc/selinux/targeted/contexts/files/file_contexts" "$MNT"
# Require room for writable installer state, logs and normal operation.
FREE=$(df --output=avail -B1 "$MNT" | tail -1 | tr -d ' ')
[[ $FREE -ge 1073741824 ]] || { echo 'Installer filesystem needs at least 1 GiB free; increase IMAGE_SIZE' >&2; exit 1; }
cp "$WORKDIR/media-rpm-manifest.json" "$ARTIFACT_DIR/"
sync -f "$MNT"
sync -f "$MNT/boot/efi"
umount -R "$MNT"
losetup -d "$LOOP"
LOOP=
rmdir "$MNT"
trap - EXIT
xz -T2 -3 --keep "$IMAGE"
(cd "$ARTIFACT_DIR"; sha256sum "$(basename "$IMAGE").xz" > SHA256SUMS)
printf 'Burn the decompressed .img to a USB drive. Boot it and use Anaconda to select the installation target.\n' > "$ARTIFACT_DIR/README.txt"
