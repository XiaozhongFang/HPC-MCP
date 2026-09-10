# CC-Switch 接続設定（MCP JSON）

他の言語：[English](../CC_SWITCH.md) | [简体中文](../zh-CN/CC_SWITCH.md) | [한국어](../ko/CC_SWITCH.md) | [繁體中文](../zh-TW/CC_SWITCH.md)

[CC-Switch](https://github.com/farion1231/cc-switch) は複数の MCP 設定
（Claude Code / Codex / Roo Code など）を **JSON** で管理し、ワンクリックで切り替えられます。

hpc-mcp は **stdio** タイプの MCP サーバです。CC-Switch では `type: "stdio"` と
`command` + `args` + `env` で記述します。`url` は**不要**です（SSE/HTTP タイプ専用）。

---

## 設定例（完全版）

CC-Switch で「新規追加」するときに、下の JSON を貼り付けます（自分のクラスタに合わせて値を変更してください）：

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

> `hpc-mcp` が conda/venv に入っていて PATH にない場合は、`command` に実行ファイルの
> フルパスを指定します（例：`"/home/alice/venvs/hpc-mcp/bin/hpc-mcp"`）。
> リポジトリ内の `scripts/hpc-mcp-run` ランチャーはインストール先を自動で探すため、最も移植性が高い方法です。

---

## フィールドの説明

| フィールド | 必須 | 説明 |
|---|---|---|
| `name` | 必須 | サーバ名。CC-Switch 上に表示される名前で、任意（例：`hpc-mcp`） |
| `type` | 必須 | 常に `"stdio"`（hpc-mcp は標準入出力でクライアントと通信します） |
| `command` | 必須 | 起動コマンド。`<リポジトリ>/scripts/hpc-mcp-run`（インストール済みの hpc-mcp を自動特定）か、hpc-mcp 実行ファイルの絶対パスを推奨 |
| `args` | 任意 | 追加のコマンドライン引数。例：`["--check"]` で接続確認のみ。通常運用では `[]` か省略 |
| `env` | 必須（少なくとも host/root） | 注入する環境変数。下記参照 |

### よく使う `env` 変数（README の「設定」と完全に同じ）

| 変数 | 必須 | 役割 |
|---|---|---|
| `HPC_MCP_HOST` | **必須** | HPC ホスト（IP または `~/.ssh/config` の `Host` 別名）。例：`my-hpc` |
| `HPC_MCP_ROOT` | **必須** | **リモート**のサンドボックスルート。agent はこの配下のファイルしか操作できない（例：`/home/shared_account/alice`） |
| `HPC_MCP_USER` | 推奨 | SSH ログインユーザー（共有アカウント） |
| `HPC_MCP_LOCAL_ROOT` | 任意 | **ローカル**でアップロード／ダウンロードを許可するディレクトリ（複数はカンマ区切り → `HPC_MCP_LOCAL_ROOTS`）。未指定なら起動ディレクトリ + `/tmp` |
| `HPC_MCP_ALLOWED_PARTITIONS` | 推奨 | 許可する Slurm パーティション（カンマ区切り）。**既定は空 = すべての投入を拒否（fail-closed）** |
| `HPC_MCP_PORT` | 任意 | SSH ポート（既定 22） |
| `HPC_MCP_IDENTITY_FILE` | 任意 | 秘密鍵のパス（既定は `~/.ssh/config` 経由） |
| `HPC_MCP_SSH_BIN` | 任意 | ssh 実行ファイル。例：`/usr/bin/ssh`、`@/mnt/c/Windows/System32/OpenSSH/ssh.exe`（WSL で必要） |
| `HPC_MCP_SFTP_BIN` | 任意 | sftp 実行ファイル（書式は `HPC_MCP_SSH_BIN` と同じ） |
| `HPC_MCP_LOG_FILE` | 任意 | ログの保存先パス |
| `HPC_MCP_LOG_LEVEL` | 任意 | `DEBUG`/`INFO`/`WARNING`/`ERROR`、既定 `INFO` |

> その他のパラメータ（Slurm のリソース上限、shell の許可リスト、ファイルの予算、キャッシュ TTL など）は
> **設定ファイル / CLI** の領域です。CC-Switch の `env` で扱えるのは上記のみです。完全な一覧は
> [`config/example.yaml`](../../config/example.yaml) と [`CONFIGURATION.md`](CONFIGURATION.md) を、
> 手順は [`QUICKSTART.md`](QUICKSTART.md) を参照してください。

---

## 使い始める前に

1. **パスワードなしログインを設定する**：hpc-mcp は `BatchMode=yes` を強制し、パスワードを要求しません。手動で確認してください：
   ```bash
   ssh -o BatchMode=yes my-hpc "echo OK"
   ```
   これが `OK` を返す必要があります。返らない場合は先に `ssh-copy-id my-hpc` を実行します。
2. **ネットワークが通ることを確認する**：プライベート IP（`192.168.x.x` など）は VPN、または `~/.ssh/config` の
   `ProxyJump` による踏み台が必要です。
3. **先にセルフチェックする**：`env` に `"HPC_MCP_HOST"` などを入れたあと、一時的に
   `command` + `args: ["--check"]` で接続確認し、成功したら通常モードに戻すと安全です。

---

## よくある質問

| 現象 | 原因と対処 |
|---|---|
| 起動後 `starting: ...` で止まる | **正常**：stdio サーバがクライアント入力を待っています。クライアントからツールを呼んでください |
| すべてのツールが `No Slurm partitions are allowed` で拒否される | `HPC_MCP_ALLOWED_PARTITIONS` が未設定か空 → 実際のパーティション名を設定（クラスタ上で `sinfo` により確認） |
| 接続が `Permission denied` | 鍵認証が未設定 → `ssh-copy-id` |
| `Connection timed out` と表示される | ネットワーク不通（プライベート IP は VPN／踏み台が必要） |
| WSL からは繋がらないがコマンドラインでは繋がる | `HPC_MCP_SSH_BIN`/`HPC_MCP_SFTP_BIN` を Windows 側の OpenSSH（`/mnt/c/...`）に向ける必要があります |
| 初回接続で `Are you sure ... yes/no?` と聞かれる | ホスト鍵が未固定 → 手動で `ssh` を 1 回実行して確認するか、`strict_host_key_checking: accept-new` を設定 |

完全なトラブルシューティング表は [QUICKSTART.md](QUICKSTART.md) の第 7 節にあります。

---

## 関連ファイル

- プロジェクト標準の設定：[`.mcp.json`](../../.mcp.json)（MCP 標準形式、`mcpServers` オブジェクト）
- パラメータの正式な説明：[`config/example.yaml`](../../config/example.yaml)
- インストールとトラブルシューティング：[`QUICKSTART.md`](QUICKSTART.md)
