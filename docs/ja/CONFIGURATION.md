# 設定ファイルリファレンス（config YAML）

他の言語：[English](../CONFIGURATION.md) | [简体中文](../zh-CN/CONFIGURATION.md) | [한국어](../ko/CONFIGURATION.md) | [繁體中文](../zh-TW/CONFIGURATION.md)

このドキュメントは **YAML 設定ファイルがサポートするすべてのパラメータ**（位置、型、既定値、範囲、
対応する環境変数 `HPC_MCP_*`、CLI オプション）を一覧します。`config/example.yaml` は、
そのままコピーして使える完全なサンプルです。

> **優先順位（高い → 低い）**：CLI オプション > 環境変数 > 設定ファイル > 組み込みの既定値。
> 同じパラメータが複数箇所にある場合、優先度の高いものが有効になります。

> **キー名のルール**：設定ファイルは**アンダースコア**のキー（`local_root`、`allowed_partitions`、
> `ssh_bin`）を使います。ハイフン形式（`local-root`、`ssh-bin`）も別名として認識されますが推奨しません。
> 未知のキーはエラーにならず、`Ignoring unknown config key 'xxx'` の警告を 1 行出して無視されます。
> この行が出たら綴りミスです。

> 本ファイルは**サーバ側の設定パラメータ**を説明します。起動／セルフチェック用の CLI パラメータは
> [QUICKSTART.md](QUICKSTART.md) の第 4 節、**各 MCP ツールのパラメータ**は [TOOLS.md](TOOLS.md) を参照してください。

---

## トップレベルのパラメータ

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `host` | string | —（必須） | SSH ホスト。`~/.ssh/config` の `Host` 別名を推奨。環境変数 `HPC_MCP_HOST`、CLI `--host` |
| `user` | string | — | SSH ユーザー（共有アカウントでも可）。`HPC_MCP_USER`、`--user` |
| `port` | int | `22` | SSH ポート（1–65535）。`HPC_MCP_PORT`、`--port` |
| `root` | string | —（必須） | リモートの**ユーザー専用**ルートディレクトリ。agent のリモート操作はすべてこの配下に制限されます。絶対パスで、`/` にはできません。`HPC_MCP_ROOT`、`--root` |
| `local_root` | string | 現在の作業ディレクトリ | ローカルのアップロード／ダウンロードを許可するディレクトリ（複数は `local_roots`）。`.ssh`/`.gnupg` などの資格情報ディレクトリは不可。`HPC_MCP_LOCAL_ROOT`、`--local-root` |
| `local_roots` | list[string] | `[現在のディレクトリ, システムの一時ディレクトリ]` | ローカルの許可ディレクトリ一覧。`local_root` と二択で、同時にある場合は `local_roots` が優先。`HPC_MCP_LOCAL_ROOTS`（カンマ区切り） |
| `identity_file` | string | `~/.ssh/config` 由来 | SSH 秘密鍵のパス（agent が鍵の中身に触れることはありません）。`HPC_MCP_IDENTITY_FILE`、`--identity-file` |
| `ssh_bin` | string | PATH から探索 | ssh 実行ファイル：名前のみ、絶対パス、または `@` 接頭辞（WSL 用）。`HPC_MCP_SSH_BIN`、`--ssh-bin` |
| `sftp_bin` | string | PATH から探索 | sftp 実行ファイル。書式は `ssh_bin` と同じ。`HPC_MCP_SFTP_BIN`、`--sftp-bin` |
| `wait_max_seconds` | int | `3600` | `hpc.slurm.wait` / `wait_and_diagnose` の最大待機秒数（≤ 7 日）。`HPC_MCP_WAIT_MAX_SECONDS` |
| `cache_ttl_seconds` | float | `2.0` | 読み取り専用クエリの重複排除キャッシュ TTL（秒）。`0` で無効。`HPC_MCP_CACHE_TTL_SECONDS` |
| `log_file` | string | stderr のみ | ログを追記するファイルのパス（`~` は展開されます）。`HPC_MCP_LOG_FILE`、`--log-file` |
| `log_level` | string | `INFO` | ログレベル：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`。`HPC_MCP_LOG_LEVEL`、`--log-level` |

> `ssh.*` サブセクションのパラメータはトップレベルにも直接書けます（例：`connect_timeout`、
> `strict_host_key_checking`）。`ssh:` サブセクションと等価です。

---

## `ssh:` サブセクション

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `host` | string | — | トップレベルの `host` と同じ |
| `port` | int | `22` | トップレベルの `port` と同じ |
| `user` | string | — | トップレベルの `user` と同じ |
| `identity_file` | string | — | トップレベルの `identity_file` と同じ |
| `connect_timeout` | int | `15` | SSH 接続タイムアウト（秒、≤ 3600）。`HPC_MCP_CONNECT_TIMEOUT` |
| `command_timeout` | int | `30` | リモートコマンド 1 回のタイムアウト（秒、≤ 86400）。`HPC_MCP_COMMAND_TIMEOUT` |
| `strict_host_key_checking` | string | `yes` | ホスト鍵の検証：`yes`（`known_hosts` に固定済みであることを要求、推奨）/ `accept-new`（初回接続時に自動登録）。**`no` は拒否されます。** `HPC_MCP_STRICT_HOST_KEY_CHECKING` |
| `ssh_bin` | string | PATH | トップレベルの `ssh_bin` と同じ |
| `sftp_bin` | string | PATH | トップレベルの `sftp_bin` と同じ |

---

## `slurm:` サブセクション

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `allowed_partitions` | list[string] | `[]`（空 = すべての投入を拒否、fail-closed） | 投入を許可するパーティションの許可リスト。名前に `/` や制御文字は使えません。`HPC_MCP_ALLOWED_PARTITIONS`（カンマ区切り） |
| `max_nodes` | int | `2` | 1 ジョブあたりのノード数上限（≥ 1）。`HPC_MCP_MAX_NODES` |
| `max_cpus` | int | `64` | 1 ジョブあたりの総 CPU 上限：`nodes × ntasks × cpus_per_task`（≥ 1）。`HPC_MCP_MAX_CPUS` |
| `max_memory_mb` | int | `262144`（256 GiB） | 1 ジョブあたりのメモリ上限（MiB、≥ 1）。`HPC_MCP_MAX_MEMORY_MB` |
| `max_gpus` | int | `4` | 1 ジョブあたりの GPU 上限（≥ 0）。`HPC_MCP_MAX_GPUS` |
| `max_time` | string | `"24:00:00"` | 1 ジョブあたりの時間上限。Slurm 形式：`HH:MM:SS`、`D-HH:MM:SS`。日数は繰り上がり可能（`"2-24:00:00"` = 3 日）。`HPC_MCP_MAX_TIME` |
| `max_concurrent_jobs` | int | `20` | **同時にアクティブなジョブの最大数。** 投入時に、このインスタンスのレジストリ内で `squeue` と `sacct` の両方で終了状態でないジョブを数え、上限に達していれば新しい投入を拒否して待機を促します。終了済み（`COMPLETED`/`FAILED`/`CANCELLED`/…）のジョブは枠を自動的に解放します。`squeue` の照会は成功したがキューにおらず `sacct` にも記録がない場合も同様に解放されます（accounting の記録が失効して枠が永久に埋まるのを防ぐため）。`squeue` の照会に失敗した場合は保守的に数え続けます（fail-closed）。`HPC_MCP_MAX_CONCURRENT_JOBS` |

> すべての数値 `max_*` フィールドは簡単な算術式を受け付けます：`+ - * /` と括弧。例：`"4*16"`、`"128/4"`。
> 環境変数でも同様に使えます（例：`HPC_MCP_MAX_CPUS=4*16`）。

---

## `shell:` サブセクション

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `safe_commands` | list[string] | `[]`（組み込みの許可リストに追加） | 組み込みの最小許可リストに追加するコマンドの basename（`/` を含まず、128 文字以下）。`HPC_MCP_SAFE_COMMANDS`（カンマ区切り） |
| `max_exec_seconds` | int | `30` | safe コマンド 1 回の実行時間上限（秒、≤ 86400）。`HPC_MCP_SHELL_MAX_EXEC_SECONDS` |
| `max_output_bytes` | int | `1048576`（1 MiB） | safe コマンド 1 回の出力上限（バイト）。`HPC_MCP_MAX_OUTPUT_BYTES` |

---

## `files:` サブセクション

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `max_read_bytes` | int | `1048576`（1 MiB） | `hpc.files.read` 1 回の読み取りの**ハード上限**（バイト）。`HPC_MCP_MAX_READ_BYTES` |
| `max_read_slice_bytes` | int | `262144`（256 KiB） | `hpc.files.read` 1 回の返却上限（bounded slice）。agent が巨大なログを 1 回でページングし尽くすのを防ぎます。`HPC_MCP_MAX_READ_SLICE_BYTES` |
| `max_write_bytes` | int | `10485760`（10 MiB） | `hpc.files.write` 1 回の書き込み上限（バイト）。`HPC_MCP_MAX_WRITE_BYTES` |
| `max_list_entries` | int | `2000` | `hpc.files.list` 1 回の返却エントリ上限。`HPC_MCP_MAX_LIST_ENTRIES` |
| `max_recursive_depth` | int | `3` | `hpc.files.list recursive` の再帰深さ上限（≤ 64）。`HPC_MCP_MAX_RECURSIVE_DEPTH` |
| `search_max_matches` | int | `200` | `hpc.files.search` の 1 ファイルあたり最大マッチ数（≤ 100000）。`HPC_MCP_SEARCH_MAX_MATCHES` |
| `search_max_context_lines` | int | `10` | 各マッチに付けるコンテキスト行数（≤ 1000）。`HPC_MCP_SEARCH_MAX_CONTEXT_LINES` |
| `search_max_scan_bytes` | int | `67108864`（64 MiB） | 1 ファイルあたりのスキャン上限（バイト）。`HPC_MCP_SEARCH_MAX_SCAN_BYTES` |
| `search_max_files` | int | `1000` | 1 回の検索でスキャンする最大ファイル数（≤ 1000000）。`HPC_MCP_SEARCH_MAX_FILES` |
| `search_max_depth` | int | `6` | 検索の再帰深さ上限（≤ 64）。`HPC_MCP_SEARCH_MAX_DEPTH` |
| `search_timeout` | int | `5` | リモート検索 1 回のタイムアウト（秒、≤ 3600）。`HPC_MCP_SEARCH_TIMEOUT` |

---

## `topology:` サブセクション

`hpc.cluster.topo`（計算ノードのハードウェア／NUMA／SIMD トポロジ探索）を制御します。このツールは
初回呼び出し（またはキャッシュ失効、`refresh=true`）で、指定パーティションに**1 CPU の収集ジョブ**を投入し、
計算ノード上で `lscpu` / `/proc/cpuinfo` / `numactl --hardware` / `/proc/meminfo` を読み取り、
ログインノードの `sinfo` によるパーティション／ノードビューと統合して、CPU モデル、sockets/cores/threads、
SIMD 命令セット、NUMA ドメインと距離、キャッシュ階層、ノードメモリ、および導出した並列パラメータの
推奨（`ntasks_per_node`、`cpus_per_task`、`--cpu-bind`/`--hint`、`OMP_NUM_THREADS`）を返します。

収集スクリプトは**サーバ側で固定された内容**です。agent が指定したパラメータがスクリプトに書き込まれることはなく、
スクリプトは `squeue`/`sacct`/`scontrol` を**呼びません**（共有アカウントの分離）。読むのはノードローカルの
ハードウェア情報だけです。

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `enabled` | bool | `true` | `hpc.cluster.topo` を登録するかどうか。`false` にするとツールは agent に公開されず、収集ジョブも一切投入されません。`HPC_MCP_TOPOLOGY_ENABLED` |
| `cache_ttl_seconds` | int | `86400`（24 h） | トポロジ結果の鮮度：プロセス内キャッシュとリモートの `$ROOT/.hpc-mcp/topo/topology_<partition>.json` がこの TTL を共有し、再キューイングを避けます。`0` = 呼び出しごとに再収集。`HPC_MCP_TOPOLOGY_CACHE_TTL_SECONDS` |
| `wait_seconds` | int | `300` | 収集ジョブの終了を待つ秒数。タイムアウト時は `status: "pending"` と `job_id` を返します（ジョブはキューに残り、次の呼び出しで再利用され、再投入はされません）。`HPC_MCP_TOPOLOGY_WAIT_SECONDS` |
| `collect_time_limit` | string | `"00:03:00"` | 収集ジョブ自体の Slurm 時間上限（書式は `slurm.max_time` と同じ）。`HPC_MCP_TOPOLOGY_COLLECT_TIME_LIMIT` |

> 収集ジョブも `slurm.allowed_partitions`、`max_concurrent_jobs`、および通常のジョブ所有権ルールの
> 対象です。`allowed_partitions` が空の場合、このツールは fail-closed（呼び出しを拒否）になります。
> 探索したパーティション名は `sinfo -p <パーティション>` とキャッシュのファイル名に使われるため、
> 一般的な Slurm ポリシーより厳格で、`[A-Za-z0-9_.-]` のみを受け付けます。

---

## 完全なサンプル

```yaml
# HPC-MCP の完全な設定例（「必須」と明記したものを除き、すべてのキーは任意）
host: my-hpc                      # 必須：SSH の Host 別名またはアドレス
user: shared_account
port: 22
root: /home/shared_account/alice  # 必須：リモートのユーザー専用ルートディレクトリ
local_root: /home/alice/proj      # ローカルのアップロード／ダウンロード用ディレクトリ
# local_roots: [ /home/alice/proj, /tmp ]
# identity_file: ~/.ssh/id_ed25519
# ssh_bin: /usr/bin/ssh
# sftp_bin: @/mnt/c/Windows/System32/OpenSSH/ssh.exe

ssh:
  connect_timeout: 15
  command_timeout: 30
  strict_host_key_checking: "yes"   # または "accept-new"。"no" は拒否されます

slurm:
  allowed_partitions: [compute]     # 必須設定：既定の空 = すべての投入を拒否
  max_nodes: 2
  max_cpus: 64                      # "4*16" のような式も可
  max_memory_mb: 262144             # 256 GiB
  max_gpus: 4
  max_time: "24:00:00"
  max_concurrent_jobs: 20           # 同時アクティブジョブ上限、既定 20

shell:
  safe_commands: []                 # 追加で許可するコマンドの basename
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
  enabled: true                     # false = hpc.cluster.topo を登録しない（収集ジョブも投入しない）
  cache_ttl_seconds: 86400          # トポロジの鮮度（24h）。0 = 毎回再収集
  wait_seconds: 300                 # 収集ジョブの待機上限。超えると pending を返す
  collect_time_limit: "00:03:00"    # 収集ジョブ自体の Slurm 時間上限

wait_max_seconds: 3600
cache_ttl_seconds: 2.0
log_file: ~/.local/share/hpc-mcp/hpc-mcp.log
log_level: INFO
```

# 設定と接続の検証
```bash
source ~/venvs/hpc-mcp/bin/activate
hpc-mcp --config ~/.config/hpc-mcp/config.yaml --check
```
