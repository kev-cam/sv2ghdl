#!/bin/bash
# Containerized nvcc: run any nvcc command line inside the CUDA-devel image
# with the current directory mounted at /w.  Needs NO GPU, NO nvidia driver,
# and NO CUDA install on the build host — compilation is pure CPU.
#
#   gpu-cc.sh -O2 -arch=sm_75 -o farm farm.cu
#
# Hardening defaults:
#   - image pinned by DIGEST (override: IMG=..., or IMG_TAG=1 to use the tag)
#   - --network=none: nvcc needs no network; the build is hermetic and the
#     mounted source cannot leave the container
# Podman is the supported engine (rootless is fine).  ENGINE=docker works if
# you must, with user-mapping caveats podman does not have.
set -eu
PIN=docker.io/nvidia/cuda@sha256:da6791294b0b04d7e65d87b7451d6f2390b4d36225ab0701ee7dfec5769829f5
TAG=docker.io/nvidia/cuda:12.4.1-devel-ubuntu22.04
IMG=${IMG:-$([ -n "${IMG_TAG:-}" ] && echo "$TAG" || echo "$PIN")}
ENGINE=${ENGINE:-podman}
USERNS=--userns=keep-id
[ "$ENGINE" = docker ] && USERNS="--user $(id -u):$(id -g)"
exec "$ENGINE" run --rm --network=none -v "$PWD":/w -w /w $USERNS \
    -e NVCC_APPEND_FLAGS= "$IMG" nvcc "$@"
