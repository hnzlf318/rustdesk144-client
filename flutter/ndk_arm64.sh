#!/usr/bin/env bash
set -e
export RUST_BACKTRACE=full
cargo ndk --platform 21 --target aarch64-linux-android build --release --features flutter,hwcodec --verbose
