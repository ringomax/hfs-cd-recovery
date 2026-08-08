#!/bin/bash
# Resumable chunked ripper for optical drives that keep dropping off the USB bus.
#
# Read /dev/rdiskN, never /dev/diskN. The buffered block device aliases
# 2352-byte CD sectors against 4096-byte pages and silently returns duplicated
# sector runs. dd reports success while the data is wrong. The raw character
# device is byte-identical to what `hdiutil create -srcdevice` produces.
#
# Progress and known-bad chunks are recorded, so re-running after reconnecting
# the drive resumes where it stopped instead of burning the reconnect on a
# chunk that already failed.
#
# Usage:
#   ./chunkrip.sh <rdevice> <outfile> <total-sectors> [reverse]
#
# total-sectors is the raw 2352-byte sector count, from `drutil status`:
#   drutil status | awk '/blocks:/{print $3}'
#
# Pass `reverse` once the readable part is done: if the drive dies at a fixed
# spot, reading the tail first secures everything else before hitting it again.

set -u

DEV=${1:-/dev/rdisk62}
OUT=${2:-disc.raw}
TOTAL=${3:-0}
ORDER=${4:-forward}

SEC=2352                    # raw CD sector, Mode 1
STEP=512                    # sectors per chunk
CHUNK=$((SEC * STEP))       # 1,204,224 bytes per dd call
LOG="${OUT%.raw}.progress"
BAD="${OUT%.raw}.bad"

if [[ $TOTAL -le 0 ]]; then
  echo "usage: $0 <rdevice> <outfile> <total-sectors> [reverse]" >&2
  exit 2
fi

touch "$OUT" "$LOG" "$BAD"
CHUNKS=$(((TOTAL + STEP - 1) / STEP))

todo=()
for ((i = 0; i < CHUNKS; i++)); do
  grep -qx "$i" "$LOG" && continue
  grep -qx "$i" "$BAD" && continue
  todo+=("$i")
done

if [[ ${#todo[@]} -eq 0 ]]; then
  echo "nothing left: $(wc -l < "$LOG") read, $(wc -l < "$BAD") bad"
  exit 0
fi

[[ $ORDER == reverse ]] && todo=($(printf '%s\n' "${todo[@]}" | sort -rn))
echo "${#todo[@]} chunks to go ($(wc -l < "$LOG") read, $(wc -l < "$BAD") bad)"

for i in "${todo[@]}"; do
  s=$((i * STEP))
  n=$((TOTAL - s))
  ((n > STEP)) && n=$STEP

  if ((n == STEP)); then
    dd if="$DEV" bs=$CHUNK skip="$i" count=1 \
       of="$OUT" seek="$i" conv=notrunc 2>/dev/null
  else                                    # final partial chunk
    dd if="$DEV" bs=$SEC skip="$s" count="$n" \
       of="$OUT" oseek="$s" conv=notrunc 2>/dev/null
  fi

  if [[ $? -ne 0 ]]; then
    if ! drutil status 2>/dev/null | grep -q 'Name: /dev/disk'; then
      echo "drive vanished at chunk $i ($((s * SEC / 1048576)) MB). replug and re-run."
      exit 1
    fi
    echo "chunk $i unreadable, skipping ($((s * SEC / 1048576)) MB)"
    echo "$i" >> "$BAD"
    continue
  fi

  echo "$i" >> "$LOG"
  ((i % 50 == 0)) && echo "  $((s * SEC / 1048576)) / $((TOTAL * SEC / 1048576)) MB"
done

echo "done: $(wc -l < "$LOG") / $CHUNKS chunks, $(wc -l < "$BAD") bad"
