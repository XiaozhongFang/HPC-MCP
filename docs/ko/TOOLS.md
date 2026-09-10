# MCP 도구 레퍼런스

다른 언어: [English](../TOOLS.md) | [简体中文](../zh-CN/TOOLS.md) | [日本語](../ja/TOOLS.md) | [繁體中文](../zh-TW/TOOLS.md)

이 문서는 HPC-MCP가 agent에 노출하는 **22개 도구**의 용도, 인자, 기본값, 제약, 반환 요점을 설명합니다. [README.ko.md](../../README.ko.md)의 "도구 목록" 표를 확장한 것입니다: README는 *어떤 도구가 있는지*, 이 파일은 *각 인자를 어떻게 채우고 무엇이 제한하는지*에 답합니다.

- **설정 파라미터**(host/root/파티션 허용 목록/리소스 상한…)는 [CONFIGURATION.md](CONFIGURATION.md).
- **명령줄 인자**(`--host`/`--root`/`--check`…)는 [QUICKSTART.md](QUICKSTART.md) 4절.
- **Agent 행동 지침**(언제 어떤 도구를 쓰는지)은 [skills/hpc-development/SKILL.md](../../skills/hpc-development/SKILL.md).

---

## 공통 규약(모든 도구에 적용)

| 규약 | 설명 |
|---|---|
| 경로 샌드박스 | 모든 원격 경로는 **절대 경로**여야 하고 `$HPC_MCP_ROOT` 안이어야 합니다. `..`, root 밖으로 나가는 심볼릭 링크, `/etc` 등은一律 거부됩니다. 로컬 경로는 `local_roots`(기본 = 프로세스 작업 디렉터리 + `/tmp`) 안이어야 하며 `.ssh`, 개인 키, 심볼릭 링크는 거부됩니다. |
| 서버 측 클램프 | 모든 "예산" 인자(바이트, 개수, 깊이, 타임아웃)는 **요청값 ≤ 서버 상한**입니다. 상한은 CONFIGURATION.md에 있습니다. 0이나 음수를 넘기면 거부됩니다(fail-closed). |
| 페이징 | 재귀 나열은 `page_size` + `cursor`로 한 계층씩 진행하며 트리 전체를 스캔하지 않습니다. 읽기는 `offset` + `next_offset`으로 이어집니다. |
| 쿼리 중복 제거 | 멱등적 읽기 전용 도구는 `도구+인자`로 `cache_ttl_seconds`(기본 2초) 동안 중복 제거되어 반복 호출이 SSH 왕복을 만들지 않습니다. 쓰기 작업은 캐시를 비웁니다. |
| 응답 마스킹 | 로그/파일 내용의 `password=`, `token=`, `Bearer`, AWS 자격 증명, PEM 개인 키 블록 등은 `[REDACTED]`로 바뀝니다. 과학 로그 내용은 영향을 받지 않습니다. |
| 실패 즉시 거부 | 설정 누락, 경로 해석 불가, 명령 파싱 실패, 파티션 불확실, SSH 이상 등 모든 불확실한 상황은 **DENY**이며, 실행 가능한 대안(`Reason:` + `Use:`)이 함께 제시됩니다. |
| annotations | `readOnly` = 원격 상태를 변경하지 않음, `destructive` = 삭제/덮어쓰기 가능, `idempotent` = 반복 호출에 추가 부작용 없음, `openWorld` = 클러스터 계산 측에 도달(작업 제출). |

---

## 1. 환경과 프로젝트 컨텍스트

### `hpc.info`

연결과 클러스터 능력 개요. **인자 없음.**

반환: `slurm_available`, `cluster`, `working_root`, `workspace_available`, `transfer_enabled`, `allowed_partitions`(제출 가능한 파티션 허용 목록), `max_cpus`/`max_nodes`/`max_memory_mb`/`max_gpus`/`max_time`.

> 보안상 SSH host/user/port와 로컬 경로 root는 **반환하지 않습니다**(agent가 MCP를 우회해 직접 연결하는 것을 방지).
> 리소스를 요청하기 전에 여기서 상한을 확인하세요.

### `hpc.project.snapshot` — `readOnly, idempotent`

한 번의 호출로 프로젝트 컨텍스트를 구축합니다: 얕은 디렉터리 개요 + 주요 소스/로그 파일 크기 + 읽기 전용 git 상태 + 이 프로젝트에서 추적 중인 작업.

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `path` | string | ✅ | — | `$HPC_MCP_ROOT` 안의 절대 경로 |
| `depth` | int | — | `2` | 열거 깊이. 서버에서 `files.max_recursive_depth`로 다시 클램프 |
| `include_git` | bool | — | `true` | 읽기 전용 git 요약(`git status` 등) 포함 여부 |
| `include_jobs` | bool | — | `true` | 이 프로젝트에서 추적 중인 작업 포함 여부 |

> `snapshot` 한 번이 `list` + `read` + `git status` + `queue`의 여러 왕복을 대체합니다.

---

## 2. 원격 파일

### `hpc.files.list` — `readOnly, idempotent`

디렉터리를 나열합니다. 재귀 시 **한 계층씩 페이징**합니다.

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 디렉터리 절대 경로(root 안) |
| `recursive` | bool | — | `false` | 재귀 나열(계층별로 진행) |
| `max_entries` | int | — | 서버 `files.max_list_entries`(2000) | 한 번에 반환하는 항목 수 하드 상한 |
| `page_size` | int | — | `max_entries`와 동일 | 페이지당 항목 수 |
| `cursor` | string | — | — | 이전 페이지가 반환한 `next_cursor`. 재귀 나열을 이어갈 때 사용 |
| `max_depth` | int | — | 서버 `files.max_recursive_depth`(3) | 재귀 깊이 상한 |

### `hpc.files.read` — `readOnly, idempotent`

파일의 한 슬라이스를 읽습니다(**bounded slice**, 호출당 `files.max_read_slice_bytes` 이하, 기본 256 KiB).

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 파일 절대 경로(root 안) |
| `max_bytes` | int | — | 서버 `files.max_read_bytes`(1 MiB) | 이번 읽기의 바이트 상한. 슬라이스 상한으로도 클램프됨 |
| `offset` | int | — | `0` | 시작 바이트 오프셋 |

반환: `size`, `bytes`, `truncated`, `end_of_file`, `next_offset`, `content`(UTF-8, 잘못된 바이트는 대체).

> 큰 로그를 조사할 때는 먼저 `hpc.files.search`로 줄을 찾고, 그 `offset`에서 작게 읽으세요.
> **`offset=0`에서 EOF까지 계속 읽지 마세요.**

### `hpc.files.search` — `readOnly, idempotent`

예산이 있는 정규식 검색(POSIX ERE). "먼저 위치를 찾고, 다음에 정독"을 위한 기능입니다.

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 파일 또는 디렉터리 절대 경로 |
| `pattern` | string | ✅ | — | 확장 정규식(단일 인용 인자로 전달되며 제어 문자는 거부) |
| `max_matches` | int | — | `files.search_max_matches`(200) | 최대 매치 수 |
| `context_lines` | int | — | `files.search_max_context_lines`(10) | 매치당 컨텍스트 줄 수 |
| `max_scan_bytes` | int | — | `files.search_max_scan_bytes`(64 MiB) | 파일당 스캔 상한 |
| `max_files` | int | — | `files.search_max_files`(1000) | 트리에서 스캔할 최대 파일 수 |
| `max_depth` | int | — | `files.search_max_depth`(6) | 트리 깊이 상한 |
| `timeout` | int | — | `files.search_timeout`(5 s) | 원격 검색 타임아웃 |

### `hpc.files.write` — `destructive`

파일을 씁니다. **낙관적 동시성 보호**를 지원합니다(공용 계정에서 다른 사람이 방금 바꾼 코드를 덮어쓰지 않도록).

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 대상 절대 경로 |
| `content` | string | ✅ | — | 텍스트 내용(전체 상한 `files.max_write_bytes`, 기본 10 MiB) |
| `append` | bool | — | `false` | 덮어쓰기가 아니라 추가 |
| `expected_size` | int | — | — | 현재 바이트 수의 기대값. 맞지 않으면 **쓰기 거부** |
| `expected_mtime` | int | — | — | 현재 mtime(epoch 초)의 기대값. 맞지 않으면 거부 |
| `expected_sha256` | string | — | — | 현재 SHA-256(64자 16진)의 기대값. 맞지 않으면 거부 |

> 세 `expected_*`는 자유롭게 조합할 수 있습니다. 지정하면 모두 맞아야 하며, 파일이 없으면 역시 거부됩니다.

### `hpc.files.mkdir` — `destructive`

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 디렉터리 경로 |
| `parents` | bool | — | `false` | `mkdir -p`와 동일(가장 가까운 존재하는 조상이 root 안인지 검증) |

### `hpc.files.delete` — `destructive`

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 대상 경로(root 자체 삭제는 불가) |
| `recursive` | bool | — | `false` | 재귀 삭제(`rm -rf`) |

### `hpc.files.upload` / `hpc.files.download`

SFTP 전송. **양쪽 끝이 샌드박스화**됩니다(로컬 ∈ `local_roots`, 원격 ∈ `$HPC_MCP_ROOT`).

| 도구 | 인자 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| `hpc.files.upload`(destructive) | `local_path` | string | ✅ | 로컬 원본 파일 경로 |
| | `remote_path` | string | ✅ | 대상 경로(root 안) |
| `hpc.files.download`(readOnly) | `remote_path` | string | ✅ | 원격 원본 파일 경로 |
| | `local_path` | string | ✅ | 로컬 대상 경로(`local_roots` 안) |

> 두 경로 모두 심볼릭 링크를 거부합니다. 로컬 측은 추가로 `.ssh`/`.gnupg`와 개인 키 파일 이름을 거부합니다. 전송 후 크기 상한을 검증합니다.

---

## 3. 로그인 노드 조회

### `hpc.shell.run_safe` — `readOnly`

로그인 노드에서 **허용 목록에 있는 명령 하나**를 실행합니다(`ls`/`find`/`cat`/`grep`/`head`/`tail`/`wc`/`sort`/`uniq`/`stat`/`du`/`df`/`git status|diff|log`/`module list|avail`/`sinfo`/`env` 등).

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `command` | string | ✅ | — | 단일 명령. 파이프, `&&`, `;`, 리다이렉션, `$()`, 백틱, 백그라운드 `&`는 **지원하지 않음** |
| `cwd` | string | — | `$HPC_MCP_ROOT` | 작업 디렉터리(root 안, realpath 검증) |
| `timeout` | int | — | `shell.max_exec_seconds`(30 s) | 명령 비용 계층으로 다시 클램프 |

제약(코드 수준 강제):

- 계산/빌드 프로그램은 **실행할 수 없습니다**(`julia`/`python`/`make`/`cmake`/`mpirun`/`pytest`/`nvcc`…) → `hpc.slurm.submit`을 사용하세요.
- `squeue`/`sacct`/`scontrol`은 **실행할 수 없습니다** → 소유권 검사가 있는 Slurm 도구를 사용하세요.
- 경로 형식 실행(`./prog`, `/usr/bin/julia`), `find -exec`, `git -c`, 중첩 셸은 **불가**합니다.
- 명령 비용 계층: LOW(15 s / 256 KiB, `ls`/`sinfo` 등), MEDIUM(30 s / 512 KiB, `cat`/`grep` 등), HIGH(10 s / 512 KiB, `find`/`du`/`sort`/`git grep` 등).

반환: `exit_code`, `stdout`, `stderr`, `cost_tier`, `timeout_seconds`(타임아웃 시 `timed_out: true`).

---

## 4. Slurm 작업

### `hpc.slurm.submit` — `openWorld`

계산 작업을 제출합니다(**유일한** 계산 진입점이며 서버 측 리소스 정책을 따릅니다).

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `command` | array 또는 string | ✅ | — | 프로그램 argv(예: `["julia","--project=.","test/runtests.jl"]`), 또는 **단일 `.sh` 스크립트 경로**(이 경우 `#SBATCH` 지시문이 기본값이 됨) |
| `job_name` | string | — | `"job"` | 작업 이름(`[A-Za-z0-9_.-]`로 정규화, 64자 이하) |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | 작업 디렉터리(root 안, realpath 검증) |
| `partition` | string | — | 설정의 허용 목록 첫 번째 | `slurm.allowed_partitions`에 맞아야 함 |
| `nodes` | int | — | `1` | ≤ `slurm.max_nodes` |
| `ntasks` | int | — | `1` | 태스크 수 |
| `cpus_per_task` | int | — | `1` | 그리고 `nodes × ntasks × cpus_per_task ≤ slurm.max_cpus` |
| `memory` | string | — | 없음 | 예: `"16G"`, `"8000"`(MiB), `"2T"`. ≤ `slurm.max_memory_mb` |
| `time_limit` | string | — | `slurm.max_time` | `HH:MM:SS`, `D-HH:MM:SS`, `MM:SS`, 또는 분 단위. ≤ `slurm.max_time` |
| `gpus` | int | — | `0` | ≤ `slurm.max_gpus` |
| `environment` | object | — | — | 추가 환경 변수(이름은 `[A-Za-z_][A-Za-z0-9_]*`에 맞아야 함) |

동작: 스크립트는 서버에서 생성되며(`#SBATCH`는 서버가 도출) stdout/stderr는 항상 `$HPC_MCP_ROOT/.hpc-mcp/jobs/<job-id>/`로 수집됩니다. 제출 직후 `squeue -j`/`sacct -j`로 작업 소유권을 교차 확인하고, 확인되지 않은 작업은 등록되지 않습니다. 동시에 활성인 작업 수는 `slurm.max_concurrent_jobs`로 제한됩니다.

### `hpc.job.run` — `openWorld`

**신뢰된 runtime profile**로 제출을 단순화합니다. 내부적으로는 `hpc.slurm.submit`과 완전히 같은 정책을 거칩니다.

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `runtime` | string | ✅ | — | 열거: `julia`, `python`, `moose`, `bash`, `shell` |
| `script` | string | ✅ | — | 스크립트 절대 경로(root 안). **moose**에서는 실행 파일 경로 전달 |
| `args` | array\<string\> | — | — | 추가 argv. **moose에서는 필수**(`[input.i]` 등). 실제 명령은 `mpirun -np <ntasks> -- <script> <args…>` |
| `job_name` | string | — | runtime 이름 | 작업 이름 |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | 작업 디렉터리 |
| `partition` | string | — | 허용 목록 첫 번째 | submit과 동일 |
| `nodes` / `ntasks` / `cpus_per_task` / `memory` / `time_limit` / `gpus` / `environment` | — | — | `hpc.slurm.submit`과 동일 | 의미와 상한이 submit과 완전히 같음 |

profile별 실제 명령: `julia --project=. <script>`, `python <script>`, `bash <script>`, `moose` → `mpirun -np <ntasks> -- <script>`.

### 작업 조회와 관리(이 인스턴스가 제출한 작업만)

서버는 **자기가 제출하고 등록한 job ID만** 조회하며(`squeue -j <ids>` / `sacct -j <ids>`), 공용 계정의 큐 전체를 스캔하지 않습니다. 다른 작업의 조회/취소는一律 거부됩니다.

| 도구 | 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|---|
| `hpc.slurm.status`(readOnly,idempotent) | `job_id` | string | ✅ | — | 상태. squeue에 기록이 없으면 `sacct`로 폴백 |
| `hpc.slurm.queue`(readOnly,idempotent) | — | — | — | — | 이 인스턴스의 활성 작업 목록(활성 작업이 없을 때의 빈 목록은 정상) |
| `hpc.slurm.output`(readOnly,idempotent) | `job_id` | string | ✅ | — | 작업 로그 읽기 |
| | `stream` | string | — | `"stdout"` | 열거: `stdout`, `stderr` |
| | `tail_bytes` | int | — | `shell.max_output_bytes`(1 MiB) | 파일 끝에서 몇 바이트를 가져올지 |
| `hpc.slurm.cancel`(destructive) | `job_id` | string | ✅ | — | 취소(이 인스턴스가 제출한 작업만) |
| `hpc.slurm.accounting`(readOnly,idempotent) | `job_id` | string | ✅ | — | `sacct` 회계: `elapsed`, `cpu_time_raw`, `max_rss`, `state`, `exit_code`, `node_list`, `alloc_cpus` |

### `hpc.jobs.wait` — `readOnly, idempotent`

작업이 종료 상태(`COMPLETED`/`FAILED`/`CANCELLED`/`TIMEOUT`/`OUT_OF_MEMORY`/`NODE_FAIL`/`PREEMPTED`)에 이를 때까지 폴링합니다.

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | 작업 ID |
| `timeout_seconds` | int | — | `wait_max_seconds`(3600) | 전체 대기 상한. 서버 상한으로도 클램프 |
| `poll_interval` | int | — | `10` | 폴링 간격(초, 서버에서 2–60으로 클램프) |

> 타임아웃이어도 작업을 취소하지 않습니다. 오류를 반환하고 폴링 지속을 안내합니다.

### `hpc.jobs.diagnose` — `readOnly`

한 번의 호출로 진단을 끝냅니다: 상태 + 회계 + stdout/stderr 꼬리 + 흔한 오류 시그니처 스캔.

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | 작업 ID |
| `stdout_lines` | int | — | `80` | stdout 꼬리 줄 수(상한 200, 각 줄은 4096바이트로 읽기량 추정) |
| `stderr_lines` | int | — | `80` | stderr 꼬리 줄 수(상한 200) |
| `include_accounting` | bool | — | `true` | `sacct` 회계 포함 여부 |
| `include_error_scan` | bool | — | `true` | 오류 시그니처(`oom`/`segfault`/`timeout`/`gpu_error`/`mpi_error`/`missing_file`) 스캔 여부 |

> `diagnose` 한 번이 `status → output → accounting → read`의 여러 왕복을 대체합니다.

### `hpc.jobs.wait_and_diagnose` — `readOnly`

작업 종료를 기다린 뒤 한 번에 진단합니다(이미 끝났으면 즉시 반환).

인자: `job_id`(필수), `timeout_seconds`, `poll_interval`, `stdout_lines`, `stderr_lines`(의미와 기본값은 `wait` / `diagnose`와 동일).

---

## 5. 클러스터 토폴로지와 병렬 파라미터

### `hpc.cluster.topo` — `readOnly, idempotent, openWorld`

**계산 노드의 실제 하드웨어 파라미터**와 도출된 병렬 최적화 권장을 가져옵니다: CPU 모델/vendor, `sockets × cores × threads`, SIMD 명령 집합(AVX2/AVX-512/SVE), NUMA 도메인과 거리 행렬, 캐시 계층, 노드 메모리, 파티션 features/GRES, 그리고 `recommended_parallel_parameters`(순수 MPI / MPI+OpenMP 하이브리드 / 순수 OpenMP 세 가지. `ntasks_per_node`, `cpus_per_task`, `--hint=nomultithread`, `--cpu-bind=cores`, `-map-by numa`, `OMP_NUM_THREADS`, 권장 `--mem`과 근거 포함).

| 인자 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `partition` | string | — | 허용 목록 첫 번째 | 탐색할 파티션. 이름은 `[A-Za-z0-9_.-]`이고 `slurm.allowed_partitions`에 맞아야 함 |
| `refresh` | bool | — | `false` | 강제 재수집(캐시 무시. 큐에 남은 이전 수집 작업은 먼저 취소) |

**호출 비용과 캐시**(기본값은 `topology.*` 설정):

- 첫 호출(또는 캐시 만료, `refresh: true`)은 **1 CPU 수집 작업**을 제출합니다
  (기본 시간 상한 `00:03:00`). 계산 노드에서 `lscpu` / `/proc/cpuinfo` /
  `numactl --hardware` / `/proc/meminfo` / `/sys/.../cpu0/cache`를 읽고,
  로그인 노드 `sinfo`의 파티션 뷰와 병합합니다.
- 결과는 `topology.cache_ttl_seconds`(기본 24 h) 동안 캐시됩니다: 프로세스 내부와
  원격 `$HPC_MCP_ROOT/.hpc-mcp/topo/topology_<partition>.json`. 캐시 적중 시 작업을
  **제출하지 않습니다**. 반환의 `source`가 출처를 알려 줍니다(`probe-job` / `session-cache` / `remote-cache`).
- 수집 작업이 아직 큐에 있으면(`topology.wait_seconds`, 기본 300 s 초과) `status: "pending"`과
  `collection_job_id`를 반환합니다. **다시 호출하면 그 작업이 재사용되며** 중복 제출되지 않습니다.

**안전 제약**: 수집 스크립트는 **서버 생성 고정 내용**이며 agent 인자가 기록되지 않습니다. 스크립트는 노드 로컬 하드웨어 정보만 읽고 `squeue`/`sacct`/`scontrol`을 **호출하지 않습니다**(공용 계정 격리). 작업 소유권, 파티션 허용 목록, 동시 실행 상한은 그대로 적용됩니다. 관리자는 `topology.enabled: false`로 이 도구를 비활성화할 수 있습니다.

**반환 요점**:

| 필드 | 의미 |
|---|---|
| `cpu.model_name` / `cpu.vendor` / `cpu.architecture` | CPU 모델, 벤더, 아키텍처 |
| `cpu.sockets` / `cores_per_socket` / `threads_per_core` | 토폴로지 세 요소 |
| `cpu.physical_cores` / `logical_cpus` | 물리 코어 수 / 논리 CPU 수(SMT 활성 시 후자가 더 큼) |
| `cpu.simd.level` / `present` | 벡터 명령 집합 등급(`avx512`/`avx2`/`avx`/`arm-neon`…)과 발견된 플래그 |
| `cpu.cache_kib` / `cache_source` | L1d/L1i/L2/L3(KiB. `null`은 해당 계층 미상)과 출처(`lscpu`/`sysfs`) |
| `numa.count` / `nodes` / `distances` | NUMA 도메인 수, 도메인별 CPU 목록과 크기, 거리 행렬 |
| `memory_mib.mem_total` | 노드 메모리(MiB) |
| `slurm.partition` / `node_variants` | 파티션 요약(노드 수/노드당 코어와 메모리/features/GRES)과 하드웨어 변종(상태 히스토그램 포함) |
| `recommended_parallel_parameters` | 세 가지 병렬 파라미터 제안 + `notes`(각 제안의 근거) |
| `collected_on_node` / `collection_job_id` | 샘플을 얻은 노드와 수집 작업 ID(추적용) |

> 제안값은 물리 코어/NUMA 도메인에서 도출한 **휴리스틱** 출발점이며 벤치마크 결론이 아닙니다. 실제 튜닝에는 서로 다른 rank/스레드 조합을 비교하는 실행이 필요합니다.

---

## 6. 전형적인 호출 순서

```text
1. hpc.info                      # 파티션 허용 목록과 리소스 상한
2. hpc.project.snapshot          # 프로젝트 컨텍스트(트리/git/내 작업)
3. hpc.cluster.topo              # 병렬 튜닝 전: CPU/SIMD/NUMA + 파라미터 제안
4. hpc.files.search  →  hpc.files.read   # 먼저 위치를 찾고, 다음에 작게 읽기
5. hpc.files.write                # 원격 편집
6. hpc.slurm.submit 또는 hpc.job.run     # 빌드/테스트/계산
7. hpc.jobs.wait_and_diagnose     # 대기 + 한 번의 진단
8. 분석 → 5단계로 복귀
```
