# Security Policy

HPC-MCP의 보안 모델: **모든 핵심 경계는 MCP 서버의 결정적 코드로 강제되며**, Skill, 시스템 프롬프트, 도구 설명의 정확성에 의존하지 않습니다.

구현 경계와 요청 흐름은 [`docs/ko/ARCHITECTURE.md`](docs/ko/ARCHITECTURE.md), 이번 검토 기록은 [`docs/ko/SECURITY_REVIEW.md`](docs/ko/SECURITY_REVIEW.md)를 참조하세요.

다른 언어: [English](SECURITY.md) | [简体中文](SECURITY.zh-CN.md) | [日本語](SECURITY.ja.md) | [繁體中文](SECURITY.zh-TW.md)

## 위협 모델

- HPC는 **공용 계정**을 사용합니다(하나의 SSH 계정을 여러 사용자가 공유).
- Agent(LLM)는 잘못된, 모호한, 또는 주입된 지시를 받을 수 있습니다.
- 목표: agent가 잘못 동작하더라도 권한을 넘지 못하게 하는 것.

## 강제되는 경계

### 1. 경로 샌드박스

- 모든 원격 경로는 먼저 어휘적으로 정규화됩니다(`posixpath.normpath`. `..`, 물결표, NUL/CR/LF, 상대 경로는 거부).
- 다음으로 **이미 존재하는 경로** 또는 **가장 가까운 존재하는 부모**에 대해 원격 `realpath`로 canonical화하여 심볼릭 링크 탈출을 차단합니다.
- 어느 단계에서든 불확실하면 거부합니다.
- 원격 root는 전용 디렉터리여야 하며 `/`로 설정할 수 없습니다. 로컬 전송에는 별도의 `local_root` 샌드박스(기본은 시작 디렉터리)가 있어 심볼릭 링크, `.ssh`, 일반적인 개인 키 파일 이름을 거부합니다.

**허용되지 않음**: `$HPC_MCP_ROOT` 밖의 모든 경로(다른 공용 계정 사용자의 디렉터리, `/etc`, `/tmp`, 시스템 디렉터리 포함), 심볼릭 링크를 통한 탈출, root 밖으로의 rename/copy, root 자체 삭제.

### 2. 로그인 노드 명령 정책

- 허용 목록의 경량 명령만 실행할 수 있습니다(`ls`, `cat`, `grep`, `git status/diff/log`, `module list` 등. 설정으로 확장 가능). `squeue`, `sacct`, `scontrol`은 `hpc.shell.run_safe`에서 호출할 수 없습니다. 작업 소유권 검사를 우회하는 것을 막기 위해서입니다.
- 모든 셸 메타 문자를 거부합니다: `;` `&&` `||` `|` `>` `>>` `<` `$( )` `` ` `` `${ }` `&`, 줄바꿈 등.
- 계산/빌드 프로그램을 거부합니다: `julia`, `python`, `make`, `cmake`, `ninja`, `mpirun`, `srun`, 컴파일러, `pytest`, `matlab`, 컨테이너 런타임 등.
- 위험한 프로그램을 거부합니다: `sudo`, `ssh`/`scp`/`rsync`, `curl`/`wget`, `nohup`/`setsid`/`tmux`, `kill`, `chmod`, `dd`, 중첩 셸, `xargs`, `eval` 등.
- `git`은 읽기 전용 하위 명령으로 제한(`commit/push/pull/clone/-c/--exec-path/--git-dir` 거부), `module`은 조회만, `find`는 `-exec`/`-delete` 거부.
- 로컬과 원격 모두 `shell=True`를 쓰지 않으며 argv는 `shlex.join`으로 다시 직렬화됩니다.
- 파싱에 실패하면 거부합니다.
- `cat`/`grep`/`find` 등의 경로 피연산자는 원격 canonical 검사를 거칩니다. `find -L/-H/-follow`, `ls -L`, `du -L`은 거부합니다. `env`/`printenv`는 비운 최소 환경에서만 실행됩니다.

### 3. Slurm 리소스 정책

- 파티션 허용 목록(기본은 비어 있음 = 모두 거부).
- 노드/CPU/메모리/GPU/시간/동시 실행 상한. 초과하면 제출을 거부합니다.
- 작업 디렉터리는 root 안이어야 합니다. 명령 argv에 제어 문자를 넣을 수 없습니다.
- 작업 출력은 항상 `$ROOT/.hpc-mcp/jobs/<id>/` 아래에 기록되며 벗어날 수 없습니다.

### 4. 작업 소유권 격리

- 현재 서비스 세션이 제출하고 등록한 job만 관리할 수 있습니다(tracked_jobs.json의 `tool_session`이 일치해야 함). 재시작 후 이전 세션의 작업은 기본적으로 새 인스턴스가 승계하지 않습니다.
- **조회 격리**: `hpc.slurm.queue/status`는 이 인스턴스가 추적하는 job ID에 대해서만 `squeue -j <ids>`를 실행합니다. 추적 중인 작업이 없으면 즉시 빈 결과를 반환하고, 계정 전체 `squeue`/`sacct`를 **결코** 발행하지 않습니다. 공용 계정에서 다른 사용자의 작업 메타데이터가 이 프로세스에 들어오지 않습니다.
- 다른 사용자나 다른 인스턴스의 작업을 조회하거나 취소할 수 없습니다.
- 이 등록 파일은 애플리케이션 수준 격리이며, 동일 Unix UID에서의 강제 접근 제어가 아닙니다. 적대적인 공용 계정에는 독립 UID나 권한 있는 원격 헬퍼가 필요합니다.

### 5. 쿼리 비용 경계(진단 도구와 예산)

- `hpc.files.read`는 **bounded slice**입니다: 한 번의 읽기가 `min(max_bytes, files.max_read_slice_bytes)`(기본 256 KiB) 이하입니다. 도구 설명은 agent를 offset=0에서 EOF까지 유도하지 않습니다(큰 파일 조사는 `hpc.files.search`로 위치를 찾습니다).
- `hpc.files.search`의 모든 예산(`max_matches`/`max_context_lines`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`)은 서버에서 클램프됩니다. 단일 파일은 먼저 `stat`하여 예산 초과면 거부합니다. 원격에서는 `grep -m` + `head` 절단 + `timeout` 보험이 적용됩니다. 패턴은 shlex로 인용되고 제어 문자는 거부되어 주입을 배제합니다.
- `hpc.files.list`의 재귀 나열은 한 계층씩 페이징하고(`page_size` + `depth:N` 커서), 원격 출력을 `head`로 자르며, `bash -o pipefail`로 SIGPIPE를 감지해 절단을 정확히 판정합니다. 트리 전체를 스캔한 뒤 버리는 일은 결코 없습니다.
- `hpc.jobs.diagnose` / `hpc.project.snapshot`의 모든 내부 쿼리는 유계이며 예산이 있습니다.
- 멱등적 읽기 전용 쿼리는 `tool + 정규화된 인자`로 중복 제거됩니다(`cache_ttl_seconds`, 기본 2s, 0이면 비활성). 쓰기 작업은 캐시를 무효화합니다.
- `hpc.info`는 로컬 경로 root(사용자 이름/프로젝트 이름이 포함될 수 있음)를 반환하지 않고 능력 불리언과 리소스 상한만 반환합니다.
- `hpc.shell.run_safe`의 모든 정당한 명령은 **command cost policy**로 계층적으로 클램프됩니다: `ls`/`head`/`pwd` 등은 LOW(15s/256KiB), `grep`/`cat`/`git diff` 등은 MEDIUM(30s/512KiB), `find`/`du`/`sort`/`git grep` 등은 HIGH(10s/512KiB). timeout과 output은 서버에서 강제되며 agent의 자제에 의존하지 않습니다.
- `hpc.files.write`는 **낙관적 동시성 보호**를 지원합니다: `expected_size`/`expected_mtime`/`expected_sha256` 중 하나라도 현재 파일 상태와 맞지 않으면 덮어쓰기를 거부합니다(fail-closed). 공용 계정에서 동료가 방금 수정한 코드를 덮어쓰는 것을 방지합니다.

### 6. SSH 경계

- 설정된 단일 호스트에만 연결할 수 있습니다. BatchMode, StrictHostKeyChecking, 연결 타임아웃이 적용됩니다.
- 개인 키 내용을 **읽지도 출력하지도 않습니다**. `~/.ssh/config`로 관리하는 것을 권장합니다.
- SSH/SFTP 출력은 스트리밍 바이트 상한을 씁니다. 타임아웃 시 자식 프로세스를 kill하고 회수합니다. ControlMaster는 서비스 종료 시 닫힙니다.
- `hpc.ssh(command=...)` 같은 임의 명령 도구는 제공하지 않습니다.
- 포트 포워딩 없음, ProxyJump 없음, 다중 호스트 없음.

### 7. 자격 증명과 로그

- 개인 키, 비밀번호, 토큰은 결코 로그에 기록하지 않습니다. 감사 로그는 민감 패턴을 마스킹하고 값을 절단합니다.
- 감사 필드: timestamp, tool, args(마스킹됨), decision(ALLOW/DENY), reason, job_id, duration.
- 마스킹은 필드 이름을 기준으로 재귀적으로 수행하고(password/token/secret/private-key 등) 제어 문자를 제거한 뒤 절단합니다. 예상치 못한 예외가 그대로 agent에 반환되지 않습니다.

## 3계층 방어와 잔여 위험

| 계층 | 기구 | 방어 대상 |
|---|---|---|
| Layer 1: MCP policy | 경로 샌드박스, 명령 허용 목록, Slurm 리소스 정책, 작업 소유권, 쿼리 예산, 감사 | 잘못 동작하는 agent |
| Layer 2: Slurm | 파티션 허용 목록, 리소스 상한, 동시 실행 상한, 단일 `sbatch` 진입점 | 계산 자원 남용 |
| Layer 3: OS/클러스터 | 독립 Unix UID / Slurm 작업 격리 + filesystem ACL / 컨테이너 샌드박스 / 권한 있는 원격 헬퍼 | **악성 코드 격리**(선택) |

**잔여 위험**: 공용 Unix UID 아래에서 MCP는 *agent가 일탈하지 않음*(Layer 1/2)만 보장할 수 있습니다. 계산 노드에 제출된 악성 코드가 그 UID가 읽을 수 있는 데이터에 접근하지 않는다고 **보장할 수 없습니다**(Layer 3). `hpc.slurm.submit`은 본질적으로 "그 Unix UID로 프로그램을 실행하는 것"입니다. Python 정규식이나 정책으로 "악성 코드를 걸러내려" 하지 마세요. 그것은 거짓 안심을 만듭니다. 진정한 악성 코드 격리는 Layer 3의 OS 수준 기구에 의존해야 합니다. 이 점은 README / QUICKSTART에서 사용자에게 명시해야 합니다.

## 명시적으로 구현하지 않는 것(v1)

임의 원격 셸, 임의 SSH 호스트, sudo, 원격 포트 포워딩, 작업 마이그레이션, 다중 호스트 SSH, HPC 상주 데몬, 원격 HTTP MCP, 자동 계정 전환, 자동 자격 증명 관리, `~/.ssh/config` / `authorized_keys` 변경.

## 실패 시 안전(fail-closed)

설정 불완전, SSH 이상, 경로 해석 불가, 명령 파싱 실패, Slurm 파라미터 파싱 불가, 파티션/호스트 불확실 — 모두 **DENY**이며, 무제한 셀로 되돌아가는 일은 없습니다.

## 보안 문제 보고

저장소 Issue를 통해 비공개로 보고하거나 메인테이너에게 연락하세요. 수정되지 않은 세부 사항을 공개 채널에 공개하지 마세요.
