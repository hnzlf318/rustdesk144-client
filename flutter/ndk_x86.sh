#!/usr/bin/env bash
set -e

#
# Fix OpenSSL build with Android NDK clang on 32-bit architectures
#

export CFLAGS="-DBROKEN_CLANG_ATOMICS"
export CXXFLAGS="-DBROKEN_CLANG_ATOMICS"
export RUST_BACKTRACE=full

cargo ndk --platform 21 --target i686-linux-android build --release --features flutter --verbose
