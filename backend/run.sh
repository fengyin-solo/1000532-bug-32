#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# 优先使用项目内虚拟环境；建不起来（精简镜像缺 python3-venv）时回退系统解释器。
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY="$(command -v python3)"
  if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    "$PY" -m pip install -r requirements.txt
  fi
fi

exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
