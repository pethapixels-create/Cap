#!/usr/bin/env bash
# engine-arms.sh <out-dir> <exe-suffix>
# Builds the engine playback benchmark for every "label=commit" in $ARMS in
# this one tree. "HEAD" builds the checkout as it is.
set -euo pipefail

out="$1"
exe="$2"
mkdir -p "$out"
: > "$out/arms.txt"

for arm in $ARMS; do
  label="${arm%%=*}"
  ref="${arm#*=}"
  if [[ "$ref" != "HEAD" ]]; then
    git cat-file -e "$ref^{commit}" 2>/dev/null || git fetch -q origin "$ref"
    git restore --source "$ref" --staged --worktree -- apps crates Cargo.toml Cargo.lock
    echo "arm $label = $(git rev-parse --short "$ref"): $(git diff --cached --shortstat HEAD -- apps crates)"
  fi
  cargo build --release -p cap-editor --example editor-playback-benchmark 2>&1 |
    grep -v "^ *Compiling" | tail -8
  mkdir -p "$out/$label"
  cp "target/release/examples/editor-playback-benchmark$exe" "$out/$label/"
  echo "$label $ref" >> "$out/arms.txt"
done
ls -la "$out"
