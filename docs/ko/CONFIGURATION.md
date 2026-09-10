# 설정 파일 레퍼런스(config YAML)

다른 언어: [English](../CONFIGURATION.md) | [简体中文](../zh-CN/CONFIGURATION.md) | [日本語](../ja/CONFIGURATION.md) | [繁體中文](../zh-TW/CONFIGURATION.md)

이 문서는 **YAML 설정 파일이 지원하는 모든 파라미터**(위치, 타입, 기본값, 범위, 대응하는 환경 변수 `HPC_MCP_*`, CLI 옵션)를 정리합니다. `config/example.yaml`은 그대로 복사해 쓸 수 있는 완전한 예시입니다.

> **우선순위(높음 → 낮음)**: CLI 옵션 > 환경 변수 > 설정 파일 > 내장 기본값.
> 같은 파라미터가 여러 곳에 있으면 우선순위가 높은 쪽이 적용됩니다.

> **키 이름 규칙**: 설정 파일은 **밑줄** 키(`local_root`, `allowed_partitions`,
> `ssh_bin`)를 씁니다. 하이픈 형식(`local-root`, `ssh-bin`)도 별칭으로 인식되지만 권장하지 않습니다.
> 알 수 없는 키는 오류가 아니라 `Ignoring unknown config key 'xxx'` 경고 한 줄을 출력하고
> 무시됩니다. 이 줄이 보이면 철자가 틀린 것입니다.

> 이 파일은 **서버 설정 파라미터**를 설명합니다. 시작/자체 점검용 CLI 파라미터는
> [QUICKSTART.md](QUICKSTART.md) 4절, **각 MCP 도구의 파라미터**는 [TOOLS.md](TOOLS.md)를 참조하세요.

---

## 최상위 파라미터

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `host` | string | —(필수) | SSH 호스트. `~/.ssh/config`의 `Host` 별칭 권장. 환경 변수 `HPC_MCP_HOST`, CLI `--host` |
| `user` | string | — | SSH 사용자(공용 계정 가능). `HPC_MCP_USER`, `--user` |
| `port` | int | `22` | SSH 포트(1–65535). `HPC_MCP_PORT`, `--port` |
| `root` | string | —(필수) | 원격 **사용자 전용** 루트 디렉터리. agent의 모든 원격 작업이 이 아래로 제한됩니다. 절대 경로여야 하고 `/`일 수 없습니다. `HPC_MCP_ROOT`, `--root` |
| `local_root` | string | 현재 작업 디렉터리 | 로컬 업로드/다운로드를 허용하는 디렉터리(여러 개는 `local_roots`). `.ssh`/`.gnupg` 같은 자격 증명 디렉터리는 불가. `HPC_MCP_LOCAL_ROOT`, `--local-root` |
| `local_roots` | list[string] | `[현재 디렉터리, 시스템 임시 디렉터리]` | 로컬 허용 디렉터리 목록. `local_root`와 택일이며 둘 다 있으면 `local_roots`가 우선. `HPC_MCP_LOCAL_ROOTS`(콤마 구분) |
| `identity_file` | string | `~/.ssh/config`에서 | SSH 개인 키 경로(agent는 키 내용에 접근하지 않음). `HPC_MCP_IDENTITY_FILE`, `--identity-file` |
| `ssh_bin` | string | PATH에서 탐색 | ssh 실행 파일: 이름만, 절대 경로, 또는 `@` 접두사(WSL용). `HPC_MCP_SSH_BIN`, `--ssh-bin` |
| `sftp_bin` | string | PATH에서 탐색 | sftp 실행 파일. 형식은 `ssh_bin`과 동일. `HPC_MCP_SFTP_BIN`, `--sftp-bin` |
| `wait_max_seconds` | int | `3600` | `hpc.slurm.wait` / `wait_and_diagnose`의 최대 대기 초(≤ 7일). `HPC_MCP_WAIT_MAX_SECONDS` |
| `cache_ttl_seconds` | float | `2.0` | 읽기 전용 쿼리 중복 제거 캐시 TTL(초). `0`이면 비활성. `HPC_MCP_CACHE_TTL_SECONDS` |
| `log_file` | string | stderr만 | 로그를 추가 기록할 파일 경로(`~`는 확장됨). `HPC_MCP_LOG_FILE`, `--log-file` |
| `log_level` | string | `INFO` | 로그 레벨: `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. `HPC_MCP_LOG_LEVEL`, `--log-level` |

> `ssh.*` 하위 섹션 파라미터는 최상위에도 직접 쓸 수 있습니다(예: `connect_timeout`,
> `strict_host_key_checking`). `ssh:` 하위 섹션과 동등합니다.

---

## `ssh:` 하위 섹션

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `host` | string | — | 최상위 `host`와 동일 |
| `port` | int | `22` | 최상위 `port`와 동일 |
| `user` | string | — | 최상위 `user`와 동일 |
| `identity_file` | string | — | 최상위 `identity_file`과 동일 |
| `connect_timeout` | int | `15` | SSH 연결 타임아웃(초, ≤ 3600). `HPC_MCP_CONNECT_TIMEOUT` |
| `command_timeout` | int | `30` | 원격 명령 한 건의 타임아웃(초, ≤ 86400). `HPC_MCP_COMMAND_TIMEOUT` |
| `strict_host_key_checking` | string | `yes` | 호스트 키 검증: `yes`(`known_hosts`에 고정되어 있어야 함, 권장) / `accept-new`(첫 연결 시 자동 등록). **`no`는 거부됩니다.** `HPC_MCP_STRICT_HOST_KEY_CHECKING` |
| `ssh_bin` | string | PATH | 최상위 `ssh_bin`과 동일 |
| `sftp_bin` | string | PATH | 최상위 `sftp_bin`과 동일 |

---

## `slurm:` 하위 섹션

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `allowed_partitions` | list[string] | `[]`(비어 있음 = 모든 제출 거부, fail-closed) | 제출을 허용하는 파티션 허용 목록. 이름에 `/`나 제어 문자를 쓸 수 없습니다. `HPC_MCP_ALLOWED_PARTITIONS`(콤마 구분) |
| `max_nodes` | int | `2` | 작업당 노드 수 상한(≥ 1). `HPC_MCP_MAX_NODES` |
| `max_cpus` | int | `64` | 작업당 총 CPU 상한: `nodes × ntasks × cpus_per_task`(≥ 1). `HPC_MCP_MAX_CPUS` |
| `max_memory_mb` | int | `262144`(256 GiB) | 작업당 메모리 상한(MiB, ≥ 1). `HPC_MCP_MAX_MEMORY_MB` |
| `max_gpus` | int | `4` | 작업당 GPU 상한(≥ 0). `HPC_MCP_MAX_GPUS` |
| `max_time` | string | `"24:00:00"` | 작업당 시간 상한. Slurm 형식: `HH:MM:SS`, `D-HH:MM:SS`. 일수는 넘칠 수 있음(`"2-24:00:00"` = 3일). `HPC_MCP_MAX_TIME` |
| `max_concurrent_jobs` | int | `20` | **동시에 활성인 작업의 최대 수.** 제출 시 이 인스턴스의 레지스트리에서 `squeue`와 `sacct` 모두 종료 상태가 아닌 작업을 세고, 상한에 도달하면 새 제출을 거부하며 대기를 안내합니다. 종료된(`COMPLETED`/`FAILED`/`CANCELLED`/…) 작업은 슬롯을 자동으로 반환합니다. `squeue` 조회는 성공했지만 큐에 없고 `sacct`에도 기록이 없으면 마찬가지로 반환합니다(accounting 기록이 만료되어 할당량이 영구히 묶이는 것을 방지). `squeue` 조회가 실패하면 보수적으로 계속 셉니다(fail-closed). `HPC_MCP_MAX_CONCURRENT_JOBS` |

> 모든 숫자 `max_*` 필드는 간단한 산술식을 받습니다: `+ - * /`와 괄호. 예: `"4*16"`, `"128/4"`.
> 환경 변수에서도 동일하게 됩니다(예: `HPC_MCP_MAX_CPUS=4*16`).

---

## `shell:` 하위 섹션

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `safe_commands` | list[string] | `[]`(내장 허용 목록에 추가) | 내장 최소 허용 목록에 추가할 명령 basename(`/` 미포함, 128자 이하). `HPC_MCP_SAFE_COMMANDS`(콤마 구분) |
| `max_exec_seconds` | int | `30` | safe 명령 한 건의 실행 시간 상한(초, ≤ 86400). `HPC_MCP_SHELL_MAX_EXEC_SECONDS` |
| `max_output_bytes` | int | `1048576`(1 MiB) | safe 명령 한 건의 출력 상한(바이트). `HPC_MCP_MAX_OUTPUT_BYTES` |

---

## `files:` 하위 섹션

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `max_read_bytes` | int | `1048576`(1 MiB) | `hpc.files.read` 한 번 읽기의 **하드 상한**(바이트). `HPC_MCP_MAX_READ_BYTES` |
| `max_read_slice_bytes` | int | `262144`(256 KiB) | `hpc.files.read` 한 번의 반환 상한(bounded slice). agent가 거대한 로그를 한 번에 끝까지 읽는 것을 방지합니다. `HPC_MCP_MAX_READ_SLICE_BYTES` |
| `max_write_bytes` | int | `10485760`(10 MiB) | `hpc.files.write` 한 번의 쓰기 상한(바이트). `HPC_MCP_MAX_WRITE_BYTES` |
| `max_list_entries` | int | `2000` | `hpc.files.list` 한 번의 반환 항목 상한. `HPC_MCP_MAX_LIST_ENTRIES` |
| `max_recursive_depth` | int | `3` | `hpc.files.list recursive`의 재귀 깊이 상한(≤ 64). `HPC_MCP_MAX_RECURSIVE_DEPTH` |
| `search_max_matches` | int | `200` | `hpc.files.search`의 파일당 최대 매치 수(≤ 100000). `HPC_MCP_SEARCH_MAX_MATCHES` |
| `search_max_context_lines` | int | `10` | 각 매치에 붙는 컨텍스트 줄 수(≤ 1000). `HPC_MCP_SEARCH_MAX_CONTEXT_LINES` |
| `search_max_scan_bytes` | int | `67108864`(64 MiB) | 파일당 스캔 상한(바이트). `HPC_MCP_SEARCH_MAX_SCAN_BYTES` |
| `search_max_files` | int | `1000` | 검색 한 번에 스캔할 최대 파일 수(≤ 1000000). `HPC_MCP_SEARCH_MAX_FILES` |
| `search_max_depth` | int | `6` | 검색 재귀 깊이 상한(≤ 64). `HPC_MCP_SEARCH_MAX_DEPTH` |
| `search_timeout` | int | `5` | 원격 검색 한 건의 타임아웃(초, ≤ 3600). `HPC_MCP_SEARCH_TIMEOUT` |

---

## `topology:` 하위 섹션

`hpc.cluster.topo`(계산 노드 하드웨어/NUMA/SIMD 토폴로지 탐색)를 제어합니다. 이 도구는 첫 호출
(또는 캐시 만료, `refresh=true`)에서 지정한 파티션에 **1 CPU 수집 작업**을 제출하고,
계산 노드에서 `lscpu` / `/proc/cpuinfo` / `numactl --hardware` / `/proc/meminfo`를 읽어
로그인 노드의 `sinfo` 파티션/노드 뷰와 병합한 뒤, CPU 모델, sockets/cores/threads,
SIMD 명령 집합, NUMA 도메인과 거리, 캐시 계층, 노드 메모리, 그리고 도출된 병렬 파라미터
권장(`ntasks_per_node`, `cpus_per_task`, `--cpu-bind`/`--hint`, `OMP_NUM_THREADS`)을 반환합니다.

수집 스크립트는 **서버 측 고정 내용**입니다. agent가 준 파라미터가 스크립트에 기록되는 일은 없으며,
스크립트는 `squeue`/`sacct`/`scontrol`을 **호출하지 않습니다**(공용 계정 격리). 노드 로컬 하드웨어
정보만 읽습니다.

| 키 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `enabled` | bool | `true` | `hpc.cluster.topo` 등록 여부. `false`이면 도구가 agent에 노출되지 않아 수집 작업도 전혀 제출되지 않습니다. `HPC_MCP_TOPOLOGY_ENABLED` |
| `cache_ttl_seconds` | int | `86400`(24 h) | 토폴로지 결과의 신선도: 프로세스 내 캐시와 원격 `$ROOT/.hpc-mcp/topo/topology_<partition>.json`이 이 TTL을 공유해 재큐잉을 피합니다. `0` = 호출마다 재수집. `HPC_MCP_TOPOLOGY_CACHE_TTL_SECONDS` |
| `wait_seconds` | int | `300` | 수집 작업 종료를 기다리는 초. 타임아웃이면 `status: "pending"`과 `job_id`를 반환합니다(작업은 큐에 남고 다음 호출에서 재사용되며 중복 제출되지 않음). `HPC_MCP_TOPOLOGY_WAIT_SECONDS` |
| `collect_time_limit` | string | `"00:03:00"` | 수집 작업 자체의 Slurm 시간 상한(형식은 `slurm.max_time`과 동일). `HPC_MCP_TOPOLOGY_COLLECT_TIME_LIMIT` |

> 수집 작업도 `slurm.allowed_partitions`, `max_concurrent_jobs`, 일반적인 작업 소유권 규칙의
> 적용을 받습니다. `allowed_partitions`가 비어 있으면 이 도구는 fail-closed(호출 거부)입니다.
> 탐색한 파티션 이름은 `sinfo -p <partition>`과 캐시 파일 이름에 쓰이므로 일반 Slurm 정책보다
> 엄격하여 `[A-Za-z0-9_.-]`만 허용합니다.

---

## 전체 예시

```yaml
# HPC-MCP 전체 설정 예시("필수"로 표시된 것을 제외하고 모든 키는 선택)
host: my-hpc                      # 필수: SSH Host 별칭 또는 주소
user: shared_account
port: 22
root: /home/shared_account/alice  # 필수: 원격 사용자 전용 루트 디렉터리
local_root: /home/alice/proj      # 로컬 업로드/다운로드 디렉터리
# local_roots: [ /home/alice/proj, /tmp ]
# identity_file: ~/.ssh/id_ed25519
# ssh_bin: /usr/bin/ssh
# sftp_bin: @/mnt/c/Windows/System32/OpenSSH/ssh.exe

ssh:
  connect_timeout: 15
  command_timeout: 30
  strict_host_key_checking: "yes"   # 또는 "accept-new". "no"는 거부됩니다

slurm:
  allowed_partitions: [compute]     # 반드시 설정: 기본값이 비어 있으면 모든 제출 거부
  max_nodes: 2
  max_cpus: 64                      # "4*16" 같은 식도 가능
  max_memory_mb: 262144             # 256 GiB
  max_gpus: 4
  max_time: "24:00:00"
  max_concurrent_jobs: 20           # 동시 활성 작업 상한, 기본 20

shell:
  safe_commands: []                 # 추가로 허용할 명령 basename
  max_exec_seconds: 30
  max_output_bytes: 1048576

files:
  max_read_bytes: 1048576
  max_read_slice_bytes: 262144
  max_write_bytes: 10485760
  max_list_entries: 2000
  max_recursive_depth: 3
  search_max_matches: 200
  search_max_context_lines: 10
  search_max_scan_bytes: 67108864
  search_max_files: 1000
  search_max_depth: 6
  search_timeout: 5

topology:
  enabled: true                     # false = hpc.cluster.topo를 등록하지 않음(수집 작업도 제출하지 않음)
  cache_ttl_seconds: 86400          # 토폴로지 신선도(24h). 0 = 매번 재수집
  wait_seconds: 300                 # 수집 작업 대기 상한. 초과 시 pending 반환
  collect_time_limit: "00:03:00"    # 수집 작업 자체의 Slurm 시간 상한

wait_max_seconds: 3600
cache_ttl_seconds: 2.0
log_file: ~/.local/share/hpc-mcp/hpc-mcp.log
log_level: INFO
```

# 설정과 연결 검증
```bash
source ~/venvs/hpc-mcp/bin/activate
hpc-mcp --config ~/.config/hpc-mcp/config.yaml --check
```
