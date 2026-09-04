#!/usr/bin/env bash
# 冒烟测试：单元/安全测试 + CLI 自检（可选真实 HPC 连通性）
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== pytest =="
python3 -m pytest tests/ -q

echo "== CLI =="
python3 -m hpc_mcp --version

if [[ -n "${HPC_MCP_HOST:-}" && -n "${HPC_MCP_ROOT:-}" ]]; then
    echo "== remote check (HPC_MCP_HOST=$HPC_MCP_HOST) =="
    python3 -m hpc_mcp --check
else
    echo "== remote check skipped (set HPC_MCP_HOST and HPC_MCP_ROOT to enable) =="
fi

echo "Smoke test OK"
