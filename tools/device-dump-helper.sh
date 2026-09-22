#!/system/bin/sh
# Dump gauguin partitions to stdout, one per invocation.
# Usage: dump.sh <raw-block-device-or-path>
dd if="$1" bs=1048576 2>/dev/null
