# HPC-MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MCP stdio server](https://img.shields.io/badge/MCP-stdio%20server-6f42c1.svg)](https://modelcontextprotocol.io)

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | **한국어** | [繁體中文](README.zh-TW.md)

**보안 우선** MCP 서버. Codex, Reasonix 같은 코딩 agent가 **SSH + Slurm**을 통해 원격 HPC 클러스터를 안전하게 다룰 수 있게 해 줍니다. 경로 샌드박스, 로그인 노드 명령 허용 목록, Slurm 리소스 상한, 작업 소유권, 그리고 모든 호출에 대한 ALLOW/DENY 감사까지 — 모두 코드로 강제됩니다.

> **처음이신가요? → [docs/ko/QUICKSTART.md](docs/ko/QUICKSTART.md)**: 단계별 설치, 각 파라미터 설명, Codex/Reasonix 설정 예시, 그리고 "시작해도 멈춰 있는다", "네트워크가 안 된다", "pip로 UNKNOWN으로 설치된다" 같은 문제의 진단 표가 있습니다.

## 아키텍처

```text
Codex / Reasonix (Agent)
        │  MCP (stdio)
        ▼
  Project Skill            ← 행동 지침(보안 경계 아님)
        │
        ▼
  HPC MCP Server           ← 보안 경계(코드로 강제)
        │
        ├── Path Sandbox        (USER_ROOT 강제)
        ├── Command Policy      (로그인 노드 허용 목록)
        ├── Slurm Resource Policy(파티션/리소스/동시 실행)
        ├── SSH Manager         (고정 argv, 로컬 셸 없음)
        ├── Job Tracker         (공용 계정에서의 작업 소유권)
        └── Audit Logger        (호출마다 ALLOW/DENY 기록)
        │ SSH / SFTP
        ▼
  HPC Login Node  ──경량 조회만 허용──┐
        │ sbatch                        │
        ▼                               ▼
  Compute Node (Slurm)        사용자 작업 디렉터리 $HPC_MCP_ROOT
        │
   Julia / MOOSE / Python / CMake
```

설계 원칙: **MCP가 보안 경계이고, Skill은 행동 지침일 뿐입니다.** agent의 프롬프트가 잘못되었거나 Skill이 오해되어도 핵심 권한 경계는 우회할 수 없습니다.

## 보안 모델

### 공용 계정 격리

HPC는 흔히 공용 계정을 씁니다(예: `/home/shared_account/`). 그 홈 디렉터리는 **당신 자신의 디렉터리가 아닙니다**. 반드시 설정하세요:

```
HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD       # 업로드/다운로드를 허용할 로컬 디렉터리
```

모든 원격 파일 작업은 이 root 아래로 제한됩니다(심볼릭 링크 canonical 검사 포함). 로컬 `upload`/`download` 역시 `local_roots`(하나 이상의 허용된 로컬 디렉터리. 기본은 시작 프로세스의 작업 디렉터리 + 시스템 임시 디렉터리 `/tmp`)로 제한되며, `.ssh`, 개인 키, 심볼릭 링크 경로를 거부합니다. 다른 사용자의 디렉터리(`/home/shared_account/other_user`)와 시스템 디렉터리(`/etc`, `/opt`)는一律 거부됩니다.

### 로그인 노드 정책

로그인 노드에서는 경량이고 읽기 전용인 관리 명령(허용 목록)만 허용됩니다: `ls`, `find`, `cat`, `grep`, `head`, `tail`, `git status/diff/log`, `module list/avail` 등. 공용 계정에서 다른 사용자의 작업을 보지 못하도록 `squeue`, `sacct`, `scontrol`은 `hpc.shell.run_safe`로 노출되지 않으며, 소유권 검사를 수행하는 Slurm 도구로만 접근할 수 있습니다.

로그인 노드에서 계산·빌드 실행은 **항상 거부**됩니다: `julia`, `python`, `make`, `cmake --build`, `ninja`, `mpirun`, `srun`, `pytest`, `matlab`, GPU 프로그램 등. 모두 `hpc.slurm.submit`으로 유도됩니다.

명령 정책은 **코드 수준**에서 거부합니다: 셸 메타 문자(`;`, `&&`, `||`, `|`, `>`, `<`, `$()`, 백틱, `&`), 경로 형식의 임의 실행 파일(`./program`), `find -exec`, `git -c`, 중첩 셸, `sudo`/`ssh`/`curl` 같은 위험한 프로그램. 파싱에 실패해도 거부합니다(fail-closed).

`env`/`printenv`는 요청되더라도 비운 최소 환경에서만 실행됩니다. SSH 토큰, 키, 클러스터 자격 증명은 반환되지 않습니다. 모든 명령의 경로 피연산자는 원격에서 `realpath`로 다시 검증되며, 심볼릭 링크를 따라가는 옵션은 거부됩니다.

### Slurm 리소스 정책

`hpc.slurm.submit`이 유일한 계산 진입점입니다. 서버에서 다음을 강제합니다:

- 파티션 허용 목록(기본은 비어 있음 = 모두 거부. agent는 목록 안의 파티션만 고를 수 있음)
- `max_nodes` / `max_cpus` / `max_memory_mb` / `max_gpus` / `max_time`
- `max_concurrent_jobs`(동시 실행 상한)
- 작업 디렉터리는 USER_ROOT 안이어야 함
- 작업 stdout/stderr는 항상 `$ROOT/.hpc-mcp/jobs/<job-id>/`로 수집됨

### 작업 소유권(공용 계정)

공용 계정에서는 Unix가 사용자를 구분하지 못합니다. 각 MCP 인스턴스는 **자기가 제출하고 등록한** 작업(`$ROOT/.hpc-mcp/tracked_jobs.json`)만 관리합니다. 다른 작업에 대한 `status/output/cancel/accounting`은一律 거부됩니다.

### 실패 시 거부(fail-closed)

설정 누락, 경로 해석 불가, 명령 파싱 실패, 파티션 불확실, SSH 이상 — 모든 불확실한 상황은 **DENY**이며, 무제한 셸로 되돌아가는 일은 없습니다.

### 3계층 보안 경계

| 계층 | 기구 | 방어 대상 |
|---|---|---|
| Layer 1: MCP policy | 경로 샌드박스, 명령 허용 목록, Slurm 리소스 정책, 작업 소유권, 감사 | agent의 일탈/오동작 |
| Layer 2: Slurm | 파티션 허용 목록, 리소스 상한, 동시 실행 상한, `sbatch` 진입점 단일화 | 계산 자원 남용 |
| Layer 3: OS/클러스터 | 독립 Unix UID / 작업 격리 / filesystem ACL / 컨테이너 | **악성 코드 격리**(선택) |

> **잔여 위험(반드시 이해하세요)**: 공용 Unix UID 아래에서 MCP는 "agent가 일탈하지 않음"(Layer 1/2)만 보장할 수 있습니다. 계산 노드에 제출된 악성 코드가 같은 UID가 읽을 수 있는 다른 데이터에 접근하지 않는다고 **보장할 수 없습니다**(Layer 3). 진정한 적대적 코드 격리에는 독립 UID, Slurm 작업 격리 + filesystem ACL, 또는 컨테이너/샌드박스가 필요합니다. Python 측 정규식이나 정책을 악성 코드에 대한 OS 수준 격리로 여기지 마세요.

### 쿼리 비용과 중복 제거

- `hpc.files.read`는 **bounded slice**입니다: 호출당 최대 `min(max_bytes, files.max_read_slice_bytes)`(기본 256KiB) 바이트이며, agent를 offset=0에서 EOF까지 읽게 하지 않습니다.
- `hpc.files.list`의 재귀 나열은 **한 계층씩 페이징**하고(`page_size` + `next_cursor`), 원격 출력을 `head`로 자르며, 트리 전체를 스캔한 뒤 버리는 일이 없습니다.
- `hpc.files.search`에는 하드 예산(`max_matches`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`, 모두 서버에서 클램프)이 있어 "먼저 위치를 찾고 다음에 정독"하는 데 씁니다.
- 멱등적 읽기 전용 쿼리는 `tool+인자`로 중복 제거됩니다(TTL 기본 2초, `cache_ttl_seconds`로 조정, 0이면 비활성). 쓰기 작업은 캐시를 적극적으로 무효화합니다.
- `hpc.slurm.queue/status`는 이 인스턴스가 추적하는 job ID만 조회하며(`squeue -j <ids>`), 공용 계정의 큐 전체를 **결코** 스캔하지 않습니다.
- `hpc.shell.run_safe`의 모든 명령(허용 목록 안의 정당한 명령 포함)에는 **command cost 예산**이 있어 `find`/`du`/`sort`/`git grep` 같은 고위험 명령이 더 짧은 timeout과 출력 상한으로 클램프됩니다.
- agent에 반환하는 내용(로그/파일 내용)에는 **최소한의 secret 마스킹**(`password=`/`token=`/`Bearer`/AWS/PEM 개인 키 블록 등)이 적용됩니다. 감사 로그 마스킹과 별도로 운영되어 과학 로그를 망가뜨리지 않습니다.

## 설치

자세한 절차는 [docs/ko/QUICKSTART.md](docs/ko/QUICKSTART.md)를 참조하세요.

설치하면 `hpc-mcp` 명령을 쓸 수 있습니다.

## 설정

세 가지 방법이 있고 우선순위는 **CLI > 환경 변수 > 설정 파일 > 기본값**입니다.

### CLI

```bash
hpc-mcp --host my-hpc --user shared_account \
  --root /home/shared_account/alice --local-root "$PWD"

# ssh/sftp 실행 파일을 지정하는 경우(WSL 환경에서 필요할 수 있음):
#   --ssh-bin  /usr/bin/ssh
#   --ssh-bin  @/usr/bin/ssh
#   --ssh-bin  @/mnt/c/Windows/System32/OpenSSH/ssh.exe
#   --sftp-bin @/usr/bin/sftp
```

### 환경 변수

```bash
export HPC_MCP_HOST=my-hpc
export HPC_MCP_USER=shared_account
export HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD
export HPC_MCP_ALLOWED_PARTITIONS=compute,debug
export HPC_MCP_MAX_CPUS=64
export HPC_MCP_MAX_TIME=24:00:00
```

### YAML 설정 파일

지원하는 모든 키, 기본값, 범위, 대응하는 환경 변수는 [`docs/ko/CONFIGURATION.md`](docs/ko/CONFIGURATION.md)에 있습니다. 아래는 자주 쓰는 최소 구성입니다:

```yaml
host: my-hpc
user: shared_account
root: /home/shared_account/alice
local_root: /path/to/local/project

slurm:
  allowed_partitions: [compute]
  max_cpus: 64
  max_nodes: 2
  max_memory_mb: 262144      # 256 GiB
  max_gpus: 4
  max_time: "24:00:00"
  max_concurrent_jobs: 20    # 동시 활성 작업 상한(기본 20, 조정 가능)
```

```bash
hpc-mcp --config config.yaml
```

### SSH 설정(권장)

연결 세부 사항은 `~/.ssh/config`에서 관리하고 `HPC_MCP_HOST`가 Host 별칭을 참조하게 합니다:

```sshconfig
Host my-hpc
    HostName hpc.example.edu
    User shared_account
    IdentityFile ~/.ssh/id_ed25519
```

agent는 개인 키 내용에 접근하지 않습니다.

**비밀번호 없는 로그인이 필수입니다**(hpc-mcp는 `BatchMode=yes`를 강제하며 비밀번호를 요구하지 않습니다). 설정 전에 BatchMode로 키 인증이 준비되었는지 확인하세요. 그렇지 않으면 모든 도구 호출이 `Permission denied`로 실패합니다:

```bash
ssh -o BatchMode=yes my-hpc "echo OK"   # 프롬프트 없이 OK가 나와야 함
ssh-copy-id my-hpc                      # 실패하면 먼저 키 인증을 설정
```

기본값은 `StrictHostKeyChecking=yes`입니다. 첫 연결 전에 수동으로 `ssh my-hpc`를 실행해 호스트 지문을 확인하고 `known_hosts`에 기록하세요. 첫 연결 자동 등록이 꼭 필요하면 설정에서 `ssh.strict_host_key_checking: accept-new`를 지정합니다.

### 연결 자체 점검

```bash
hpc-mcp --host my-hpc --root /home/shared_account/alice --check
```

## MCP 클라이언트 통합

### 권장: 한 명령으로 자동 등록(`hpc-mcp mcp-add`)

설치 후 `mcp-add`로 hpc-mcp를 Codex와 Reasonix 설정에 자동 등록할 수 있습니다. 기록되는 것은 **이식 가능한 런처 스크립트 경로**(`<repo>/scripts/hpc-mcp-run`)이며, 이 스크립트가 해당 머신의 hpc-mcp를 자동으로 찾으므로(conda/venv/PATH) 특정 머신의 conda 경로에 **묶이지 않습니다**. 다른 머신에서는 clone + 설치를 다시 하면 됩니다:

```bash
# 저장소 안에서 실행(저장소의 scripts/hpc-mcp-run을 찾습니다)
cd ~/git_repo/HPC-MCP
hpc-mcp mcp-add --config ~/.config/hpc-mcp/192.168.10.10.yaml

# 또는 인자를 직접 전달
hpc-mcp mcp-add --host my-hpc --user shared_account --root /home/shared_account/alice
```

결과:

- `~/.codex/config.toml`에 `[mcp_servers.hpc]`가 기록됨(command는 `scripts/hpc-mcp-run`을 가리킴)
- `~/.reasonix/config.toml`에 hpc plugin이 기록됨(같은 런처 스크립트를 가리킴)
- 런처 스크립트는 hpc-mcp를 순서대로 찾습니다: `$HPC_MCP_BIN` → PATH → 일반적인 conda/venv 경로. 찾지 못하면 조용히 실패하지 않고 명확한 메시지를 냅니다
- hpc 섹션만 추가/갱신하며 기존의 다른 MCP server / provider 설정을 **망가뜨리지 않습니다**
- 멱등적: 반복 실행해도 중복 섹션이 생기지 않습니다

변경 후 **Codex / Reasonix를 재시작**하세요.

### CC-Switch(MCP 설정 관리자)

[CC-Switch](https://github.com/farion1231/cc-switch)는 여러 MCP server 설정을 JSON으로 관리하고 원클릭으로 전환합니다. 전체 stdio JSON 설정(`command` + `env`), 필드 설명, 자주 묻는 질문은 **[`docs/ko/CC_SWITCH.md`](docs/ko/CC_SWITCH.md)**에 있습니다:

```json
{
  "name": "hpc-mcp",
  "type": "stdio",
  "command": "/home/yourname/git_repo/HPC-MCP/scripts/hpc-mcp-run",
  "args": [],
  "env": {
    "HPC_MCP_HOST": "my-hpc",
    "HPC_MCP_USER": "shared_account",
    "HPC_MCP_ROOT": "/home/shared_account/alice",
    "HPC_MCP_LOCAL_ROOT": "/home/yourname/my-project",
    "HPC_MCP_ALLOWED_PARTITIONS": "compute,debug"
  }
}
```

### 프로젝트 단위 `.mcp.json`(가장 이식성 높음)

저장소에는 `.mcp.json` 템플릿(MCP 표준 프로젝트 단위 설정)이 들어 있습니다. 여기에 `hpc` 항목을 추가하고 command를 `./scripts/hpc-mcp-run`으로 향하게 하세요. 프로젝트 단위 MCP를 지원하는 클라이언트(저장소 디렉터리에서 시작한 Codex/Reasonix 등)는 자동으로 읽습니다. host/root 등은 환경 변수(`${HPC_MCP_HOST}` 등, shell profile에서 정의)로 주입합니다. 다른 머신으로 옮기는 방법은 저장소 clone → hpc-mcp 설치 → 환경 변수 정의 → 저장소 디렉터리에서 클라이언트 시작, 그뿐입니다.

### 수동 설정(선택)

#### Codex

```bash
codex mcp add hpc \
  --env HPC_MCP_HOST=my-hpc \
  --env HPC_MCP_USER=shared_account \
  --env HPC_MCP_ROOT=/home/shared_account/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  -- hpc-mcp
```

#### Reasonix

```bash
reasonix mcp add hpc \
  --env HPC_MCP_HOST=my-hpc \
  --env HPC_MCP_ROOT=/home/shared_account/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute \
  hpc-mcp
```

둘 다 stdio argv 방식으로 시작하며 셸을 거치지 않습니다. 수동 방식에서 `hpc-mcp`가 PATH에 없다면 절대 경로(예: `/path/to/conda/envs/hpc-mcp/bin/hpc-mcp`)로 바꿔야 합니다.

## 도구 목록(22개)

> **각 도구의 인자, 기본값, 제약, 반환 요점**은 **[docs/ko/TOOLS.md](docs/ko/TOOLS.md)**에 있습니다. 아래 표는 색인입니다.

### 저수준 프리미티브

| 도구 | 설명 | annotations |
|---|---|---|
| `hpc.info` | 연결/클러스터 정보(로컬 경로는 노출하지 않음) | readOnly |
| `hpc.files.list` | 디렉터리 나열(bounded 페이징, recursive는 계층별 + cursor) | readOnly |
| `hpc.files.read` | 파일 읽기(bounded slice, 호출당 ≤256KiB) | readOnly |
| `hpc.files.write` | 파일 쓰기(expected_size/mtime/hash 낙관적 동시성 보호) | destructive |
| `hpc.files.mkdir` | 디렉터리 생성 | — |
| `hpc.files.delete` | 삭제 | destructive |
| `hpc.files.upload` | 로컬에서 업로드(SFTP) | destructive |
| `hpc.files.download` | 로컬로 다운로드(SFTP) | readOnly |
| `hpc.shell.run_safe` | 허용 목록 경량 명령(command cost 예산 포함) | readOnly |
| `hpc.slurm.submit` | 계산 작업 제출 | openWorld |
| `hpc.slurm.status` | 작업 상태 | readOnly |
| `hpc.slurm.queue` | 내 작업 큐 | readOnly |
| `hpc.slurm.output` | 작업 stdout/stderr | readOnly |
| `hpc.slurm.cancel` | 작업 취소 | destructive |
| `hpc.slurm.accounting` | sacct 회계 | readOnly |
| `hpc.jobs.wait` | 작업 완료 대기(상한 있음) | readOnly |

### 고수준 agent 도구

| 도구 | 설명 | annotations |
|---|---|---|
| `hpc.files.search` | 예산 있는 정규식 검색(로그 오류 줄 찾기) | readOnly |
| `hpc.jobs.diagnose` | 상태 + 회계 + 로그 꼬리 + 오류 시그니처를 한 번에 진단 | readOnly |
| `hpc.jobs.wait_and_diagnose` | 작업 종료를 기다린 뒤 한 번에 진단 | readOnly |
| `hpc.project.snapshot` | 한 번에 프로젝트 컨텍스트 구축(디렉터리 개요 + git + 작업) | readOnly |
| `hpc.cluster.topo` | 계산 노드 하드웨어 토폴로지(CPU 모델/SIMD/NUMA/cache) + 병렬 파라미터 제안 | readOnly, openWorld |
| `hpc.job.run` | runtime profile 고수준 제출(julia/python/moose/bash) | openWorld |

### 호출 예시

Julia 작업을 제출합니다(파티션은 `hpc.info`가 반환하는 허용 목록에서 고를 수 있고, 생략하면 설정의 첫 번째가 쓰입니다):

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "job_name": "demo-test",
    "working_directory": "/home/shared_account/alice/demo_benchmark",
    "partition": "compute",
    "cpus_per_task": 8,
    "time_limit": "00:30:00",
    "command": ["julia", "--project=.", "scripts/test.jl"]
  }
}
```

인자 기본값은 서버 설정 또는 `.sh` 스크립트의 `#SBATCH` 지시문에서 옵니다(명시한 인자가 우선). 파티션은 설정의 허용 목록에 맞아야 하며 아니면 거부됩니다. `.sh` 작업 스크립트 경로를 직접 제출할 수도 있습니다:

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "command": "/home/shared_account/alice/proj/run.sh"
  }
}
```

거부되면 실행 가능한 정보가 반환됩니다:

```text
Operation denied.

Reason:
'julia' is a computational/build workload and is forbidden on HPC login nodes.

Use:
hpc.slurm.submit
```

## 병렬 최적화 파라미터(`hpc.cluster.topo`)

로그인 노드 허용 목록은 **의도적으로** `lscpu`/`numactl`을 통과시키지 않으며 `/proc`도 경로 샌드박스 밖에 있습니다. 따라서 CPU 모델, SIMD 명령 집합, NUMA 토폴로지 같은 파라미터는 계산 노드에서만 얻을 수 있습니다. `hpc.cluster.topo`는 이 흐름을 한 번의 호출로 만듭니다:

```json
{
  "tool": "hpc.cluster.topo",
  "arguments": { "partition": "thcp1" }
}
```

- **첫 호출**(또는 캐시 만료, `refresh: true`)은 **1 CPU 수집 작업**을 제출해 계산 노드에서 `lscpu` / `/proc/cpuinfo` / `numactl --hardware` / `/proc/meminfo`를 읽고, 로그인 노드 `sinfo`의 파티션 뷰와 병합합니다.
- **반환**: CPU 모델과 sockets/cores/threads, SIMD 명령 집합(AVX2/AVX-512/SVE), NUMA 도메인과 거리 행렬, 캐시 계층, 노드 메모리, 파티션 features/GRES, 그리고 도출된 병렬 파라미터 제안(`ntasks_per_node`, `cpus_per_task`, `--hint=nomultithread`, `--cpu-bind=cores`, `OMP_NUM_THREADS`, `-map-by numa`, 권장 `--mem`)과 각 제안의 근거, "물리 코어 vs 논리 코어"의 트레이드오프.
- **캐시**: `topology.cache_ttl_seconds`(기본 24h) 동안 프로세스 내 캐시 또는 원격 `$ROOT/.hpc-mcp/topo/topology_<partition>.json`에서 가져오므로, 이후 세션이 같은 머신으로 반복해서 큐에 서지 않습니다.
- **큐 대기**: 수집 작업이 아직 스케줄되지 않았으면 `status: "pending"` + `job_id`를 반환합니다. 다시 호출하면 그 작업이 재사용되고 중복 제출되지 않습니다.
- **안전성**: 수집 스크립트는 **서버 측 고정 내용**이며(agent 인자는 스크립트에 들어가지 않습니다), **큐를 조회하지 않습니다**(`squeue`/`sacct`/`scontrol`). 노드 로컬 하드웨어 정보만 읽어 공용 계정 격리 규칙을 지킵니다. 작업 소유권, 파티션 허용 목록, 동시 실행 상한은 그대로 유효합니다.
- 관리자는 `topology.enabled: false`로 이 도구를 완전히 끌 수 있습니다(수집 작업을 전혀 제출하지 않음).

## 권장 워크플로(agent)

1. `hpc.info`로 환경 파악 → 2. `hpc.project.snapshot`으로 프로젝트 컨텍스트를 한 번에 구축 →
3. 병렬성/성능이 관련되면 `hpc.cluster.topo`로 CPU/SIMD/NUMA와 권장 병렬 파라미터를 한 번에 획득 →
4. `hpc.files.search`로 먼저 위치를 찾고(로그 오류 줄 등), `hpc.files.read`로 작은 컨텍스트 읽기 →
5. `hpc.files.write`로 원격 편집 → 6. 빌드/테스트/계산은 모두 `hpc.slurm.submit` →
7. `hpc.jobs.diagnose`로 실패 작업을 한 번에 진단 → 8. 분석, 수정, 반복.

원칙: **한 번의 고수준 호출로 끝낼 수 있는 것을 여러 저수준 호출로 쪼개지 마세요.** `hpc.files.read`는 bounded slice이므로 offset=0에서 EOF까지 읽지 마세요. 반복되는 읽기 전용 쿼리는 서버 측 TTL 캐시가 중복 제거합니다.

자세한 내용은 [`skills/hpc-development/SKILL.md`](skills/hpc-development/SKILL.md)를 참조하세요.

## 보안 테스트

```bash
python -m pytest tests/ -q
```

대상: 경로 횡단, 심볼릭 링크 탈출, 명령 주입, login/compute 경계, Slurm 리소스 남용, 작업 격리.

발견 사항, 수정, 잔여 위험의 전체 기록은 [`docs/ko/SECURITY_REVIEW.md`](docs/ko/SECURITY_REVIEW.md), 모듈 경계와 요청 흐름은 [`docs/ko/ARCHITECTURE.md`](docs/ko/ARCHITECTURE.md)에 있습니다.

## 제한(v1에서 명시적으로 다루지 않음)

임의 원격 셸, 임의 SSH 호스트, sudo, 포트 포워딩, 다중 호스트, 원격 상주 daemon, HTTP MCP, 자동 자격 증명 관리.

## 문제 해결

- **시작 시 "No HPC host/root configured"**: 세 가지 설정 방법 중 하나로 host와 root를 제공하세요.
- **모든 도구가 "No Slurm partitions are allowed"로 DENY**: `slurm.allowed_partitions`를 설정하세요(기본은 비어 있고 fail-closed).
- **SSH 255 오류**: 먼저 `hpc-mcp ... --check`로 검증하고, `~/.ssh/config`와 BatchMode 키 인증이 되는지 확인하세요.
- **로그**: stderr로 출력됩니다(stdout은 MCP 프로토콜 전용). `--log-file`로 파일에 추가할 수 있습니다.

## 문서

| 문서 | 내용 |
|---|---|
| [docs/ko/QUICKSTART.md](docs/ko/QUICKSTART.md) | 단계별 설치 + 각 파라미터 설명 + 진단 표 |
| [docs/ko/CONFIGURATION.md](docs/ko/CONFIGURATION.md) | 모든 YAML 키, 기본값, 범위, 환경 변수 |
| [docs/ko/TOOLS.md](docs/ko/TOOLS.md) | 22개 도구의 인자/기본값/제약 |
| [docs/ko/CC_SWITCH.md](docs/ko/CC_SWITCH.md) | CC-Switch JSON 설정, 필드 설명, FAQ |
| [docs/ko/ARCHITECTURE.md](docs/ko/ARCHITECTURE.md) | 모듈 경계, 요청 흐름, 격리 계층 |
| [docs/ko/SECURITY_REVIEW.md](docs/ko/SECURITY_REVIEW.md) | 발견 사항, 수정, 검증, 잔여 위험 |
| [SECURITY.ko.md](SECURITY.ko.md) | 위협 모델과 강제되는 경계 |

## 라이선스

MIT. [LICENSE](LICENSE)를 참조하세요.
