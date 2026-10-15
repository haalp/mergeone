#!/usr/bin/env sh
# curl -fsSL https://mergeone.dev/install.sh | sh
set -e
REPO="haalp/mergeone"
OS=$(uname -s | tr '[:upper:]' '[:lower:]'); ARCH=$(uname -m)
case "$ARCH" in x86_64|amd64) ARCH=amd64;; arm64|aarch64) ARCH=arm64;; *) echo "unsupported arch $ARCH"; exit 1;; esac
TAG=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p')
[ -n "$TAG" ] || { echo "no release found"; exit 1; }
URL="https://github.com/$REPO/releases/download/$TAG/mergeone_${OS}_${ARCH}.tar.gz"
TMP=$(mktemp -d); curl -fsSL "$URL" | tar xz -C "$TMP"
BIN="${MERGEONE_BIN:-/usr/local/bin}"; mkdir -p "$BIN" 2>/dev/null || true
install -m 755 "$TMP/mergeone_${OS}_${ARCH}" "$BIN/mergeone" 2>/dev/null || { BIN="$HOME/.local/bin"; mkdir -p "$BIN"; install -m 755 "$TMP/mergeone_${OS}_${ARCH}" "$BIN/mergeone"; }
mkdir -p "$HOME/.mergeone"; cp -n "$TMP/assets/"* "$HOME/.mergeone/" 2>/dev/null || true
echo "installed mergeone $TAG to $BIN/mergeone (assets in ~/.mergeone)"
