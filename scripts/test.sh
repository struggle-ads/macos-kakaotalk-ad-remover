#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
bash scripts/build.sh
xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -framework Cocoa \
  tests/hooks.m -o build/hooks-test
build/hooks-test "$PWD/build/KakaoAdBlock.dylib"
xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -Wno-unused-parameter -framework Cocoa \
  tests/app_control_fixture.m -o build/app-control-fixture
python3 tests/app_control_smoke.py
