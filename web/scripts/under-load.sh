#!/usr/bin/env bash
# Run a command while CPU burners keep the machine busy.
#
#   bash scripts/under-load.sh <command...>
#   LOAD_BURNERS=60 bash scripts/under-load.sh pnpm test
#
# K burners run (default 3 x the core count). They are killed when this script
# exits, is interrupted or is terminated. The exit status is the command's.
# This is a proof tool for load-proof tests. CI never calls it.
set -u

if [ "$#" -eq 0 ]; then
  echo "usage: under-load.sh <command...>" >&2
  exit 2
fi

cores="$(getconf _NPROCESSORS_ONLN)"
burners="${LOAD_BURNERS:-$((cores * 3))}"
case "$burners" in
  '' | *[!0-9]*)
    echo "LOAD_BURNERS must be a whole number, got: $burners" >&2
    exit 2
    ;;
esac

pids=()
cmd_pid=""

stop_burners() {
  local pid
  trap - EXIT INT TERM
  if [ -n "$cmd_pid" ]; then
    kill "$cmd_pid" 2>/dev/null
  fi
  for pid in ${pids[@]+"${pids[@]}"}; do
    kill "$pid" 2>/dev/null
  done
  for pid in ${pids[@]+"${pids[@]}"}; do
    wait "$pid" 2>/dev/null
  done
}

trap stop_burners EXIT
trap 'stop_burners; exit 130' INT
trap 'stop_burners; exit 143' TERM

i=0
while [ "$i" -lt "$burners" ]; do
  yes > /dev/null &
  pids+=("$!")
  i=$((i + 1))
done

for pid in ${pids[@]+"${pids[@]}"}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "a burner died on start (pid $pid)" >&2
    exit 1
  fi
done

echo "burners: $burners on $cores cores"
echo "load average at start: $(uptime)"

# The command runs in the background and the script waits on it, so a signal
# sent to the script is handled at once and not after the command finishes.
"$@" &
cmd_pid=$!
wait "$cmd_pid"
status=$?
cmd_pid=""
exit "$status"
