#!/usr/bin/env bash
# Usage: configure_kernel.sh SOURCE OUTPUT {fedora|ubuntu} [LOCALVERSION]
set -euo pipefail
: "${GAOKUN_DIR:?missing GAOKUN_DIR}"
src_dir=$(realpath "${1:?missing source}")
out_dir=$(realpath -m "${2:?missing output}")
distro=${3:?missing distro}
case "$distro" in fedora|ubuntu) ;; *) echo "Unsupported kernel distro: $distro" >&2; exit 1 ;; esac
mkdir -p "$out_dir"
export ARCH=arm64
export KCONFIG_CONFIG="$out_dir/.config"
make -C "$src_dir" O="$out_dir" CROSS_COMPILE="${CROSS_COMPILE:-}" gaokun3_defconfig
bash "$src_dir/scripts/kconfig/merge_config.sh" -m -O "$out_dir" "$out_dir/.config" \
    "$GAOKUN_DIR/defconfig/distro/common.config" \
    "$GAOKUN_DIR/defconfig/distro/$distro.config"
if [[ -n ${4:-} ]]; then
    "$src_dir/scripts/config" --file "$out_dir/.config" --set-str LOCALVERSION "$4"
fi
make -C "$src_dir" O="$out_dir" CROSS_COMPILE="${CROSS_COMPILE:-}" olddefconfig
python3 "$GAOKUN_DIR/tools/boot/check-kernel-config.py" "$out_dir/.config" \
    "$GAOKUN_DIR/defconfig/distro/common.config" \
    "$GAOKUN_DIR/defconfig/distro/$distro.config"
printf '%s\n' "$distro" > "$out_dir/gaokun-distro"
