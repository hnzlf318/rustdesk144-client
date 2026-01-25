#!/usr/bin/env bash
set -e
export RUST_BACKTRACE=full
cargo ndk --platform 21 --target x86_64-linux-android build --release --features flutter --verbose
