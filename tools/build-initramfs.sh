#!/usr/bin/env bash
#
# Build the bring-up initramfs: one static aarch64 binary that mounts the
# pseudo-filesystems and prints a hardware report to the framebuffer console.
#
# Deliberately not busybox: the diagnostic interface here is a photograph of the
# phone's screen, so the goal is a readable report and a heartbeat, not a shell.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=${ROOT:-$HERE/../work/initramfs}
CROSS=${CROSS:-aarch64-linux-gnu-}
OUT=${OUT:-$HERE/../work/out/initramfs.cpio.gz}

rm -rf "$ROOT"
mkdir -p "$ROOT"/{bin,proc,sys,dev,tmp}

echo "== compiling init"
"${CROSS}gcc" -static -O2 -Wall -Wextra -o "$ROOT/init" "$HERE/initramfs/init.c"

echo "== packing"
( cd "$ROOT" && find . | cpio -o -H newc --quiet | gzip -9 ) > "$OUT"

mkdir -p "$(dirname "$OUT")"
ls -l "$OUT"
echo "== done: $OUT"
