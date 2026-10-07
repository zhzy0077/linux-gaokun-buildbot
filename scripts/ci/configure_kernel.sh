#!/usr/bin/env bash
# Usage: configure_kernel.sh SOURCE OUTPUT {fedora|ubuntu} [LOCALVERSION]
set -euo pipefail
: "${GAOKUN_DIR:?missing GAOKUN_DIR}"
src_dir=$(realpath "${1:?missing source}")
out_dir=$(realpath -m "${2:?missing output}")
distro=${3:?missing distro}
case "$distro" in fedora|ubuntu) ;; *) echo "Unsupported kernel distro: $distro" >&2; exit 1 ;; esac
case ${4:-} in ''|-gaokun3) el2=false ;; -gaokun3-el2) el2=true ;; *) echo 'Unsupported kernel variant' >&2; exit 1 ;; esac
mkdir -p "$out_dir"
export ARCH=arm64
checker="$GAOKUN_DIR/tools/boot/check-kernel-config.py"
baseline=$(python3 "$checker" --repo "$GAOKUN_DIR" --distro "$distro" --baseline)
# Normalize the untouched vendor config separately to distinguish kernel-version,
# vendor-patch and compiler changes from our deliberate board overrides.
cp "$baseline" "$out_dir/vendor-normalized.config"
KCONFIG_CONFIG="$out_dir/vendor-normalized.config" \
    make -C "$src_dir" O="$out_dir" CROSS_COMPILE="${CROSS_COMPILE:-}" olddefconfig
export KCONFIG_CONFIG="$out_dir/.config"
cp "$baseline" "$out_dir/.config"
fragments=("$GAOKUN_DIR/defconfig/distro/common.config"
           "$GAOKUN_DIR/defconfig/distro/$distro.config"
           "$GAOKUN_DIR/defconfig/gaokun3-required.config")
args=()
if [[ $el2 == true ]]; then
    fragments+=("$GAOKUN_DIR/defconfig/gaokun3-el2.config")
    args+=(--el2)
fi
bash "$src_dir/scripts/kconfig/merge_config.sh" -m -O "$out_dir" "$out_dir/.config" "${fragments[@]}"
make -C "$src_dir" O="$out_dir" CROSS_COMPILE="${CROSS_COMPILE:-}" olddefconfig
python3 "$checker" --repo "$GAOKUN_DIR" --distro "$distro" --audit "$out_dir" "${args[@]}"
printf '%s\n' "$distro" > "$out_dir/gaokun-distro"
