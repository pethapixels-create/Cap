#!/usr/bin/env bash
# build-arms.sh <out-dir> <release-dir> <exe-suffix> [cargo target args...]
# Builds every "label=commit" in $ARMS in this one tree, so arms after the
# first only rebuild what differs. "HEAD" builds the checkout as it is.
set -euo pipefail

out="$1"
release="$2"
exe="$3"
shift 3
mkdir -p "$out"
: > "$out/arms.txt"

for arm in $ARMS; do
  label="${arm%%=*}"
  ref="${arm#*=}"
  if [[ "$ref" != "HEAD" ]]; then
    git cat-file -e "$ref^{commit}" 2>/dev/null || git fetch -q origin "$ref"
    git restore --source "$ref" --staged --worktree -- apps crates vendor Cargo.toml Cargo.lock
    echo "arm $label = $(git rev-parse --short "$ref"): $(git diff --cached --shortstat HEAD -- apps crates)"
  fi
  cargo +1.95.0 build --manifest-path apps/desktop-gpui/Cargo.toml --locked "$@" --release --bin cap-gpui 2>&1 |
    grep -v "^ *Compiling" | tail -8
  mkdir -p "$out/$label"
  cp "$release/cap-gpui$exe" "$out/$label/cap-gpui$exe"
  if [[ -n "$exe" ]]; then
    cp "$GITHUB_WORKSPACE"/target/release/*.dll "$out/$label/" 2>/dev/null || true
    find "$release" -maxdepth 1 -name '*.dll' -exec cp {} "$out/$label/" \;
  fi
  (sha256sum "$out/$label/cap-gpui$exe" 2>/dev/null || shasum -a 256 "$out/$label/cap-gpui$exe") | cut -c1-16
  echo "$label $ref" >> "$out/arms.txt"
done

if [[ "$(cut -d' ' -f1 "$out/arms.txt" | wc -l)" -gt 1 ]]; then
  first="$(head -1 "$out/arms.txt" | cut -d' ' -f1)"
  while read -r label _; do
    [[ "$label" == "$first" ]] && continue
    if cmp -s "$out/$first/cap-gpui$exe" "$out/$label/cap-gpui$exe"; then
      echo "::warning::arm $label is byte-identical to $first on this platform"
    fi
  done < "$out/arms.txt"
fi
git rev-parse HEAD > "$out/head.sha"
ls -la "$out"
