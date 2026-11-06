#!/usr/bin/env bash
# A fresh runner's first simulator boot takes 2-9 minutes. Do it once and share
# the device's data, so a test shard's boot is the ordinary, about-a-minute one.
# Usage: simulator_snapshot.sh udid iPhone|iPad
#        simulator_snapshot.sh save iPhone|iPad FILE
#        simulator_snapshot.sh restore iPhone|iPad FILE
set -euo pipefail

command=$1
device=$2
devices="$HOME/Library/Developer/CoreSimulator/Devices"

# Hosted images roll out gradually, so one run can span two, and each names its
# devices differently. The data depends on the runtime that booted it, not on
# the id, so the runtime's build is what has to match.
read -r udid runtime < <(xcrun simctl list -j devices available runtimes | DEVICE="$device" python3 -c '
import json, os, sys
data = json.load(sys.stdin)
key, found = next((key, d) for key, devices in data["devices"].items() if "iOS" in key
                  for d in devices if os.environ["DEVICE"] in d["name"])
build = next(r["buildversion"] for r in data["runtimes"] if r["identifier"] == key)
print(found["udid"], f"{key}-{build}")')

case "$command" in
  udid)
    echo "$udid"
    ;;
  save)
    xcrun simctl boot "$udid"
    xcrun simctl bootstatus "$udid" -b
    xcrun simctl shutdown "$udid"
    echo "$runtime" > "$devices/$udid/runtime"
    tar -C "$devices/$udid" -cf "$3" runtime data
    ;;
  restore)
    saved=$(tar -xOf "$3" runtime)
    if [ "$saved" != "$runtime" ]; then
      echo "::warning::Simulator snapshot is for $saved, not $runtime; booting cold"
      exit 0
    fi
    rm -rf "${devices:?}/$udid/data"
    tar -C "$devices/$udid" -xf "$3" data
    ;;
  *)
    echo "Unknown command: $command" >&2
    exit 1
    ;;
esac
