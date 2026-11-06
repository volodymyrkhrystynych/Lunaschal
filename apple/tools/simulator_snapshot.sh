#!/usr/bin/env bash
# A fresh runner's first simulator boot takes 2-9 minutes. Do it once and share
# the device's data, so a test shard's boot is the ordinary, short one.
# Usage: simulator_snapshot.sh udid iPhone|iPad
#        simulator_snapshot.sh save iPhone|iPad FILE
#        simulator_snapshot.sh restore iPhone|iPad FILE
set -euo pipefail

command=$1
device=$2
devices="$HOME/Library/Developer/CoreSimulator/Devices"

udid=$(xcrun simctl list devices available -j | DEVICE="$device" python3 -c 'import json,os,sys; data=json.load(sys.stdin); print(next(d["udid"] for runtime,devices in data["devices"].items() if "iOS" in runtime for d in devices if os.environ["DEVICE"] in d["name"]))')

case "$command" in
  udid)
    echo "$udid"
    ;;
  save)
    xcrun simctl boot "$udid"
    xcrun simctl bootstatus "$udid" -b
    xcrun simctl shutdown "$udid"
    tar -C "$devices" -cf "$3" "$udid/data"
    ;;
  restore)
    # Images roll out gradually, and a device id from another image means
    # nothing here. Booting cold is slow but correct.
    # sed rather than head: head closing the pipe early fails under pipefail.
    first_entry=$(tar -tf "$3" | sed -n 1p)
    if [ "${first_entry%%/*}" != "$udid" ]; then
      echo "::warning::Simulator snapshot is for another image; booting cold"
      exit 0
    fi
    rm -rf "${devices:?}/$udid/data"
    tar -C "$devices" -xf "$3"
    ;;
  *)
    echo "Unknown command: $command" >&2
    exit 1
    ;;
esac
