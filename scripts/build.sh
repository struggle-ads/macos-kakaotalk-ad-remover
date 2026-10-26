#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
xcrun clang -dynamiclib -fobjc-arc -fblocks -Wall -Wextra -Werror \
  -Wno-unused-parameter -arch arm64 -arch x86_64 -mmacosx-version-min=12.0 \
  -framework Cocoa -install_name '@executable_path/../Frameworks/KakaoAdBlock.dylib' \
  src/AdBlock.m -o build/KakaoAdBlock.dylib
codesign --force --sign - build/KakaoAdBlock.dylib
xcrun clang -fobjc-arc -fblocks -Wall -Wextra -Werror -Wno-unused-parameter \
  -arch arm64 -arch x86_64 -mmacosx-version-min=12.0 -framework Cocoa \
  src/AppControl.m -o build/KakaoAppControl
codesign --force --sign - build/KakaoAppControl
"${KAKAO_PATCHER_PYTHON:-python3}" -c 'from pathlib import Path; from kakao_patch.install import record_artifacts; record_artifacts(Path.cwd())'
printf 'Built: build/KakaoAdBlock.dylib, build/KakaoAppControl\n'
