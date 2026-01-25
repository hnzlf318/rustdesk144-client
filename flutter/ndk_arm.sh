#!/usr/bin/env bash
set -e
export RUST_BACKTRACE=full
cargo ndk --platform 21 --target armv7-linux-androideabi build --release --features flutter,hwcodec --verbose
