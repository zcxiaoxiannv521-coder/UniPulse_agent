#!/bin/zsh
set -eu
unipulse_web_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
unipulse_repo_dir="$(cd -- "$unipulse_web_dir/.." && pwd)"
if [[ -x "$unipulse_repo_dir/.venv/bin/python" ]]; then
  unipulse_python="$unipulse_repo_dir/.venv/bin/python"
elif [[ -n "${UNIPULSE_PYTHON:-}" && -x "$UNIPULSE_PYTHON" ]]; then
  unipulse_python="$UNIPULSE_PYTHON"
else
  echo "首次启动：创建仓库本地环境，需要 Python 3.11 或以上。"
  if ! python3 -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
    echo "请按 web/README.md 安装 Python 并创建 .venv 后重新启动。"
    read -r 'unipulse_pause?按回车关闭'
    exit 1
  fi
  python3 -m venv "$unipulse_repo_dir/.venv"
  unipulse_python="$unipulse_repo_dir/.venv/bin/python"
fi
if ! "$unipulse_python" -c 'import streamlit' 2>/dev/null; then
  "$unipulse_python" -m pip install -r "$unipulse_web_dir/requirements.txt"
fi
cd "$unipulse_web_dir"
if "$unipulse_python" - <<'PY'
from urllib.request import build_opener, ProxyHandler
try:
    with build_opener(ProxyHandler({})).open('http://127.0.0.1:8501/_stcore/health', timeout=2) as response:
        healthy = response.status == 200 and response.read().strip() == b'ok'
    raise SystemExit(0 if healthy else 1)
except Exception:
    raise SystemExit(1)
PY
then
  echo "8501 已有 Streamlit 服务，请直接打开 http://127.0.0.1:8501 。"
  echo "若不是 UniPulse，请检查之前的网页窗口；本脚本不会关闭其他服务。"
  read -r 'unipulse_pause?按回车关闭提示'
  exit 0
fi
echo "打开 http://127.0.0.1:8501 体验；在此窗口按 Control+C 停止。"
exec "$unipulse_python" -m streamlit run app.py --server.port 8501
