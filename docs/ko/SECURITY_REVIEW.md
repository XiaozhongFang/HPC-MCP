# 보안 검토(2026-09-04)

다른 언어: [English](../SECURITY_REVIEW.md) | [简体中文](../zh-CN/SECURITY_REVIEW.md) | [日本語](../ja/SECURITY_REVIEW.md) | [繁體中文](../zh-TW/SECURITY_REVIEW.md)

## 발견 사항과 수정

| 심각도 | 영역 | 발견 사항 | 수정 |
| --- | --- | --- | --- |
| Critical | 파일 전송 | 업로드/다운로드가 심볼릭 링크 대상과 자격 증명 파일을 포함한 임의의 로컬 경로를 허용했다. | `local_root`, 구성 요소 검사, 자격 증명 파일 이름 거부 목록, 배치 전송 시 제어 문자 거부, 다운로드 후 크기 상한. |
| Critical | Safe shell | 어휘상 root 안에 있는 심볼릭 링크로 `cat`/`grep`이 원격 샌드박스 밖을 읽을 수 있었다. 실행 파일 경로와 follow 계열 옵션이 명령 표면을 더 넓혔다. | 피연산자에 대한 원격 canonical 검사, 실행 파일은 basename만 허용하는 정책, `-L/-H/-follow` 거부. |
| High | 작업 추적 | 저장된 `job_dir`와 세션 간 항목이 신뢰되어 다른 세션 작업의 output/accounting/cancel이 가능했다. | 세션에 묶인 스키마 검증과 숫자 job ID에서 도출한 경로. canonical 메타데이터 디렉터리와 잠금 기반 원자적 쓰기. |
| High | 리소스 고갈 | SSH/SFTP가 무한 `communicate()` 버퍼링을 사용했고 과대한 명령/환경 변수 페이로드도 받아들였다. | 스트리밍 바이트 상한, 자식 프로세스 회수, 유계 argv/environment/스크립트 크기, 검증된 타임아웃. |
| High | 설정 | 주입된 `environ`이 무시되었고, 잘못된 중첩 YAML과 음수 상한이 런타임 오류나 잘못된 정책 상태로 새어 나왔다. | 결정적 환경 출처, 타입이 있는 섹션/상한 검증, canonical root, 안전한 호스트 키 모드만 허용. |
| Medium | 감사 | 정규식만 쓰는 마스킹이 사전 키에 있는 비밀(`{"token": "..."}` 등)과 제어 문자를 놓쳤다. | 재귀적이고 키 이름을 인식하는 마스킹, 제어 문자 이스케이프, 절단. |
| Medium | 동시성 | 동시 submit 호출이 활성 작업 검사를 동시에 통과할 수 있었다. | 비동기 제출 잠금과 원자적 메타데이터 잠금 디렉터리. |
| Low | 유지보수성 | 사용되지 않는 헬퍼와 import가 보안 경계를 흐렸다. | 죽은 코드를 제거하고 policy/service/transport의 책임을 문서화. |

## 검증

회귀 테스트는 경로 횡단, 심볼릭 링크 탈출, 명령 주입, login/compute 분리, Slurm 상한, 작업 소유권, 설정, 파일 서비스를 포괄합니다. 실행:

```bash
python -m compileall -q src
python -m pytest -q
git diff --check
```

정적 검사는 가능하면 Bandit/Ruff를 추가하고, 배포 환경에서 `python -m pip check`도 실행하세요.

## 운영 요구 사항

가능하면 `StrictHostKeyChecking=yes`를 사용하고 `known_hosts`를 미리 채워 두세요.
`HPC_MCP_ROOT`는 대상 사용자만 다루도록 하고, `HPC_MCP_LOCAL_ROOT`는 전송에 필요한 최소한의
로컬 프로젝트 디렉터리로 설정합니다. 새 프로세스 세션은 더 오래된 세션이 등록한 작업을 관리할 수
없습니다. 이는 애플리케이션 수준의 경계입니다. 동등한 Unix 계정은 여전히 JSON 추적 파일을 변조하거나
원격 경로 경쟁을 일으킬 수 있으므로, 동일 계정의 적대적 배포에는 독립 Unix 계정이나 권한 있는
원격 헬퍼가 필요합니다.

---

# 보안 + 효율 검토(v0.2, 2026-09)

## 범위

이번 라운드는 기존 경계를 전혀 약화하지 않으면서 서버를 "agent의 일탈을 막는" 단계에서
"agent가 최소한의 원격 I/O로 최대의 가치를 얻도록 돕는" 단계로 끌어올렸습니다.
아래 발견 사항은 모두 이번에 수정되었고 회귀 테스트로 커버됩니다.

## 발견 사항과 수정

| 심각도 | 영역 | 발견 사항 | 수정 |
| --- | --- | --- | --- |
| Critical | 공용 계정 Slurm | `_queue_states()` / `queue()`가 계정 전체 `squeue`를 실행하고 Python에서 걸러내어 다른 사용자의 작업 메타데이터가 MCP 프로세스에 들어왔다. | 모든 소유권 검사가 `squeue -j <tracked ids>`만 실행하도록 바뀌었습니다(500건 단위 배치). 추적 중인 작업이 없으면 `squeue`를 전혀 실행하지 않습니다. 새 `tests/security/test_shared_account_isolation.py`가 타인의 행이 결과에 도달하지 않고 `-j` 없는 조회가 결코 발행되지 않음을 증명합니다. |
| High | 파일 읽기 | 도구 설명이 로그 전체를 `offset=0`에서 EOF까지 페이지하도록 유도했고, 각 `read`가 stat+dd+base64 왕복이었다. | `hpc.files.read`는 이제 `files.max_read_slice_bytes`(기본 256 KiB)로 상한이 정해진 bounded slice이며, 설명은 먼저 `hpc.files.search`를 쓰도록 유도합니다. |
| High | 디렉터리 나열 | 재귀 `find`가 트리 전체를 스트리밍하고 Python이 사후에 잘랐다. | `hpc.files.list`는 원격 호출 한 번에 한 계층을 열거하고, `head`로 원격 출력을 자르며(`bash -o pipefail`로 SIGPIPE 감지), `depth:N` 커서로 이어서 진행하고, `max_depth`(기본 3)를 클램프합니다. |
| High | 로그 진단 | Agent가 `status -> output -> accounting -> read` 시퀀스를 조립해야 했다(도구 스래싱). | 새 `hpc.jobs.diagnose`(상태 + 회계 + 유계 꼬리 + 오류 시그니처)와 `hpc.jobs.wait_and_diagnose`. 모든 내부 쿼리는 자기 소유 작업으로 한정되고 상한이 있습니다. |
| High | 검색 | 일급의 유계 검색이 없어 agent가 `grep` 파이프라인을 즉석에서 만들었다. | 새 `hpc.files.search`: 단일 파일은 stat 후 초과 시 거부, 트리는 `grep -rn` + 제외 디렉터리 + `-m` + `head` + `timeout`. 모든 예산은 서버에서 클램프되고, 패턴은 인용된 argv로 전달됩니다(주입 없음). |
| Medium | 쿼리 중복 제거 | 반복되는 동일 status/list 호출이 매번 SSH를 다시 실행했다. | `QueryCache`(TTL `cache_ttl_seconds`, 기본 2 s, 0이면 비활성)가 멱등적 읽기 전용 도구를 정규화된 tool+args로 중복 제거합니다. 쓰기 작업은 캐시 전체를 무효화합니다. |
| Medium | 명령 비용 | 정당한 명령(`find`/`du`/`sort`/`git grep`)이 로그인 노드를 포화시킬 수 있었다. | `security/command_cost.py`가 모든 safe 명령을 계층화(LOW 15s/256KiB, MEDIUM 30s/512KiB, HIGH 10s/512KiB)하고 `SafeExec`에서 강제하며 `cost_tier`/`timeout_seconds`를 반환합니다. |
| Medium | 응답 누출 | 감사 마스킹이 *agent로 가는 응답*을 보호하지 않았다(예: 작업 stderr의 `password=`). | `response_redactor.py`가 `files.read/search`, `slurm.output`, `shell.run_safe`, `diagnose`, `snapshot`에서 명백한 비밀(`password=`/`token=`/`api_key`/`Bearer`/AWS/PEM 블록)을 마스킹합니다. 패턴은 최소로 유지해 과학 로그 내용을 보존합니다. |
| Medium | 정보 노출 | `hpc.info`가 원시 `local_roots` 경로(사용자 이름/프로젝트 이름이 포함될 수 있음)를 반환했다. | `workspace_available`/`transfer_enabled` 불리언으로 교체. 회귀 테스트가 로컬 사용자 이름이 결코 나타나지 않음을 단언합니다. |
| Medium | 쓰기 경쟁 | 다른 프로세스가 바꾼 파일을 덮어쓰면 작업이 조용히 망가질 수 있었다. | `hpc.files.write`가 `expected_size`/`expected_mtime`/`expected_sha256`을 받습니다. 불일치(또는 파일 없음)는 fail-closed로 쓰기를 거부합니다. |
| Low | 고수준 실행 | 전문가와 agent가 하나의 원시 argv 제출 표면을 공유했다. | 새 `hpc.job.run`이 고정 runtime profile(julia/python/moose/bash)로 argv를 만들고 제출 정책을 그대로 재사용합니다. `hpc.slurm.submit`은 전문가용으로 남습니다. |

## 새 보안 회귀 스위트(이번 라운드)

- `test_shared_account_isolation.py` —— 계정 전체 `squeue` 없음, 타인의 작업이 결과에 도달하지
  않음, 추적 대상이 없으면 스캔하지 않음, 타인의 cancel/accounting/output 거부.
- `test_files_search.py` —— 검색 예산, 샌드박스 탈출, 제어 문자와 주입 거부,
  `-m`/`head` 상한, 컨텍스트 파싱.
- `test_command_cost.py` —— 계층 분류, git 하위 명령 세분화, `SafeExec`에서 강제되는
  타임아웃/출력 클램프.
- `test_response_redaction.py` —— password/token/API key/Bearer/AWS/PEM 마스킹,
  일반 로그 내용 보존, 유계 재귀.
- `test_info_minimal_exposure.py` —— `hpc.info`가 로컬 root와 SSH 세부 정보를 숨김.
- `test_cache.py` —— 중복 제거 키, TTL 만료, 크기 상한, 쓰기에 의한 무효화.
- `test_project_snapshot.py` —— 유계 트리, git 요약, 자기 소유 작업만.
- `test_files_service.py` 추가분 —— 읽기 슬라이스 예산, list 페이징/커서, 쓰기 동시성 보호.
- `test_slurm_manager.py` 추가분 —— diagnose 집계와 상한, wait_and_diagnose,
  job.run 정책 재사용.

## 잔여 위험(변경 없음, 3계층으로 명시)

Layer 1(MCP policy)과 Layer 2(Slurm 리소스 정책)는 *잘못 동작하는 agent*를 무해하게 만들지만,
공용 Unix UID로 계산 노드에서 실행되는 *악성 코드*를 격리할 수는 없습니다. `hpc.slurm.submit`이
부여하는 것은 바로 그 UID의 권한입니다. 그 위협 모델에는 Layer 3(독립 Unix UID, Slurm 작업 격리 +
filesystem ACL, 컨테이너/샌드박스, 또는 권한 있는 원격 헬퍼)이 필요합니다. "명백히 악의적인"
프로그램을 정규식/정책으로 거르는 것은 OS 수준 격리 기구로 명시적으로 취급하지 않습니다. 참조:
`README.md` / `SECURITY.md` / `docs/ARCHITECTURE.md`.
