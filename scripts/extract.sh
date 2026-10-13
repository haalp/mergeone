#!/usr/bin/env bash
# Clone kubernetes/kubernetes with enough first-parent history and extract the dataset.
set -euo pipefail
REPO="${1:-$HOME/kubernetes}"
N="${2:-15000}"
if [ ! -d "$REPO/.git" ]; then
  git clone --depth 1 --single-branch --branch master https://github.com/kubernetes/kubernetes "$REPO"
fi
# ~15k merges on master is roughly 1.5 years; deepen until we have enough.
git -C "$REPO" fetch --depth=200000 origin master || true
python -m scmbench.data.extract_cli --repo "$REPO" --max-merges "$N"
