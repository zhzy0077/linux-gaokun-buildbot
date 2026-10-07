#!/usr/bin/env bash
# Shared helpers for the native arm64 kernel build/package jobs.

report_kernel_disk_usage() {
  printf '\n=== Disk usage: %s ===\n' "$1"
  df -h "$WORKDIR" || true
  df -i "$WORKDIR" || true
  du -h --max-depth=1 "$WORKDIR" || true
}

stage_kernel_devel() {
  local src_dir="$1"
  local out_dir="$2"
  local dest_dir="$3"
  # Filter before copying: a full distro build can fill the runner before a
  # copy-then-delete pass gets to remove its objects and temporary link images.
  local excludes=(
    --exclude='.git'
    --exclude='*.o'
    --exclude='*.ko'
    --exclude='*.a'
    --exclude='*.cmd'
    --exclude='*.mod'
    --exclude='*.mod.c'
    --exclude='/.tmp*'
    --exclude='/vmlinux.unstripped'
    --exclude='/arch/arm64/boot/Image*'
    --exclude='*.dtb'
    --exclude='*.dtbo'
  )

  mkdir -p "$dest_dir"
  rsync -a --delete --delete-excluded "${excludes[@]}" "$src_dir/" "$dest_dir/"
  rsync -a "${excludes[@]}" "$out_dir/" "$dest_dir/"
  # Replace the O= forwarding Makefile, which points at the CI source checkout.
  # Keep .config, generated headers, Module.symvers, Rust metadata, host tools
  # and the final vmlinux (including BTF) for external module builds.
  install -m644 "$src_dir/Makefile" "$dest_dir/Makefile"
  find "$dest_dir" -type l \( -name build -o -name source \) -delete
}
