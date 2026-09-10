# MCP ツールリファレンス

他の言語：[English](../TOOLS.md) | [简体中文](../zh-CN/TOOLS.md) | [한국어](../ko/TOOLS.md) | [繁體中文](../zh-TW/TOOLS.md)

本ドキュメントは、HPC-MCP が agent に公開する **22 個のツール**の用途、引数、既定値、制約、返却値の要点を説明します。これは [README.ja.md](../../README.ja.md) の「ツール一覧」の展開版です：README は*どのツールがあるか*に、本ファイルは*各引数をどう埋め、何によってクランプされるか*に答えます。

- **設定パラメータ**（host/root/パーティション許可リスト/リソース上限…）は [CONFIGURATION.md](CONFIGURATION.md)。
- **コマンドライン引数**（`--host`/`--root`/`--check`…）は [QUICKSTART.md](QUICKSTART.md) の第 4 節。
- **Agent の行動指針**（どのツールをいつ使うか）は [skills/hpc-development/SKILL.md](../../skills/hpc-development/SKILL.md)。

---

## 共通の約束事（すべてのツールに適用）

| 約束事 | 説明 |
|---|---|
| パスサンドボックス | すべてのリモートパスは**絶対パス**で `$HPC_MCP_ROOT` の内側でなければなりません。`..`、root 外に出るシンボリックリンク、`/etc` などは一律拒否されます。ローカルパスは `local_roots`（既定 = プロセスの作業ディレクトリ + `/tmp`）の内側でなければならず、`.ssh`、秘密鍵、シンボリックリンクは拒否されます。 |
| サーバ側のクランプ | すべての「予算」引数（バイト数、件数、深さ、タイムアウト）は**要求値 ≤ サーバ上限**です。上限は CONFIGURATION.md にあります。0 や負の値は拒否されます（fail-closed）。 |
| ページング | 再帰一覧は `page_size` + `cursor` で 1 階層ずつ進み、ツリー全体をスキャンしません。読み取りは `offset` + `next_offset` で継続します。 |
| クエリの重複排除 | 冪等な読み取り専用ツールは `tool + 引数` で `cache_ttl_seconds`（既定 2 s）のあいだ重複排除され、繰り返し呼んでも SSH の往復は発生しません。書き込み操作はキャッシュを消去します。 |
| 応答のマスキング | ログやファイル内容中の `password=`、`token=`、`Bearer`、AWS 資格情報、PEM 秘密鍵ブロックなどは `[REDACTED]` に置き換えられます。科学技術系のログ内容は影響を受けません。 |
| 失敗即拒否 | 設定の欠落、パスの解決不能、コマンドの解析失敗、パーティションの不確実性、SSH の異常——あらゆる不確実な状況は **DENY** となり、実行可能な代替案（`Reason:` + `Use:`）が示されます。 |
| annotations | `readOnly` = リモート状態を変更しない、`destructive` = 削除／上書きの可能性がある、`idempotent` = 繰り返し呼んでも追加の副作用がない、`openWorld` = クラスタの計算側に到達する（ジョブを投入する）。 |

---

## 1. 環境とプロジェクトのコンテキスト

### `hpc.info`

接続とクラスタ能力の概要。**引数なし。**

返却：`slurm_available`、`cluster`、`working_root`、`workspace_available`、`transfer_enabled`、`allowed_partitions`（投入可能なパーティションの許可リスト）、`max_cpus`/`max_nodes`/`max_memory_mb`/`max_gpus`/`max_time`。

> セキュリティのため、SSH の host/user/port とローカルのパス root は**返しません**（agent が MCP を迂回して直接接続するのを防ぐため）。
> リソースを要求する前に、ここで上限を確認してください。

### `hpc.project.snapshot` — `readOnly, idempotent`

1 回の呼び出しでプロジェクトのコンテキストを構築します：浅いディレクトリ概観 + 主要なソース／ログファイルのサイズ + 読み取り専用の git 状態 + このプロジェクトで追跡中のジョブ。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | `$HPC_MCP_ROOT` 内の絶対パス |
| `depth` | int | — | `2` | 列挙の深さ。サーバ側で `files.max_recursive_depth` によりクランプ |
| `include_git` | bool | — | `true` | 読み取り専用の git サマリ（`git status` など）を含めるか |
| `include_jobs` | bool | — | `true` | このプロジェクトで追跡中のジョブを含めるか |

> 1 回の `snapshot` が `list` + `read` + `git status` + `queue` の複数往復を置き換えます。

---

## 2. リモートファイル

### `hpc.files.list` — `readOnly, idempotent`

ディレクトリを列挙します。再帰時は**1 階層ずつページング**します。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | ディレクトリの絶対パス（root 内） |
| `recursive` | bool | — | `false` | 再帰列挙（階層ごとに進む） |
| `max_entries` | int | — | サーバの `files.max_list_entries`（2000） | 1 回の返却件数のハード上限 |
| `page_size` | int | — | `max_entries` と同じ | 1 ページあたりの件数 |
| `cursor` | string | — | — | 前ページが返した `next_cursor`。再帰列挙を続けるために使う |
| `max_depth` | int | — | サーバの `files.max_recursive_depth`（3） | 再帰の深さ上限 |

### `hpc.files.read` — `readOnly, idempotent`

ファイルの 1 スライスを読み取ります（**bounded slice**、1 回あたり `files.max_read_slice_bytes` 以下、既定 256 KiB）。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | ファイルの絶対パス（root 内） |
| `max_bytes` | int | — | サーバの `files.max_read_bytes`（1 MiB） | 今回の読み取りバイト上限。スライス上限でもクランプされる |
| `offset` | int | — | `0` | 開始バイトオフセット |

返却：`size`、`bytes`、`truncated`、`end_of_file`、`next_offset`、`content`（UTF-8、不正バイトは置換）。

> 大きなログを調査するときは、まず `hpc.files.search` で行を特定し、その `offset` から小さく読んでください。
> **`offset=0` から EOF まで読み続けないでください。**

### `hpc.files.search` — `readOnly, idempotent`

予算付きの正規表現検索（POSIX ERE）。「まず位置を特定し、次に精読する」ための機能です。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | ファイルまたはディレクトリの絶対パス |
| `pattern` | string | ✅ | — | 拡張正規表現（単一のクォートされた引数として渡され、制御文字は拒否） |
| `max_matches` | int | — | `files.search_max_matches`（200） | 最大マッチ数 |
| `context_lines` | int | — | `files.search_max_context_lines`（10） | 各マッチに付けるコンテキスト行数 |
| `max_scan_bytes` | int | — | `files.search_max_scan_bytes`（64 MiB） | 1 ファイルあたりのスキャン上限 |
| `max_files` | int | — | `files.search_max_files`（1000） | ツリー内でスキャンする最大ファイル数 |
| `max_depth` | int | — | `files.search_max_depth`（6） | ツリーの深さ上限 |
| `timeout` | int | — | `files.search_timeout`（5 s） | リモート検索のタイムアウト |

### `hpc.files.write` — `destructive`

ファイルを書き込みます。**楽観的並行性保護**付き（共有アカウントで、他人が直前に変更したコードを上書きしないため）。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 対象の絶対パス |
| `content` | string | ✅ | — | テキスト内容（全体の上限は `files.max_write_bytes`、既定 10 MiB） |
| `append` | bool | — | `false` | 上書きではなく追記 |
| `expected_size` | int | — | — | 現在のバイト数の期待値。一致しなければ**書き込みを拒否** |
| `expected_mtime` | int | — | — | 現在の mtime（epoch 秒）の期待値。一致しなければ拒否 |
| `expected_sha256` | string | — | — | 現在の SHA-256（64 桁の 16 進）の期待値。一致しなければ拒否 |

> 3 つの `expected_*` は自由に組み合わせられます。指定した場合はすべて一致する必要があり、ファイルが存在しない場合も拒否されます。

### `hpc.files.mkdir` — `destructive`

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | ディレクトリのパス |
| `parents` | bool | — | `false` | `mkdir -p` 相当（最も近い既存の祖先が root 内にあることを検証） |

### `hpc.files.delete` — `destructive`

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `path` | string | ✅ | — | 対象パス（root 自体の削除は不可） |
| `recursive` | bool | — | `false` | 再帰削除（`rm -rf`） |

### `hpc.files.upload` / `hpc.files.download`

SFTP 転送。**両端がサンドボックス化**されます（ローカル ∈ `local_roots`、リモート ∈ `$HPC_MCP_ROOT`）。

| ツール | 引数 | 型 | 必須 | 説明 |
|---|---|---|---|---|
| `hpc.files.upload`（destructive） | `local_path` | string | ✅ | ローカルのソースファイルパス |
| | `remote_path` | string | ✅ | 対象パス（root 内） |
| `hpc.files.download`（readOnly） | `remote_path` | string | ✅ | リモートのソースファイルパス |
| | `local_path` | string | ✅ | ローカルの対象パス（`local_roots` 内） |

> 両方のパスでシンボリックリンクを拒否します。ローカル側はさらに `.ssh`/`.gnupg` と秘密鍵のファイル名を拒否します。転送後にサイズ上限を検証します。

---

## 3. ログインノードの照会

### `hpc.shell.run_safe` — `readOnly`

ログインノードで**許可リストにある 1 つのコマンド**を実行します（`ls`/`find`/`cat`/`grep`/`head`/`tail`/`wc`/`sort`/`uniq`/`stat`/`du`/`df`/`git status|diff|log`/`module list|avail`/`sinfo`/`env` など）。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `command` | string | ✅ | — | 単一のコマンド。パイプ、`&&`、`;`、リダイレクト、`$()`、バッククォート、末尾の `&` は**使用できません** |
| `cwd` | string | — | `$HPC_MCP_ROOT` | 作業ディレクトリ（root 内、realpath 検証済み） |
| `timeout` | int | — | `shell.max_exec_seconds`（30 s） | コマンドコストの階層によってさらにクランプ |

制約（コードレベルで強制）：

- 計算／ビルド系プログラムは**実行できません**（`julia`/`python`/`make`/`cmake`/`mpirun`/`pytest`/`nvcc`…）→ `hpc.slurm.submit` を使ってください。
- `squeue`/`sacct`/`scontrol` は**実行できません** → 所有権検査付きの Slurm ツールを使ってください。
- パス形式の実行（`./prog`、`/usr/bin/julia`）、`find -exec`、`git -c`、ネストしたシェルは**使えません**。
- コマンドコストの階層：LOW（15 s / 256 KiB、`ls`/`sinfo` など）、MEDIUM（30 s / 512 KiB、`cat`/`grep` など）、HIGH（10 s / 512 KiB、`find`/`du`/`sort`/`git grep` など）。

返却：`exit_code`、`stdout`、`stderr`、`cost_tier`、`timeout_seconds`（タイムアウト時は `timed_out: true`）。

---

## 4. Slurm ジョブ

### `hpc.slurm.submit` — `openWorld`

計算ジョブを投入します（**唯一**の計算入口で、サーバ側のリソースポリシーに従います）。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `command` | array または string | ✅ | — | プログラムの argv（例：`["julia","--project=.","test/runtests.jl"]`）、または**単一の `.sh` スクリプトパス**（この場合 `#SBATCH` 指令が既定値になります） |
| `job_name` | string | — | `"job"` | ジョブ名（`[A-Za-z0-9_.-]` に正規化、64 文字以下） |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | ジョブの作業ディレクトリ（root 内、realpath 検証済み） |
| `partition` | string | — | 設定の許可リストの先頭 | `slurm.allowed_partitions` に一致する必要があります |
| `nodes` | int | — | `1` | ≤ `slurm.max_nodes` |
| `ntasks` | int | — | `1` | タスク数 |
| `cpus_per_task` | int | — | `1` | かつ `nodes × ntasks × cpus_per_task ≤ slurm.max_cpus` |
| `memory` | string | — | なし | 例：`"16G"`、`"8000"`（MiB）、`"2T"`。≤ `slurm.max_memory_mb` |
| `time_limit` | string | — | `slurm.max_time` | `HH:MM:SS`、`D-HH:MM:SS`、`MM:SS`、または分のみ。≤ `slurm.max_time` |
| `gpus` | int | — | `0` | ≤ `slurm.max_gpus` |
| `environment` | object | — | — | 追加の環境変数（名前は `[A-Za-z_][A-Za-z0-9_]*` に一致すること） |

挙動：スクリプトはサーバ側で生成され（`#SBATCH` はサーバが導出）、stdout/stderr は必ず `$HPC_MCP_ROOT/.hpc-mcp/jobs/<job-id>/` に取り込まれます。投入直後に `squeue -j`/`sacct -j` でジョブの所有権を相互確認し、確認できないジョブは登録されません。同時にアクティブなジョブ数は `slurm.max_concurrent_jobs` で制限されます。

### `hpc.job.run` — `openWorld`

**信頼された runtime profile** による簡易投入です。内部的には `hpc.slurm.submit` とまったく同じポリシーを通ります。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `runtime` | string | ✅ | — | 列挙：`julia`、`python`、`moose`、`bash`、`shell` |
| `script` | string | ✅ | — | スクリプトの絶対パス（root 内）。**moose** では実行ファイルのパスを渡す |
| `args` | array\<string\> | — | — | 追加 argv。**moose では必須**（`[input.i]` など）。実際のコマンドは `mpirun -np <ntasks> -- <script> <args…>` |
| `job_name` | string | — | runtime 名 | ジョブ名 |
| `working_directory` | string | — | `$HPC_MCP_ROOT` | 作業ディレクトリ |
| `partition` | string | — | 許可リストの先頭 | submit と同じ |
| `nodes` / `ntasks` / `cpus_per_task` / `memory` / `time_limit` / `gpus` / `environment` | — | — | `hpc.slurm.submit` と同じ | 意味と上限は submit と完全に同じ |

各 profile の実際のコマンド：`julia --project=. <script>`、`python <script>`、`bash <script>`、`moose` → `mpirun -np <ntasks> -- <script>`。

### ジョブの照会と管理（このインスタンスが投入したジョブのみ）

サーバは**自分が投入・登録した job ID だけ**を照会し（`squeue -j <ids>` / `sacct -j <ids>`）、共有アカウントのキュー全体をスキャンすることは決してありません。他のジョブの照会・取り消しは一律拒否されます。

| ツール | 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|---|
| `hpc.slurm.status`（readOnly,idempotent） | `job_id` | string | ✅ | — | 状態。squeue に記録がなければ `sacct` にフォールバック |
| `hpc.slurm.queue`（readOnly,idempotent） | — | — | — | — | このインスタンスのアクティブジョブ一覧（アクティブがない場合の空は正常） |
| `hpc.slurm.output`（readOnly,idempotent） | `job_id` | string | ✅ | — | ジョブログを読む |
| | `stream` | string | — | `"stdout"` | 列挙：`stdout`、`stderr` |
| | `tail_bytes` | int | — | `shell.max_output_bytes`（1 MiB） | ファイル末尾から何バイト取るか |
| `hpc.slurm.cancel`（destructive） | `job_id` | string | ✅ | — | 取り消し（このインスタンスが投入したジョブのみ） |
| `hpc.slurm.accounting`（readOnly,idempotent） | `job_id` | string | ✅ | — | `sacct` の会計：`elapsed`、`cpu_time_raw`、`max_rss`、`state`、`exit_code`、`node_list`、`alloc_cpus` |

### `hpc.jobs.wait` — `readOnly, idempotent`

ジョブが終了状態（`COMPLETED`/`FAILED`/`CANCELLED`/`TIMEOUT`/`OUT_OF_MEMORY`/`NODE_FAIL`/`PREEMPTED`）になるまでポーリングします。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | ジョブ ID |
| `timeout_seconds` | int | — | `wait_max_seconds`（3600） | 全体の待機上限。サーバ上限でもクランプ |
| `poll_interval` | int | — | `10` | ポーリング間隔（秒、サーバ側で 2–60 にクランプ） |

> タイムアウトしてもジョブは取り消されません。エラーを返し、ポーリングの継続を促します。

### `hpc.jobs.diagnose` — `readOnly`

1 回の呼び出しで診断を完結します：状態 + 会計 + stdout/stderr の末尾 + よくあるエラーシグネチャのスキャン。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `job_id` | string | ✅ | — | ジョブ ID |
| `stdout_lines` | int | — | `80` | stdout の末尾行数（上限 200。各行は 4096 バイトとして読み取り量を見積もる） |
| `stderr_lines` | int | — | `80` | stderr の末尾行数（上限 200） |
| `include_accounting` | bool | — | `true` | `sacct` の会計を含めるか |
| `include_error_scan` | bool | — | `true` | エラーシグネチャ（`oom`/`segfault`/`timeout`/`gpu_error`/`mpi_error`/`missing_file`）をスキャンするか |

> 1 回の `diagnose` が `status → output → accounting → read` の複数往復を置き換えます。

### `hpc.jobs.wait_and_diagnose` — `readOnly`

ジョブの終了を待ってから 1 回で診断します（すでに終了していれば即座に返ります）。

引数：`job_id`（必須）、`timeout_seconds`、`poll_interval`、`stdout_lines`、`stderr_lines`（意味と既定値は `wait` / `diagnose` と同じ）。

---

## 5. クラスタトポロジと並列パラメータ

### `hpc.cluster.topo` — `readOnly, idempotent, openWorld`

**計算ノードの実際のハードウェアパラメータ**と、そこから導出した並列最適化の提案を取得します：CPU モデル/vendor、`sockets × cores × threads`、SIMD 命令セット（AVX2/AVX-512/SVE）、NUMA ドメインと距離行列、キャッシュ階層、ノードメモリ、パーティションの features/GRES、および `recommended_parallel_parameters`（純 MPI / MPI+OpenMP ハイブリッド / 純 OpenMP の 3 種類。`ntasks_per_node`、`cpus_per_task`、`--hint=nomultithread`、`--cpu-bind=cores`、`-map-by numa`、`OMP_NUM_THREADS`、推奨 `--mem` とその根拠を含む）。

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `partition` | string | — | 許可リストの先頭 | 探索するパーティション。名前は `[A-Za-z0-9_.-]` で、`slurm.allowed_partitions` に一致すること |
| `refresh` | bool | — | `false` | 強制的に再収集（キャッシュを無視。キューに残る古い収集ジョブは先に取り消す） |

**呼び出しコストとキャッシュ**（既定値は `topology.*` 設定）：

- 初回呼び出し（またはキャッシュ失効、`refresh: true`）は**1 CPU の収集ジョブ**を投入します
  （既定の時間上限 `00:03:00`）。計算ノード上で `lscpu` / `/proc/cpuinfo` /
  `numactl --hardware` / `/proc/meminfo` / `/sys/.../cpu0/cache` を読み、
  ログインノードの `sinfo` によるパーティションビューと統合します。
- 結果は `topology.cache_ttl_seconds`（既定 24 h）のあいだキャッシュされます：プロセス内と、
  リモートの `$HPC_MCP_ROOT/.hpc-mcp/topo/topology_<partition>.json`。キャッシュヒット時は
  ジョブを**投入しません**。返却値の `source` が由来を示します（`probe-job` / `session-cache` / `remote-cache`）。
- 収集ジョブがまだキューにある場合（`topology.wait_seconds`、既定 300 s を超過）は
  `status: "pending"` と `collection_job_id` を返します。**再度呼ぶとそのジョブが再利用され**、重複投入はされません。

**安全上の制約**：収集スクリプトは**サーバ生成の固定内容**で、agent の引数が書き込まれることはありません。スクリプトはノードローカルのハードウェア情報のみを読み、`squeue`/`sacct`/`scontrol` を**呼びません**（共有アカウントの分離）。ジョブ所有権、パーティション許可リスト、同時実行上限は通常どおり適用されます。管理者は `topology.enabled: false` でこのツールを無効化できます。

**返却値の要点**：

| フィールド | 意味 |
|---|---|
| `cpu.model_name` / `cpu.vendor` / `cpu.architecture` | CPU モデル、ベンダー、アーキテクチャ |
| `cpu.sockets` / `cores_per_socket` / `threads_per_core` | トポロジの三要素 |
| `cpu.physical_cores` / `logical_cpus` | 物理コア数 / 論理 CPU 数（SMT 有効時は後者が大きい） |
| `cpu.simd.level` / `present` | ベクトル命令セットの階層（`avx512`/`avx2`/`avx`/`arm-neon`…）と検出されたフラグ |
| `cpu.cache_kib` / `cache_source` | L1d/L1i/L2/L3（KiB。`null` はその階層が不明）と取得元（`lscpu`/`sysfs`） |
| `numa.count` / `nodes` / `distances` | NUMA ドメイン数、各ドメインの CPU 一覧とサイズ、距離行列 |
| `memory_mib.mem_total` | ノードメモリ（MiB） |
| `slurm.partition` / `node_variants` | パーティションのサマリ（ノード数／ノードあたりのコアとメモリ／features/GRES）とハードウェアの変種（状態のヒストグラム付き） |
| `recommended_parallel_parameters` | 3 種類の並列パラメータ提案 + `notes`（各提案の根拠） |
| `collected_on_node` / `collection_job_id` | サンプルを取得したノードと収集ジョブ ID（追跡用） |

> 提案値は物理コア／NUMA ドメインから導出した**ヒューリスティック**な出発点であり、ベンチマークの結論ではありません。実際のチューニングには、異なる rank／スレッド数の組み合わせを比較する実行が必要です。

---

## 6. 典型的な呼び出し順序

```text
1. hpc.info                      # パーティション許可リストとリソース上限
2. hpc.project.snapshot          # プロジェクトのコンテキスト（ツリー/git/自分のジョブ）
3. hpc.cluster.topo              # 並列最適化の前に：CPU/SIMD/NUMA + パラメータ提案
4. hpc.files.search  →  hpc.files.read   # まず位置を特定し、次に小さく読む
5. hpc.files.write                # リモート編集
6. hpc.slurm.submit または hpc.job.run   # ビルド/テスト/計算
7. hpc.jobs.wait_and_diagnose     # 待機 + 1 回の診断
8. 分析 → 手順 5 に戻る
```
