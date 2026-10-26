#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin=""
for candidate in "$(command -v python3 || true)" /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    python_bin="$candidate"
    break
  fi
done
if [[ -z "$python_bin" ]]; then
  printf 'Python 3.9 이상이 필요합니다. Python 또는 Apple Command Line Tools를 설치한 뒤 다시 실행하세요.\n' >&2
  exit 1
fi
exec "$python_bin" "$project_dir/patcher.py" install "$@"
