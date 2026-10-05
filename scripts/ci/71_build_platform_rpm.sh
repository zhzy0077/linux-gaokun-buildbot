#!/usr/bin/env bash
set -euo pipefail

GAOKUN_DIR="${GAOKUN_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
: "${WORKDIR:?missing WORKDIR}"
: "${ARTIFACT_DIR:?missing ARTIFACT_DIR}"
PLATFORM_RPM_VERSION="${PLATFORM_RPM_VERSION:-$(date -u +%Y%m%d)}"
[[ "$PLATFORM_RPM_VERSION" =~ ^[0-9][0-9.]*$ ]] || {
  echo 'PLATFORM_RPM_VERSION must contain only digits and dots' >&2
  exit 1
}

mkdir -p "$WORKDIR" "$ARTIFACT_DIR"
WORKDIR="$(realpath "$WORKDIR")"
ARTIFACT_DIR="$(realpath "$ARTIFACT_DIR")"
# Keep each build isolated, including when the work directory is reused.
RPM_TOPDIR="$(mktemp -d "$WORKDIR/platform-rpmbuild.XXXXXX")"
mkdir -p "$RPM_TOPDIR"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

source_name=gaokun3-platform.tar.gz
tar -C "$GAOKUN_DIR" --exclude=__pycache__ -czf "$RPM_TOPDIR/SOURCES/$source_name" \
  tools/audio tools/bluetooth tools/monitors tools/touchscreen-tuner tools/image-assets
sed -e "s|@PLATFORM_VERSION@|$PLATFORM_RPM_VERSION|g" \
    -e "s|@SOURCE_NAME@|$source_name|g" \
    "$GAOKUN_DIR/packaging/rpm/gaokun3-platform.spec.in" \
    > "$RPM_TOPDIR/SPECS/gaokun3-platform.spec"

rpmbuild --define "_topdir $RPM_TOPDIR" -bb "$RPM_TOPDIR/SPECS/gaokun3-platform.spec"
rpm_path="$(find "$RPM_TOPDIR/RPMS/noarch" -name 'gaokun3-platform-*.rpm' -print -quit)"
[[ -n "$rpm_path" ]] || { echo 'Platform RPM was not produced' >&2; exit 1; }
cp "$rpm_path" "$ARTIFACT_DIR/"
basename "$rpm_path" > "$WORKDIR/platform-rpm-name.txt"
