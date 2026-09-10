# CC-Switch 연결 설정(MCP JSON)

다른 언어: [English](../CC_SWITCH.md) | [简体中文](../zh-CN/CC_SWITCH.md) | [日本語](../ja/CC_SWITCH.md) | [繁體中文](../zh-TW/CC_SWITCH.md)

[CC-Switch](https://github.com/farion1231/cc-switch)는 여러 MCP 설정(Claude Code / Codex / Roo Code 등)을 **JSON**으로 관리하고 원클릭으로 전환할 수 있게 해 줍니다.

hpc-mcp는 **stdio** 유형의 MCP 서버입니다. CC-Switch에서는 `type: "stdio"`와 `command` + `args` + `env`로 기술하며, `url`은 **필요하지 않습니다**(SSE/HTTP 유형 전용).

---

## 전체 설정 예시

CC-Switch에서 "새로 추가"할 때 아래 JSON을 붙여 넣으세요(자신의 클러스터에 맞게 값을 수정):

```json
{
  "name": "hpc-mcp",
  "type": "stdio",
  "command": "/home/alice/venvs/hpc-mcp/bin/hpc-mcp",
  "args": ["--config", "/home/alice/.config/hpc-mcp/192.168.12.12.yaml"],
  "env": {
    "HPC_MCP_HOST": "hpc",
    "HPC_MCP_USER": "shared_account",
    "HPC_MCP_ROOT": "/home/shared_account/alice/yourhome",
    "HPC_MCP_LOCAL_ROOT": "/home/alice/project",
    "HPC_MCP_ALLOWED_PARTITIONS": "compute,debug",
    "HPC_MCP_SSH_BIN": "/mnt/c/Windows/System32/OpenSSH/ssh.exe",
    "HPC_MCP_SFTP_BIN": "/mnt/c/Windows/System32/OpenSSH/sftp.exe",
    "HPC_MCP_PORT": "22",
    "HPC_MCP_LOG_FILE": "/home/alice/.local/share/hpc-mcp/hpc-mcp.log",
    "HPC_MCP_LOG_LEVEL": "INFO"
  }
}
```

> `hpc-mcp`가 conda/venv에 설치되어 PATH에 없다면 `command`에 실행 파일의 전체 경로를
> 지정하세요(예: `"/home/alice/venvs/hpc-mcp/bin/hpc-mcp"`).
> 저장소의 `scripts/hpc-mcp-run` 런처는 설치 위치를 자동으로 찾으므로 이식성이 가장 높습니다.

---

## 필드 설명

| 필드 | 필수 | 설명 |
|---|---|---|
| `name` | 필수 | 서버 이름. CC-Switch에 표시되는 이름이며 임의 지정 가능(예: `hpc-mcp`) |
| `type` | 필수 | 항상 `"stdio"`(hpc-mcp는 표준 입출력으로 클라이언트와 통신) |
| `command` | 필수 | 실행 명령. `<저장소>/scripts/hpc-mcp-run`(설치된 hpc-mcp를 자동으로 찾음) 또는 hpc-mcp 실행 파일의 절대 경로 권장 |
| `args` | 선택 | 추가 명령줄 인자. 예: `["--check"]`는 연결 자체 점검만 수행. 일반 실행에서는 `[]` 또는 생략 |
| `env` | 필수(최소 host/root) | 주입할 환경 변수. 아래 참조 |

### 자주 쓰는 `env` 변수(README의 "설정" 절과 완전히 동일)

| 변수 | 필수 | 역할 |
|---|---|---|
| `HPC_MCP_HOST` | **필수** | HPC 호스트(IP 또는 `~/.ssh/config`의 `Host` 별칭). 예: `my-hpc` |
| `HPC_MCP_ROOT` | **필수** | **원격** 샌드박스 루트. agent는 이 아래의 파일만 다룰 수 있음(예: `/home/shared_account/alice`) |
| `HPC_MCP_USER` | 권장 | SSH 로그인 사용자(공용 계정) |
| `HPC_MCP_LOCAL_ROOT` | 선택 | **로컬**에서 업로드/다운로드를 허용하는 디렉터리(여러 개는 콤마 구분 → `HPC_MCP_LOCAL_ROOTS`). 미지정 시 시작 디렉터리 + `/tmp` |
| `HPC_MCP_ALLOWED_PARTITIONS` | 권장 | 허용할 Slurm 파티션(콤마 구분). **기본값은 비어 있음 = 모든 제출 거부(fail-closed)** |
| `HPC_MCP_PORT` | 선택 | SSH 포트(기본 22) |
| `HPC_MCP_IDENTITY_FILE` | 선택 | 개인 키 경로(기본은 `~/.ssh/config` 경유) |
| `HPC_MCP_SSH_BIN` | 선택 | ssh 실행 파일. 예: `/usr/bin/ssh`, `@/mnt/c/Windows/System32/OpenSSH/ssh.exe`(WSL에서 필요) |
| `HPC_MCP_SFTP_BIN` | 선택 | sftp 실행 파일(형식은 `HPC_MCP_SSH_BIN`과 동일) |
| `HPC_MCP_LOG_FILE` | 선택 | 로그를 저장할 경로 |
| `HPC_MCP_LOG_LEVEL` | 선택 | `DEBUG`/`INFO`/`WARNING`/`ERROR`, 기본 `INFO` |

> 그 밖의 파라미터(Slurm 리소스 상한, shell 허용 목록, 파일 예산, 캐시 TTL 등)는
> **설정 파일/CLI** 영역입니다. CC-Switch의 `env`로 다룰 수 있는 것은 위 항목뿐입니다. 전체 목록은
> [`config/example.yaml`](../../config/example.yaml)과 [`CONFIGURATION.md`](CONFIGURATION.md)를,
> 절차는 [`QUICKSTART.md`](QUICKSTART.md)를 참조하세요.

---

## 시작하기 전에

1. **비밀번호 없는 로그인 설정**: hpc-mcp는 `BatchMode=yes`를 강제하며 비밀번호를 요구하지 않습니다. 수동으로 확인하세요:
   ```bash
   ssh -o BatchMode=yes my-hpc "echo OK"
   ```
   이것이 `OK`를 반환해야 합니다. 그렇지 않으면 먼저 `ssh-copy-id my-hpc`를 실행하세요.
2. **네트워크 도달 확인**: 사설 IP(`192.168.x.x` 등)는 VPN이 필요하거나 `~/.ssh/config`의
   `ProxyJump` 점프 호스트가 필요합니다.
3. **먼저 자체 점검**: `env`에 `"HPC_MCP_HOST"` 등을 넣은 뒤 임시로
   `command` + `args: ["--check"]`로 연결을 확인하고, 성공하면 일반 모드로 되돌리는 것이 안전합니다.

---

## 자주 묻는 질문

| 현상 | 원인과 조치 |
|---|---|
| 시작 후 `starting: ...`에서 멈춤 | **정상**: stdio 서버가 클라이언트 입력을 기다리는 중입니다. 클라이언트에서 도구를 호출하세요 |
| 모든 도구가 `No Slurm partitions are allowed`로 거부됨 | `HPC_MCP_ALLOWED_PARTITIONS`가 없거나 비어 있음 → 실제 파티션 이름을 설정(클러스터에서 `sinfo`로 확인) |
| 연결 시 `Permission denied` | 키 인증 없음 → `ssh-copy-id` |
| `Connection timed out` 발생 | 네트워크 불통(사설 IP는 VPN/점프 호스트 필요) |
| WSL에서는 연결되지 않지만 명령줄에서는 됨 | `HPC_MCP_SSH_BIN`/`HPC_MCP_SFTP_BIN`을 Windows의 OpenSSH(`/mnt/c/...`)로 지정해야 합니다 |
| 첫 연결에서 `Are you sure ... yes/no?` 질문 | 호스트 키 미고정 → 수동으로 `ssh`를 한 번 실행해 확인하거나 `strict_host_key_checking: accept-new` 설정 |

전체 문제 해결 표는 [QUICKSTART.md](QUICKSTART.md) 7절에 있습니다.

---

## 관련 파일

- 프로젝트 표준 설정: [`.mcp.json`](../../.mcp.json)(MCP 표준 형식, `mcpServers` 객체)
- 파라미터 정본 설명: [`config/example.yaml`](../../config/example.yaml)
- 설치와 문제 해결: [`QUICKSTART.md`](QUICKSTART.md)
