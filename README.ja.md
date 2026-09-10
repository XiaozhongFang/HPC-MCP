# HPC-MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![MCP stdio server](https://img.shields.io/badge/MCP-stdio%20server-6f42c1.svg)](https://modelcontextprotocol.io)

[English](README.md) | [简体中文](README.zh-CN.md) | **日本語** | [한국어](README.ko.md) | [繁體中文](README.zh-TW.md)

**セキュリティ最優先**の MCP サーバ。Codex、Reasonix などのコーディング agent が、**SSH + Slurm** 経由でリモート HPC クラスタを安全に操作できるようにします。パスサンドボックス、ログインノードのコマンド許可リスト、Slurm のリソース上限、ジョブ所有権、そしてすべての呼び出しに対する ALLOW/DENY 監査——すべてコードで強制されます。

> **はじめての方へ → [docs/ja/QUICKSTART.md](docs/ja/QUICKSTART.md)**：手順どおりのインストール、各パラメータの説明、Codex/Reasonix の設定例、そして「起動しても固まる」「ネットワークが不通」「pip で UNKNOWN として入る」といった問題の切り分け表があります。

## アーキテクチャ

```text
Codex / Reasonix (Agent)
        │  MCP (stdio)
        ▼
  Project Skill            ← 行動指針（セキュリティ境界ではない）
        │
        ▼
  HPC MCP Server           ← セキュリティ境界（コードで強制）
        │
        ├── Path Sandbox        （USER_ROOT を強制）
        ├── Command Policy      （ログインノードの許可リスト）
        ├── Slurm Resource Policy（パーティション／リソース／同時実行）
        ├── SSH Manager         （固定 argv、ローカルシェルなし）
        ├── Job Tracker         （共有アカウント下のジョブ所有権）
        └── Audit Logger        （呼び出しごとの ALLOW/DENY 記録）
        │ SSH / SFTP
        ▼
  HPC Login Node  ──軽量な照会のみ許可──┐
        │ sbatch                          │
        ▼                                 ▼
  Compute Node (Slurm)         ユーザーの作業ディレクトリ $HPC_MCP_ROOT
        │
   Julia / MOOSE / Python / CMake
```

設計原則：**MCP がセキュリティ境界であり、Skill は行動指針にすぎない。** agent のプロンプトが誤っていても、Skill が誤解されても、中核の権限境界は迂回できません。

## セキュリティモデル

### 共有アカウントの分離

HPC では共有アカウントを使うことがよくあります（例：`/home/shared_account/`）。そのホームディレクトリは**あなた自身のディレクトリではありません**。必ず設定してください：

```
HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD       # アップロード／ダウンロードを許可するローカルディレクトリ
```

すべてのリモートファイル操作は、この root の配下に制限されます（シンボリックリンクの canonical 検査を含む）。ローカルの `upload`/`download` も同様に `local_roots`（1 つ以上の許可されたローカルディレクトリ。既定は起動プロセスの作業ディレクトリ + システムの一時ディレクトリ `/tmp`）に制限され、`.ssh`、秘密鍵、シンボリックリンクのパスは拒否されます。他ユーザーのディレクトリ（`/home/shared_account/other_user`）やシステムディレクトリ（`/etc`、`/opt`）は一律拒否です。

### ログインノードのポリシー

ログインノードで許可されるのは、軽量で読み取り専用の管理コマンド（許可リスト）だけです：`ls`、`find`、`cat`、`grep`、`head`、`tail`、`git status/diff/log`、`module list/avail` など。共有アカウントで他利用者のジョブを見られないように、`squeue`、`sacct`、`scontrol` は `hpc.shell.run_safe` からは公開されず、所有権検査を行う Slurm ツール経由でのみ利用できます。

ログインノードでは、計算・ビルド系の実行を**常に拒否**します：`julia`、`python`、`make`、`cmake --build`、`ninja`、`mpirun`、`srun`、`pytest`、`matlab`、GPU プログラムなど。すべて `hpc.slurm.submit` へ誘導されます。

コマンドポリシーは**コードレベル**で拒否します：シェルメタ文字（`;`、`&&`、`||`、`|`、`>`、`<`、`$()`、バッククォート、`&`）、パス形式の任意実行ファイル（`./program`）、`find -exec`、`git -c`、ネストしたシェル、`sudo`/`ssh`/`curl` などの危険なプログラム。解析に失敗した場合も同様に拒否します（fail-closed）。

`env`/`printenv` は要求されても、クリアされた最小環境でのみ実行されます。SSH のトークン、鍵、クラスタの資格情報が返ることはありません。すべてのコマンドのパスオペランドはリモートで `realpath` により再検証され、シンボリックリンクを追うオプションは拒否されます。

### Slurm リソースポリシー

`hpc.slurm.submit` が唯一の計算入口です。サーバ側で次を強制します：

- パーティションの許可リスト（既定は空 = すべて拒否。agent はリスト内のパーティションしか選べない）
- `max_nodes` / `max_cpus` / `max_memory_mb` / `max_gpus` / `max_time`
- `max_concurrent_jobs`（同時実行の上限）
- 作業ディレクトリは USER_ROOT の内側でなければならない
- ジョブの stdout/stderr は必ず `$ROOT/.hpc-mcp/jobs/<job-id>/` に取り込まれる

### ジョブ所有権（共有アカウント）

共有アカウントでは、Unix 側で利用者を区別できません。各 MCP インスタンスは、**自身が投入して登録した**ジョブ（`$ROOT/.hpc-mcp/tracked_jobs.json`）だけを管理します。他のジョブに対する `status/output/cancel/accounting` は一律拒否です。

### 失敗時は拒否（fail-closed）

設定の欠落、パスの解決不能、コマンドの解析失敗、パーティションの不確実性、SSH の異常——あらゆる不確実な状況は **DENY** となり、無制限のシェルへフォールバックすることはありません。

### 3 層のセキュリティ境界

| 層 | 機構 | 防ぐ対象 |
|---|---|---|
| Layer 1: MCP policy | パスサンドボックス、コマンド許可リスト、Slurm リソースポリシー、ジョブ所有権、監査 | agent の逸脱・暴走 |
| Layer 2: Slurm | パーティション許可リスト、リソース上限、同時実行上限、`sbatch` 入口の一本化 | 計算資源の濫用 |
| Layer 3: OS/クラスタ | 独立した Unix UID / ジョブ分離 / filesystem ACL / コンテナ | **悪意あるコードの分離**（任意） |

> **残るリスク（必ず理解してください）**：共有 Unix UID のもとでは、MCP は「agent が暴走しないこと」（Layer 1/2）しか保証できません。計算ノードに投入された悪意あるコードが、同じ UID が読める他のデータにアクセスしないことは**保証できません**（Layer 3）。真に敵対的なコードの分離には、独立した UID、Slurm のジョブ分離 + filesystem ACL、またはコンテナ／サンドボックスが必要です。Python 側の正規表現やポリシーを、悪意あるコードに対する OS レベルの分離と見なさないでください。

### クエリコストと重複排除

- `hpc.files.read` は **bounded slice** です：1 回あたり最大 `min(max_bytes, files.max_read_slice_bytes)`（既定 256KiB）バイトで、agent を offset=0 から EOF まで読ませることはありません。
- `hpc.files.list` の再帰列挙は**1 階層ずつページング**し（`page_size` + `next_cursor`）、リモート出力を `head` で打ち切り、ツリー全体をスキャンしてから捨てることは決してありません。
- `hpc.files.search` にはハードな予算（`max_matches`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`、すべてサーバ側でクランプ）があり、「まず位置を特定し、次に精読する」ために使います。
- 冪等な読み取り専用クエリは `tool+引数` で重複排除されます（TTL 既定 2 秒、`cache_ttl_seconds` で調整、0 で無効）。書き込み操作はキャッシュを積極的に無効化します。
- `hpc.slurm.queue/status` はこのインスタンスが追跡する job ID だけを照会し（`squeue -j <ids>`）、共有アカウントのキュー全体を**決して**スキャンしません。
- `hpc.shell.run_safe` のすべてのコマンド（許可リスト内の正当なコマンドを含む）には **command cost 予算**があり、`find`/`du`/`sort`/`git grep` などの高リスクコマンドは、より短い timeout と出力上限にクランプされます。
- agent に返す内容（ログ／ファイル内容）には**最小限の secret マスキング**（`password=`/`token=`/`Bearer`/AWS/PEM 秘密鍵ブロックなど）が適用されます。監査ログのマスキングとは別に運用され、科学技術系のログを壊しません。

## インストール

詳しい手順は [docs/ja/QUICKSTART.md](docs/ja/QUICKSTART.md) を参照してください。

インストールすると `hpc-mcp` コマンドが使えるようになります。

## 設定

3 つの方法があり、優先順位は **CLI > 環境変数 > 設定ファイル > 既定値** です。

### CLI

```bash
hpc-mcp --host my-hpc --user shared_account \
  --root /home/shared_account/alice --local-root "$PWD"

# ssh/sftp の実行ファイルを指定する場合（WSL 環境で必要になることがあります）：
#   --ssh-bin  /usr/bin/ssh
#   --ssh-bin  @/usr/bin/ssh
#   --ssh-bin  @/mnt/c/Windows/System32/OpenSSH/ssh.exe
#   --sftp-bin @/usr/bin/sftp
```

### 環境変数

```bash
export HPC_MCP_HOST=my-hpc
export HPC_MCP_USER=shared_account
export HPC_MCP_ROOT=/home/shared_account/alice
export HPC_MCP_LOCAL_ROOT=$PWD
export HPC_MCP_ALLOWED_PARTITIONS=compute,debug
export HPC_MCP_MAX_CPUS=64
export HPC_MCP_MAX_TIME=24:00:00
```

### YAML 設定ファイル

サポートされるすべてのキー、既定値、範囲、対応する環境変数は [`docs/ja/CONFIGURATION.md`](docs/ja/CONFIGURATION.md) にあります。以下はよく使う最小構成です：

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
  max_concurrent_jobs: 20    # 同時アクティブジョブ上限（既定 20、変更可）
```

```bash
hpc-mcp --config config.yaml
```

### SSH 設定（推奨）

接続の詳細は `~/.ssh/config` で管理し、`HPC_MCP_HOST` から Host 別名を参照します：

```sshconfig
Host my-hpc
    HostName hpc.example.edu
    User shared_account
    IdentityFile ~/.ssh/id_ed25519
```

agent が秘密鍵の中身に触れることはありません。

**パスワードなしログインが必須です**（hpc-mcp は `BatchMode=yes` を強制し、パスワードを要求しません）。設定の前に BatchMode で鍵認証が使えることを確認してください。そうでないと、すべてのツール呼び出しが `Permission denied` で失敗します：

```bash
ssh -o BatchMode=yes my-hpc "echo OK"   # プロンプトなしで OK が返る必要があります
ssh-copy-id my-hpc                      # 失敗する場合は先に鍵認証を設定
```

既定は `StrictHostKeyChecking=yes` です。初回接続の前に手動で `ssh my-hpc` を実行し、ホストフィンガープリントを確認して `known_hosts` に登録してください。どうしても初回の自動登録が必要な場合は、設定で `ssh.strict_host_key_checking: accept-new` を指定します。

### 接続のセルフチェック

```bash
hpc-mcp --host my-hpc --root /home/shared_account/alice --check
```

## MCP クライアントへの組み込み

### 推奨：1 コマンドでの自動登録（`hpc-mcp mcp-add`）

インストール後、`mcp-add` で hpc-mcp を Codex と Reasonix の設定に自動登録できます。書き込まれるのは**移植可能な起動スクリプトのパス**（`<repo>/scripts/hpc-mcp-run`）で、このスクリプトがそのマシンの hpc-mcp を自動的に見つける（conda/venv/PATH）ため、特定マシンの conda パスに**依存しません**。別のマシンでは clone + インストールをやり直すだけです：

```bash
# リポジトリ内で実行（リポジトリの scripts/hpc-mcp-run を見つけます）
cd ~/git_repo/HPC-MCP
hpc-mcp mcp-add --config ~/.config/hpc-mcp/192.168.10.10.yaml

# または直接引数を渡す
hpc-mcp mcp-add --host my-hpc --user shared_account --root /home/shared_account/alice
```

結果：

- `~/.codex/config.toml` に `[mcp_servers.hpc]` が書き込まれる（command は `scripts/hpc-mcp-run` を指す）
- `~/.reasonix/config.toml` に hpc plugin が書き込まれる（同じく起動スクリプトを指す）
- 起動スクリプトは hpc-mcp を順に探します：`$HPC_MCP_BIN` → PATH → 一般的な conda/venv のパス。見つからないときは黙って失敗せず、明確なメッセージを出します
- hpc のセクションを追加／更新するだけで、既存の他の MCP server / provider 設定は**壊しません**
- 冪等：繰り返し実行しても重複したセクションはできません

変更後は **Codex / Reasonix を再起動**してください。

### CC-Switch（MCP 設定マネージャ）

[CC-Switch](https://github.com/farion1231/cc-switch) は複数の MCP server 設定を JSON で管理し、ワンクリックで切り替えられます。完全な stdio JSON 設定（`command` + `env`）、フィールドの説明、よくある質問は **[`docs/ja/CC_SWITCH.md`](docs/ja/CC_SWITCH.md)** にあります：

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

### プロジェクト単位の `.mcp.json`（最も移植性が高い）

リポジトリには `.mcp.json` のテンプレート（MCP 標準のプロジェクト単位設定）が含まれます。ここに `hpc` エントリを追加し、command を `./scripts/hpc-mcp-run` に向けます。プロジェクト単位の MCP に対応したクライアント（リポジトリのディレクトリから起動した Codex/Reasonix など）は自動的に読み込みます。host/root などは環境変数（`${HPC_MCP_HOST}` など。shell profile で定義）から注入します。別のマシンへの移行は、リポジトリを clone → hpc-mcp をインストール → 環境変数を定義 → リポジトリのディレクトリからクライアントを起動、それだけです。

### 手動での設定（任意）

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

どちらも stdio の argv 方式で起動し、シェルは介しません。手動方式では、`hpc-mcp` が PATH にない場合に絶対パス（例：`/path/to/conda/envs/hpc-mcp/bin/hpc-mcp`）へ置き換える必要があります。

## ツール一覧（22 個）

> **各ツールの引数、既定値、制約、返却値の要点**は **[docs/ja/TOOLS.md](docs/ja/TOOLS.md)** にあります。以下の表は索引です。

### 低レベルのプリミティブ

| ツール | 説明 | annotations |
|---|---|---|
| `hpc.info` | 接続／クラスタ情報（ローカルパスは公開しない） | readOnly |
| `hpc.files.list` | ディレクトリ列挙（有界ページング、recursive は階層ごと + cursor） | readOnly |
| `hpc.files.read` | ファイル読み取り（bounded slice、1 回 ≤256KiB） | readOnly |
| `hpc.files.write` | ファイル書き込み（expected_size/mtime/hash による楽観的並行性保護） | destructive |
| `hpc.files.mkdir` | ディレクトリ作成 | — |
| `hpc.files.delete` | 削除 | destructive |
| `hpc.files.upload` | ローカルからのアップロード（SFTP） | destructive |
| `hpc.files.download` | ローカルへのダウンロード（SFTP） | readOnly |
| `hpc.shell.run_safe` | 許可リストの軽量コマンド（command cost 予算付き） | readOnly |
| `hpc.slurm.submit` | 計算ジョブの投入 | openWorld |
| `hpc.slurm.status` | ジョブの状態 | readOnly |
| `hpc.slurm.queue` | 自分のジョブキュー | readOnly |
| `hpc.slurm.output` | ジョブの stdout/stderr | readOnly |
| `hpc.slurm.cancel` | ジョブの取り消し | destructive |
| `hpc.slurm.accounting` | sacct の会計情報 | readOnly |
| `hpc.jobs.wait` | ジョブ完了の待機（上限あり） | readOnly |

### 高レベルの agent ツール

| ツール | 説明 | annotations |
|---|---|---|
| `hpc.files.search` | 予算付きの正規表現検索（ログのエラー行の特定） | readOnly |
| `hpc.jobs.diagnose` | 状態 + 会計 + ログ末尾 + エラーシグネチャを 1 回で診断 | readOnly |
| `hpc.jobs.wait_and_diagnose` | ジョブの終了を待って 1 回で診断 | readOnly |
| `hpc.project.snapshot` | 1 回でプロジェクトのコンテキストを構築（ディレクトリ概観 + git + ジョブ） | readOnly |
| `hpc.cluster.topo` | 計算ノードのハードウェアトポロジ（CPU モデル/SIMD/NUMA/cache）+ 並列パラメータの提案 | readOnly, openWorld |
| `hpc.job.run` | runtime profile による高レベル投入（julia/python/moose/bash） | openWorld |

### 呼び出し例

Julia ジョブを投入します（パーティションは `hpc.info` が返す許可リストから選べます。省略すると設定の先頭が使われます）：

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

引数の既定値はサーバ設定、または `.sh` スクリプトの `#SBATCH` 指令から取られます（明示した引数が優先）。パーティションは設定の許可リストに一致しないと拒否されます。`.sh` のジョブスクリプトのパスを直接投入することもできます：

```json
{
  "tool": "hpc.slurm.submit",
  "arguments": {
    "command": "/home/shared_account/alice/proj/run.sh"
  }
}
```

拒否された場合は、実行可能な情報が返ります：

```text
Operation denied.

Reason:
'julia' is a computational/build workload and is forbidden on HPC login nodes.

Use:
hpc.slurm.submit
```

## 並列最適化パラメータ（`hpc.cluster.topo`）

ログインノードの許可リストは**意図的に** `lscpu`/`numactl` を通さず、`/proc` もパスサンドボックスの外にあります。そのため CPU モデル、SIMD 命令セット、NUMA トポロジといったパラメータは計算ノードでしか取得できません。`hpc.cluster.topo` はこの一連の流れを 1 回の呼び出しにまとめます：

```json
{
  "tool": "hpc.cluster.topo",
  "arguments": { "partition": "thcp1" }
}
```

- **初回呼び出し**（またはキャッシュ失効、`refresh: true`）は**1 CPU の収集ジョブ**を投入し、計算ノード上で `lscpu` / `/proc/cpuinfo` / `numactl --hardware` / `/proc/meminfo` を読み、ログインノードの `sinfo` のパーティションビューと統合します。
- **返却値**：CPU モデルと sockets/cores/threads、SIMD 命令セット（AVX2/AVX-512/SVE）、NUMA ドメインと距離行列、キャッシュ階層、ノードメモリ、パーティションの features/GRES、そして導出した並列パラメータの提案（`ntasks_per_node`、`cpus_per_task`、`--hint=nomultithread`、`--cpu-bind=cores`、`OMP_NUM_THREADS`、`-map-by numa`、推奨 `--mem`）と、各提案の根拠、および「物理コア vs 論理コア」のトレードオフ。
- **キャッシュ**：`topology.cache_ttl_seconds`（既定 24h）のあいだはプロセス内キャッシュ、またはリモートの `$ROOT/.hpc-mcp/topo/topology_<partition>.json` が使われ、以後のセッションが同じマシンで何度もキューに並ぶことはありません。
- **キュー待ち**：収集ジョブがまだスケジュールされていない場合は `status: "pending"` + `job_id` を返します。再度呼ぶとそのジョブが再利用され、重複投入はされません。
- **安全性**：収集スクリプトは**サーバ側で固定された内容**で（agent の引数はスクリプトに入りません）、かつ**キューを照会しません**（`squeue`/`sacct`/`scontrol`）。ノードローカルのハードウェア情報だけを読み、共有アカウントの分離ルールに従います。ジョブ所有権、パーティション許可リスト、同時実行上限は通常どおり有効です。
- 管理者は `topology.enabled: false` でこのツール全体を無効化できます（収集ジョブを一切投入しません）。

## 推奨ワークフロー（agent）

1. `hpc.info` で環境を把握 → 2. `hpc.project.snapshot` でプロジェクトのコンテキストを 1 回で構築 →
3. 並列性／性能が関わる場合は `hpc.cluster.topo` で CPU/SIMD/NUMA と推奨並列パラメータを 1 回で取得 →
4. `hpc.files.search` でまず位置を特定し（ログのエラー行など）、`hpc.files.read` で小さなコンテキストを読む →
5. `hpc.files.write` でリモート編集 → 6. ビルド／テスト／計算はすべて `hpc.slurm.submit` →
7. `hpc.jobs.diagnose` で失敗ジョブを 1 回で診断 → 8. 分析、修正、繰り返し。

原則：**1 回の高レベル呼び出しで済むものを、複数の低レベル呼び出しに分解しないこと。** `hpc.files.read` は bounded slice であり、offset=0 から EOF まで読まないこと。繰り返しの読み取り専用クエリはサーバ側の TTL キャッシュで重複排除されます。

詳細は [`skills/hpc-development/SKILL.md`](skills/hpc-development/SKILL.md) を参照してください。

## セキュリティテスト

```bash
python -m pytest tests/ -q
```

対象：パス経路の横断、シンボリックリンクの脱出、コマンドインジェクション、login/compute の境界、Slurm リソースの濫用、ジョブの分離。

検出事項、修正、残るリスクの完全な記録は [`docs/ja/SECURITY_REVIEW.md`](docs/ja/SECURITY_REVIEW.md)、モジュール境界とリクエストの流れは [`docs/ja/ARCHITECTURE.md`](docs/ja/ARCHITECTURE.md) にあります。

## 制限（v1 で明示的に扱わないもの）

任意のリモートシェル、任意の SSH ホスト、sudo、ポート転送、マルチホスト、リモート常駐 daemon、HTTP MCP、自動資格情報管理。

## トラブルシューティング

- **起動時に "No HPC host/root configured"**：3 つの設定方法のいずれかで host と root を提供してください。
- **すべてのツールが "No Slurm partitions are allowed" で DENY**：`slurm.allowed_partitions` を設定してください（既定は空で fail-closed）。
- **SSH 255 エラー**：まず `hpc-mcp ... --check` で確認し、`~/.ssh/config` と BatchMode の鍵認証が使えることを確かめてください。
- **ログ**：stderr に出力されます（stdout は MCP プロトコル専用）。`--log-file` でファイルに追記できます。

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [docs/ja/QUICKSTART.md](docs/ja/QUICKSTART.md) | 手順どおりのインストール + 各パラメータの説明 + 切り分け表 |
| [docs/ja/CONFIGURATION.md](docs/ja/CONFIGURATION.md) | すべての YAML キー、既定値、範囲、環境変数 |
| [docs/ja/TOOLS.md](docs/ja/TOOLS.md) | 22 個のツールの引数／既定値／制約 |
| [docs/ja/CC_SWITCH.md](docs/ja/CC_SWITCH.md) | CC-Switch の JSON 設定、フィールド説明、FAQ |
| [docs/ja/ARCHITECTURE.md](docs/ja/ARCHITECTURE.md) | モジュール境界、リクエストの流れ、分離の層 |
| [docs/ja/SECURITY_REVIEW.md](docs/ja/SECURITY_REVIEW.md) | 検出事項、修正、検証、残るリスク |
| [SECURITY.ja.md](SECURITY.ja.md) | 脅威モデルと強制される境界 |

## ライセンス

MIT。[LICENSE](LICENSE) を参照してください。
