# 빠른 시작 및 문제 해결 (단계별 안내)

다른 언어: [English](../QUICKSTART.md) | [简体中文](../zh-CN/QUICKSTART.md) | [日本語](../ja/QUICKSTART.md) | [繁體中文](../zh-TW/QUICKSTART.md)

이 문서는 hpc-mcp를 처음부터 끝까지 동작시키는 과정과 각 파라미터의 설명, 그리고 흔히 겪는 "멈춘 것처럼 보이는" 현상의 원인을 정리한 것입니다.

---

## 0. 1분 만에 이해하는 전체 그림

`hpc-mcp`는 **MCP 서버(stdio)** 이며, 일회성 명령이 아닙니다.

- `hpc-mcp ...`로 실행하면 `starting: ...` 한 줄을 출력한 뒤 **입력을 기다리며 멈춰 있습니다**.
  **이것은 정상입니다.** MCP 클라이언트(Codex / Reasonix)가 표준 입력으로 요청을 보내기를 기다리는 중입니다.
  이 시점에는 **아직 SSH로 연결하지 않았으므로**, 시작 로그만 보고 SSH가 되는지 판단할 수 없습니다.
- 실제 SSH 연결은 클라이언트가 처음으로 도구(예: `hpc.info`)를 호출할 때 일어납니다.
- **설정과 네트워크가 지금 통하는지 확인하려면** `--check`를 사용하세요(3단계). 능동적으로 SSH를 한 번 연결하고 결과를 출력합니다.

---

## 1. 설치

**전용 가상 환경에 설치하는 것을 강력히 권장합니다.** `pip install --user`로 시스템 `~/.local`에 바로 넣으면 이미 설치된 패키지(torch, httpcore 등)와 충돌해 `UNKNOWN-0.0.0` 껍데기 패키지나 `pip's dependency resolver does not take into account...` 경고가 나타납니다.

### 방법 A(권장): virtualenv 격리 환경

시스템에 `python3-venv`가 없으면 virtualenv를 사용합니다(사용자 권한만으로 가능, root 불필요).

```bash
# 1. virtualenv 설치(한 번만)
python3 -m pip install --user virtualenv

# 2. 프로젝트 전용 환경 생성(한 번만, 이후 계속 사용)
python3 -m virtualenv ~/venvs/hpc-mcp

# 3. 활성화 후 hpc-mcp 설치
source ~/venvs/hpc-mcp/bin/activate
cd ~/git_repo/HPC-MCP
pip install .

# 4. 사용
hpc-mcp --version        # hpc-mcp 0.1.0이 출력되어야 함
hpc-mcp --help
```

> 시스템에 `python3-venv`가 있다면 표준 라이브러리 방식인
> `python3 -m venv ~/venvs/hpc-mcp`를 써도 나머지 단계는 동일합니다.

설치 후 `hpc-mcp`를 찾지 못하면 환경이 활성화되어 있는지 확인하거나 전체 경로로 실행하세요.

```bash
~/venvs/hpc-mcp/bin/hpc-mcp --version
```

Codex / Reasonix에 등록할 때는 그 전체 경로를 명령으로 지정합니다.

```bash
codex mcp add hpc ... -- ~/venvs/hpc-mcp/bin/hpc-mcp
```

### 방법 B: conda

이미 conda를 쓰고 있다면:

```bash
conda create -n hpc-mcp python=3.10
conda activate hpc-mcp
cd ~/git_repo/HPC-MCP
pip install .
```

### 방법 C: ~/.local에 직접 설치(비권장, 임시 용도)

```bash
cd ~/git_repo/HPC-MCP
python3 -m pip install --user .
```

> `UNKNOWN-0.0.0`이나 의존성 충돌 경고가 나오면 pip/setuptools가 오래되었거나
> `~/.local`이 이미 어지러운 상태입니다. 방법 A로 전환하세요.

### 문제가 생기면

- `pip install .`에서 `UNKNOWN-0.0.0`이 나옴 → pip/setuptools가 오래되었고 환경이 격리되지 않음.
- `ERROR: pip's dependency resolver does not currently take into account...` 발생
  → 시스템 `~/.local`에 다른 패키지(torch/httpcore 등)가 섞여 있음. **hpc-mcp 동작에는 영향이 없지만**, 해당 환경이 혼합 상태라는 뜻이므로 가상 환경으로 옮기는 것을 권장합니다.

### ⚠️ 설치 후 첫 단계: 비밀번호 없는 SSH 확인

**hpc-mcp는 비밀번호 없는 로그인을 강제합니다(`BatchMode=yes`, 비밀번호 입력 불가).** 설정하지 않으면 서버가 시작되자마자 모든 도구 호출이 `Permission denied`로 실패하며, **비밀번호 프롬프트는 절대 나타나지 않습니다**. 따라서 패키지를 설치하고 클러스터에 연결하기 전에 **반드시 키 인증이 되는지 직접 확인**하세요.

```bash
# 핵심: -o BatchMode=yes로 hpc-mcp와 동일한 연결 방식을 재현합니다.
# 비밀번호를 묻지 않고 OK가 나오면 키 인증이 준비된 것입니다.
ssh -o BatchMode=yes -o ConnectTimeout=10 username@192.168.12.12 "echo OK"
```

- `OK`가 출력됨 → 키 인증 완료. 3단계로 진행하세요.
- `Permission denied (publickey...)` → **아직 키 인증이 없음**. 먼저 설정합니다:
  ```bash
  ssh-copy-id username@192.168.12.12   # 한 번만 비밀번호를 묻고, 이후에는 묻지 않음
  ```
  그런 다음 위의 BatchMode 확인 명령을 다시 실행하세요.
- `Connection timed out` → 네트워크가 통하지 않음. VPN 연결 또는 점프 호스트 설정을 먼저 하세요(2단계).

> `~/.ssh/config`로 연결을 관리한다면 명령의 호스트를 `Host` 별칭으로 바꾸세요:
> `ssh -o BatchMode=yes my-hpc "echo OK"`

---

## 2. SSH 준비: 먼저 "일반 ssh가 비밀번호 없이 통하는" 상태 만들기

hpc-mcp는 내부적으로 시스템 OpenSSH를 사용하므로, **모든 ssh 문제는 터미널에서 먼저 재현하세요**. 수동으로 테스트합니다.

```bash
ssh username@192.168.12.12 "echo OK && hostname"
```

세 가지 결과:

| 결과 | 의미 | 조치 |
|---|---|---|
| `OK`와 호스트명 출력 | 키 인증과 네트워크 모두 정상 | 3단계로 진행 |
| `password:`에서 멈춤 | 키 인증 없음 | 키 설정: `ssh-copy-id username@192.168.12.12` |
| `Connection timed out` / 100% 패킷 손실 | **네트워크가 통하지 않음** | 아래 "네트워크 불통" 참조 |

### 네트워크 불통(가장 흔한 경우)

`192.168.12.12`는 **사설망 주소**입니다. 캠퍼스망이나 VPN에 연결되어 있지 않으면 외부에서는 도달할 수 없습니다.

- 평소 어떻게 접속하는지 먼저 확인하세요. VPN이 먼저 필요한가요? 점프 호스트가 필요한가요?
- VPN이 필요하면 연결 후 다시 테스트합니다.
- 점프 호스트가 필요하면 4단계의 `~/.ssh/config`(`ProxyJump`) 설정을 참고하세요.

### 강력 권장: `~/.ssh/config`로 연결 관리

연결 세부 정보를 config에 적어 두면 hpc-mcp의 `--host`는 별칭만 참조하면 되어 가장 편합니다.

```sshconfig
# ~/.ssh/config
Host 192.168.12.12
    HostName 192.168.12.12
    User username
    IdentityFile ~/.ssh/id_ed25519
    # 점프 호스트가 필요하면 다음 줄을 활성화(jump-host를 실제 값으로 교체):
    # ProxyJump jump-host
```

설정 후 터미널에서 `ssh 192.168.12.12 "echo OK"`가 통하면 hpc-mcp에서는 `--host 192.168.12.12`를 사용합니다.

---

## 3. `--check`로 검증하기(중요! 건너뛰지 마세요)

`--check`는 **능동적으로 SSH를 한 번 연결**해 원격 정보를 출력하므로 설정과 네트워크의 정오를 즉시 알 수 있습니다.

```bash
hpc-mcp \
  --host 192.168.12.12 \
  --user username \
  --ssh-bin /mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --sftp-bin /mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --root /home/username/alice \
  --local-root "$PWD" \
  --check
```

- 성공: `Configuration OK. Remote probe succeeded:`와 함께 hostname / 원격 사용자 / Slurm 사용 가능 여부가 출력됩니다.
- 실패: `Connection check FAILED: ...`와 원인(타임아웃 / 거부 / 인증 실패)이 출력되므로 그에 따라 점검합니다.

> **판단 기준**: `--check`가 성공하면 네트워크와 설정 모두 문제가 없으므로 다음 단계로 Codex/Reasonix를 연결하면 됩니다.
> `--check`가 실패하면 SSH/네트워크를 먼저 해결하고, 클라이언트 설정은 나중에 하세요.

---

## 4. 파라미터 설명

> 이 절은 **시작/자체 점검 관련** CLI 파라미터만 다룹니다. 설정 파일의 전체 키
> (`slurm.*`, `files.*`, `topology.*` 및 모든 환경 변수)는
> [CONFIGURATION.md](CONFIGURATION.md)를, **각 MCP 도구의 파라미터**는
> [TOOLS.md](TOOLS.md)를 참조하세요.

| 파라미터 | 역할 | 환경 변수 | 필수 |
|---|---|---|---|
| `--host` | HPC 호스트(IP 또는 `~/.ssh/config` 별칭) | `HPC_MCP_HOST` | **필수** |
| `--user` | SSH 로그인 사용자(여기서는 공용 계정) | `HPC_MCP_USER` | 권장 |
| `--root` | **원격** 샌드박스 루트. agent는 이 아래의 파일만 다룰 수 있음 | `HPC_MCP_ROOT` | **필수** |
| `--local-root` | **로컬**에서 업로드/다운로드를 허용하는 디렉터리(기본값 = 시작 시 디렉터리 + `/tmp`, 여러 개는 `local_roots`) | `HPC_MCP_LOCAL_ROOT` / `HPC_MCP_LOCAL_ROOTS` | 선택 |
| `--port` | SSH 포트(기본 22) | `HPC_MCP_PORT` | 선택 |
| `--identity-file` | 개인 키 경로(기본은 `~/.ssh/config` 사용) | `HPC_MCP_IDENTITY_FILE` | 선택 |
| `--ssh-bin` | ssh 실행 파일 경로. 예: `/usr/bin/ssh`, `@/usr/bin/ssh`, `@/mnt/c/.../ssh.exe` | `HPC_MCP_SSH_BIN` | 선택 |
| `--sftp-bin` | sftp 실행 파일 경로(형식은 `--ssh-bin`과 동일) | `HPC_MCP_SFTP_BIN` | 선택 |
| `--config` | YAML 설정 파일 경로 | — | 선택 |
| `--check` | 연결만 확인하고 종료 | — | 선택 |
| `--log-file` | 로그를 추가 기록할 파일(stderr에도 계속 출력) | `HPC_MCP_LOG_FILE` | 선택 |
| `--log-level` | 로그 레벨: `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`(기본 `INFO`) | `HPC_MCP_LOG_LEVEL` | 선택 |
| `--version` | 버전을 출력하고 종료 | — | 선택 |

하위 명령 `hpc-mcp mcp-add`(Codex / Reasonix의 MCP 설정을 기록. 멱등적이며 기존 설정을 망가뜨리지 않음):

| 파라미터 | 역할 | 기본값 |
|---|---|---|
| `--client` | 갱신할 클라이언트: `codex`, `reasonix`, `all` | `all` |
| `--config` | YAML 설정 경로(그 안의 host/root 등이 클라이언트 env에 기록됨) | — |
| `--host` / `--user` / `--root` | 설정 파일 없이 직접 전달 | — |

**`--local-root`에 대하여**: `hpc.files.upload`/`download`가 접근할 수 있는 **로컬** 디렉터리 범위를 제한하여, agent가 로컬의 `.ssh` 같은 민감한 디렉터리를 읽지 못하게 합니다. 보통 현재 프로젝트 디렉터리(`$PWD`)면 충분합니다. **필수는 아니며**, 지정하지 않으면 현재 디렉터리가 기본값입니다.

**`--root`에 대하여**: 이는 원격 클러스터에서 **당신 전용 하위 디렉터리**입니다(로그인은 공용 계정 `username`을 사용하므로). `/home/username/alice`는 그 공용 계정 아래의 당신 개인 공간이며 정확한 지정입니다.

---

## 5. 긴 파라미터 대신 설정 파일 사용(권장)

파라미터를 YAML에 고정하면 이후에는 한 줄로 시작할 수 있습니다.

```bash
mkdir -p ~/.config/hpc-mcp
cat > ~/.config/hpc-mcp/192.168.12.12.yaml <<'EOF'
host: 192.168.12.12                                  # ~/.ssh/config의 Host 별칭
user: username
root: /home/username/alice
local_root: /home/alice               # 허용할 로컬 디렉터리
ssh_bin: /mnt/c/Windows/System32/OpenSSH/ssh.exe
sftp_bin: /mnt/c/Windows/System32/OpenSSH/sftp.exe

slurm:
  allowed_partitions: [thcp1]               # 실제 파티션 이름으로 반드시 변경!
  max_cpus: 64
  max_nodes: 2
  max_time: "24:00:00"

# 선택: 계산 노드 토폴로지 탐색(hpc.cluster.topo에서 사용)
# topology:
#   enabled: true             # false = 도구를 등록하지 않고 수집 작업도 전혀 제출하지 않음
#   cache_ttl_seconds: 86400  # 토폴로지 캐시 24h
EOF
```

시작 / 자체 점검:

```bash
hpc-mcp --config ~/.config/hpc-mcp/192.168.12.12.yaml --check
```

> 주의: `allowed_partitions`의 기본값은 **비어 있음**입니다(= 모든 제출 거부, fail-closed).
> 반드시 실제 클러스터의 파티션 이름으로 바꾸세요(클러스터에서 `sinfo`로 확인).

> **키 이름 규칙**: 설정 파일의 키는 **밑줄**을 씁니다(`ssh_bin`, `local_root`,
> `allowed_partitions`). `config/example.yaml`과 일치합니다. 하이픈 형식
> (`ssh-bin`)도 별칭으로 인식되지만 권장하지 않습니다.
> 철자가 틀린 키는 오류가 아니라 `Ignoring unknown config key 'xxx'` 경고 한 줄만
> 출력하고 무시됩니다. 이 줄이 보이면 설정을 고치세요.

> **소스/설정을 바꿨는데 반영되지 않나요?** `pip install .`로 설치했다면
> 가상 환경에 **복사본**이 들어 있습니다. 저장소 소스를 편집해도 자동 반영되지 않으며
> 재설치가 필요합니다:
> ```bash
> source ~/venvs/hpc-mcp/bin/activate
> cd ~/git_repo/HPC-MCP && pip install .
> # 이후 편집을 즉시 반영하려면 editable로 설치: pip install -e .
> ```

---

## 6. Codex / Reasonix 연결

`--check`가 통과하면 클라이언트에 등록합니다.

### Codex

```bash
codex mcp add hpc \
  --env HPC_MCP_HOST=192.168.12.12 \
  --env HPC_MCP_USER=username \
  --env HPC_MCP_ROOT=/home/username/alice/yourhome \
  --env HPC_MCP_LOCAL_ROOT=/home/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute,debug \
  --env HPC_MCP_SSH_BIN=/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --env HPC_MCP_SFTP_BIN=/mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --env HPC_MCP_PORT=22 \
  --env HPC_MCP_LOG_FILE=/home/alice/.local/share/hpc-mcp/hpc-mcp.log \
  --env HPC_MCP_LOG_LEVEL=INFO \
  -- \
  /home/alice/venvs/hpc-mcp/bin/hpc-mcp \
  --config /home/alice/.config/hpc-mcp/192.168.12.12.yaml
```

### Reasonix

```bash
reasonix mcp add hpc \
  --env HPC_MCP_HOST=192.168.12.12 \
  --env HPC_MCP_USER=username \
  --env HPC_MCP_ROOT=/home/username/alice/yourhome \
  --env HPC_MCP_LOCAL_ROOT=/home/alice \
  --env HPC_MCP_ALLOWED_PARTITIONS=compute,debug \
  --env HPC_MCP_SSH_BIN=/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  --env HPC_MCP_SFTP_BIN=/mnt/c/Windows/System32/OpenSSH/sftp.exe \
  --env HPC_MCP_PORT=22 \
  --env HPC_MCP_LOG_FILE=/home/alice/.local/share/hpc-mcp/hpc-mcp.log \
  --env HPC_MCP_LOG_LEVEL=INFO \
  /home/alice/venvs/hpc-mcp/bin/hpc-mcp \
  --config /home/alice/.config/hpc-mcp/192.168.12.12.yaml
```

등록하면 클라이언트가 stdio로 `hpc-mcp`를 실행합니다. 이후 "내 프로젝트 디렉터리를 나열해 줘", "Slurm 작업을 제출해 줘"라고 요청할 수 있습니다.

---

## 7. "멈춘 것처럼 보이는" 현상 대응표

| 보이는 현상 | 실제 원인 | 조치 |
|---|---|---|
| `starting: ...`에서 멈춰 움직이지 않음 | **정상**. stdio 서버가 클라이언트 입력을 기다리는 중 | 아무것도 하지 않아도 됩니다. 클라이언트에서 도구를 호출하거나 `--check`로 확인하세요 |
| `--check`가 `Connection timed out` | **네트워크 불통**(사설 IP는 VPN 필요) | VPN 연결/점프 호스트 설정 후 수동으로 `ssh` 테스트 |
| 명령줄에서는 되는데 설정 파일로는 연결되지 않음 | 설정의 `ssh_bin`이 적용되지 않아(키 이름 오타/오래된 복사본 설치) PATH의 `ssh`로 되돌아감(WSL의 Linux ssh는 Windows VPN 경로를 타지 않음) | 로그의 `using ssh executable: ...`이 설정한 것인지 확인. 키 이름을 바로잡고(5절) `pip install .`로 재설치 |
| `--check`가 `Permission denied` | 키 인증 없음 | `ssh-copy-id`로 키 설정 |
| `--check`가 `getsockname failed: Not a socket` | SSH 제어 소켓 재사용 이상(WSL이나 오래된 설치에서 흔함) | hpc-mcp를 갱신해 재설치. 최신 버전은 항상 독립된 일반 `ssh` 프로세스를 사용 |
| 첫 연결에서 `Are you sure ... yes/no?` 질문 | 호스트 키 미고정 | 수동으로 `ssh`를 한 번 실행해 yes 입력. 또는 `strict_host_key_checking: accept-new` 설정 |
| 모든 도구가 `No Slurm partitions are allowed`로 거부됨 | 파티션 허용 목록이 비어 있음 | `slurm.allowed_partitions` 설정 |
| `pip install .`에서 `UNKNOWN-0.0.0` | pip/setuptools가 오래됨 | 가상 환경에 재설치(1단계 방법 A) |
| `ERROR: pip's dependency resolver does not currently take into account...`(torch/httpcore 충돌) | 시스템 `~/.local`에 다른 패키지가 섞여 있음 | **hpc-mcp 동작에는 영향 없음**. 격리된 가상 환경으로 전환 권장(1단계 방법 A) |

---

## 8. 로그 보기

- 모든 로그는 **stderr**로 출력됩니다(stdout은 MCP 프로토콜 전용).
- 파일로 남기려면: `--log-file ~/.local/share/hpc-mcp/hpc-mcp.log`.
- 각 호출의 허용/거부를 보려면: `--log-level DEBUG`.
