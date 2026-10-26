#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/build.sh
python3 - <<'PY'
from pathlib import Path
import hashlib
import zipfile
from kakao_patch import VERSION

root = Path.cwd()
output = root / 'dist'
output.mkdir(exist_ok=True)
archive = output / ('kakao-ad-patcher-' + VERSION + '.zip')
files = [root / 'README.md', root / 'patcher.py', root / 'install.sh',
         root / 'build/KakaoAdBlock.dylib', root / 'build/KakaoAppControl', root / 'build/artifacts.json']
files += sorted((root / 'kakao_patch').glob('*.py'))
files += [root / 'src/AdBlock.m', root / 'src/AppControl.m', root / 'scripts/build.sh']
files += [root / 'docs/verification.md']
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    for path in files:
        bundle.write(path, str(path.relative_to(root)))
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / 'SHA256SUMS').write_text(digest + '  ' + archive.name + '\n')
print(str(archive))
print('SHA256 ' + digest)
PY
