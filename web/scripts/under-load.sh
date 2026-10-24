#!/usr/bin/env bash
# Run a command while CPU burners keep the machine busy.
#
#   bash scripts/under-load.sh <command...>
#   LOAD_BURNERS=60 LOAD_MAX_SECONDS=600 bash scripts/under-load.sh pnpm test
#
# K burners run (default 3 x the core count, at most 20 x the core count, at
# least 1). They stop when this script exits, is interrupted or is terminated.
# If this script is killed with SIGKILL (or the OOM killer), a watchdog notices
# within a second and stops the burners and the command. The same watchdog
# enforces a hard deadline, LOAD_MAX_SECONDS (default 1800): after it the
# burners and the command are stopped even if this script is still waiting.
#
# The command runs in its own process group and gets /dev/null as stdin. On a
# signal the whole group is stopped, so no worker outlives it. The exit status
# is the command's. This is a proof tool for load-proof tests. CI never calls it.
set -u
set -m

if [ "$#" -eq 0 ]; then
  echo "usage: under-load.sh <command...>" >&2
  exit 2
fi

cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
case "$cores" in
  '' | *[!0-9]* | 0) cores=1 ;;
esac
max_burners=$((cores * 20))
burners="${LOAD_BURNERS:-$((cores * 3))}"
max_seconds="${LOAD_MAX_SECONDS:-1800}"
case "$burners" in
  '' | *[!0-9]*)
    echo "LOAD_BURNERS must be a whole number, got: $burners" >&2
    exit 2
    ;;
esac
if [ "$burners" -lt 1 ] || [ "$burners" -gt "$max_burners" ]; then
  echo "LOAD_BURNERS must be from 1 to $max_burners (20 x $cores cores), got: $burners" >&2
  exit 2
fi
case "$max_seconds" in
  '' | *[!0-9]* | 0)
    echo "LOAD_MAX_SECONDS must be a whole number above 0, got: $max_seconds" >&2
    exit 2
    ;;
esac

parent_pid="$$"
pids=()
cmd_pid=""
watchdog_pid=""

# Stop the command's whole process group: TERM, a few seconds' grace, then KILL.
stop_command() {
  local i=0
  [ -n "$cmd_pid" ] || return 0
  kill -TERM -- "-$cmd_pid" 2>/dev/null || return 0
  while [ "$i" -lt 25 ] && kill -0 -- "-$cmd_pid" 2>/dev/null; do
    sleep 0.2
    i=$((i + 1))
  done
  kill -KILL -- "-$cmd_pid" 2>/dev/null
  return 0
}

stop_burners() {
  local pid
  trap - EXIT INT TERM
  if [ -n "$watchdog_pid" ]; then
    kill -- "-$watchdog_pid" 2>/dev/null
  fi
  stop_command
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
  yes > /dev/null < /dev/null &
  pids+=("$!")
  i=$((i + 1))
done

for pid in ${pids[@]+"${pids[@]}"}; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "a burner died on start (pid $pid)" >&2
    exit 1
  fi
done

echo "burners: $burners on $cores cores, hard stop after ${max_seconds}s"
echo "load average at start: $(uptime)"

# The command, in its own process group (set -m), so a signal reaches its
# children too. stdin is /dev/null.
"$@" < /dev/null &
cmd_pid=$!

# One watchdog for all the burners and the command. It lives in its own process
# group, so a SIGKILL sent to this script does not take it down. It stops
# everything when this script is gone or the deadline passes.
(
  started="$SECONDS"
  while kill -0 "$parent_pid" 2>/dev/null; do
    if [ $((SECONDS - started)) -ge "$max_seconds" ]; then
      break
    fi
    sleep 1
  done
  kill -TERM -- "-$cmd_pid" 2>/dev/null
  for pid in ${pids[@]+"${pids[@]}"}; do
    kill "$pid" 2>/dev/null
  done
  sleep 5
  kill -KILL -- "-$cmd_pid" 2>/dev/null
  for pid in ${pids[@]+"${pids[@]}"}; do
    kill -KILL "$pid" 2>/dev/null
  done
) > /dev/null 2>&1 < /dev/null &
watchdog_pid=$!

wait "$cmd_pid"
status=$?
cmd_pid=""
exit "$status"
