#!/usr/bin/env bash
# 安装 hpc-mcp（pipx 优先，回退 pip --user）
set -euo pipefail

cd "$(dirname "$0")/.."

if command -v pipx >/dev/null 2>&1; then
    pipx install .
elif command -v uv >/dev/null 2>&1; then
    uv tool install .
else
    python3 -m pip install --user .
fi

echo "Installed. Verify with: hpc-mcp --version"
