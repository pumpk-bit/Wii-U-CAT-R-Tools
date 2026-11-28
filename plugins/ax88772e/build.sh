#!/bin/bash
# Builds 5usbe72e.ipx. Needs devkitARM and STROOPWAFEL_ROOT (a stroopwafel checkout); see README.md.
set -e
: "${DEVKITPRO:=/opt/devkitpro}"
: "${DEVKITARM:=$DEVKITPRO/devkitARM}"
export DEVKITPRO DEVKITARM
if [ -z "$STROOPWAFEL_ROOT" ]; then
    echo "Set STROOPWAFEL_ROOT to a checkout of https://github.com/StroopwafelCFW/stroopwafel" >&2
    exit 1
fi
export PATH="$DEVKITARM/bin:$PATH"
cd "$(dirname "$0")"
make
sha256sum 5usbe72e.ipx
