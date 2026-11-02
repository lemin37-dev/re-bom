#!/usr/bin/env bash
# Re:봄 검색 API 실행 (macOS·Linux·Git Bash). 레포 어디서 실행해도 루트 기준으로 동작한다.
#   scripts/serve/run_api.sh                    # sample 모드, http://localhost:8000/docs
#   PORT=8080 REBOM_DATA_MODE=sample scripts/serve/run_api.sh
#   RELOAD=0 scripts/serve/run_api.sh           # 자동 재시작 끄기
set -euo pipefail
cd "$(dirname "$0")/../.."

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
RELOAD="${RELOAD:-1}"

if [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe   # Git Bash
else
  echo "가상환경(.venv) 만드는 중..."
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv/Scripts/python.exe; fi
fi

if ! "$PY" -c "import fastapi, uvicorn, pydantic_settings, rebom" 2>/dev/null; then
  echo "패키지 설치 중..."
  "$PY" -m pip install -q -e ".[serve,dev]"
fi

args=(-m uvicorn rebom.serving.app:create_app --factory --host "$HOST" --port "$PORT")
[ "$RELOAD" = "1" ] && args+=(--reload --reload-dir src)
echo "검색 API: http://${HOST}:${PORT}/docs"
exec "$PY" "${args[@]}"
