set -euo pipefail

ROOT_DIR="."
BUILD_DIR="$ROOT_DIR/build"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

cmake -S "$ROOT_DIR" -B "$BUILD_DIR" \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++

cmake --build "$BUILD_DIR"