#!/usr/bin/env bash
# Runs one shard of UI tests against the build the simulator-build job uploaded.
# Usage: run_ui_shard.sh iPhone|iPad SHARD TOTAL
set -euo pipefail

device=$1
shard=$2
total=$3

xctestruns=(apple/DerivedData/Build/Products/*.xctestrun)
if [ "${#xctestruns[@]}" -ne 1 ] || [ ! -f "${xctestruns[0]}" ]; then
  echo "Expected one .xctestrun, found: ${xctestruns[*]}" >&2
  exit 1
fi

simulator_id=$(xcrun simctl list devices available -j | DEVICE="$device" python3 -c 'import json,os,sys; data=json.load(sys.stdin); print(next(d["udid"] for runtime,devices in data["devices"].items() if "iOS" in runtime for d in devices if os.environ["DEVICE"] in d["name"]))')

# Finish first-boot setup before XCTest starts its launch deadline.
xcrun simctl boot "$simulator_id"
xcrun simctl bootstatus "$simulator_id" -b

# A selector error must stop the job, not accidentally run the full suite.
selection_file=$(mktemp)
selection=()
if [ "$device" = iPad ]; then
  python3 apple/tools/ui_shards.py apple/UITests "$shard" "$total" --ipad > "$selection_file"
else
  # The photo-picker test must not depend on stock simulator photos.
  xcrun simctl addmedia "$simulator_id" apple/DerivedData/Build/Products/test-photo.png
  python3 apple/tools/ui_shards.py apple/UITests "$shard" "$total" > "$selection_file"
  if [ "$shard" = 1 ]; then selection+=("-only-testing:LunaschalAppTests"); fi
fi
while IFS= read -r test; do selection+=("$test"); done < "$selection_file"

xcodebuild test-without-building -xctestrun "${xctestruns[0]}" \
  -destination "platform=iOS Simulator,id=$simulator_id" \
  -parallel-testing-enabled NO -destination-timeout 120 \
  -retry-tests-on-failure -test-iterations 2 \
  -test-timeouts-enabled YES -default-test-execution-time-allowance 300 \
  -resultBundlePath apple/TestResults.xcresult \
  "${selection[@]}"
