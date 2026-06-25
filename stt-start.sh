#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
exec ./stt/run_listener.sh
