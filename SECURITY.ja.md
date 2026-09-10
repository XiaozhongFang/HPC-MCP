# Security Policy

HPC-MCP のセキュリティモデル：**重要な境界はすべて MCP サーバの決定的なコードで強制され**、Skill、システムプロンプト、ツール説明の正しさには依存しません。

実装上の境界とリクエストの流れは [`docs/ja/ARCHITECTURE.md`](docs/ja/ARCHITECTURE.md)、今回のレビュー記録は [`docs/ja/SECURITY_REVIEW.md`](docs/ja/SECURITY_REVIEW.md) を参照してください。

他の言語：[English](SECURITY.md) | [简体中文](SECURITY.zh-CN.md) | [한국어](SECURITY.ko.md) | [繁體中文](SECURITY.zh-TW.md)

## 脅威モデル

- HPC は**共有アカウント**を使います（1 つの SSH アカウントを複数人で共有）。
- Agent（LLM）は誤った、曖昧な、あるいは注入された指示を受け取る可能性があります。
- 目標：agent が逸脱しても、権限を越えられないこと。

## 強制される境界

### 1. パスサンドボックス

- すべてのリモートパスはまず字句的に正規化されます（`posixpath.normpath`。`..`、チルダ、NUL/CR/LF、相対パスは拒否）。
- 次に**既存のパス**または**最も近い既存の親**に対してリモート `realpath` で canonical 化し、シンボリックリンクによる脱出を遮断します。
- いずれかの手順で不確実な場合は拒否します。
- リモート root は専用ディレクトリでなければならず、`/` には設定できません。ローカル転送には別途 `local_root` サンドボックス（既定は起動ディレクトリ）があり、シンボリックリンク、`.ssh`、および一般的な秘密鍵ファイル名を拒否します。

**許可されないもの**：`$HPC_MCP_ROOT` の外にあるすべてのパス（他の共有アカウント利用者のディレクトリ、`/etc`、`/tmp`、システムディレクトリを含む）、シンボリックリンクを介した脱出、root の外への rename/copy、root 自体の削除。

### 2. ログインノードのコマンドポリシー

- 許可リストにある軽量コマンドのみ実行できます（`ls`、`cat`、`grep`、`git status/diff/log`、`module list` など。設定で拡張可能）。`squeue`、`sacct`、`scontrol` は `hpc.shell.run_safe` から呼び出せません。ジョブ所有権の検査を回避されるのを防ぐためです。
- すべてのシェルメタ文字を拒否します：`;` `&&` `||` `|` `>` `>>` `<` `$( )` `` ` `` `${ }` `&`、改行など。
- 計算／ビルド系プログラムを拒否します：`julia`、`python`、`make`、`cmake`、`ninja`、`mpirun`、`srun`、コンパイラ、`pytest`、`matlab`、コンテナランタイムなど。
- 危険なプログラムを拒否します：`sudo`、`ssh`/`scp`/`rsync`、`curl`/`wget`、`nohup`/`setsid`/`tmux`、`kill`、`chmod`、`dd`、ネストしたシェル、`xargs`、`eval` など。
- `git` は読み取り専用サブコマンドに限定（`commit/push/pull/clone/-c/--exec-path/--git-dir` は拒否）。`module` は照会のみ。`find` は `-exec`/`-delete` を拒否。
- ローカルでもリモートでも `shell=True` は使いません。argv は `shlex.join` で再シリアライズされます。
- 解析に失敗した場合は拒否します。
- `cat`/`grep`/`find` などのパスオペランドはリモートで canonical 検査されます。`find -L/-H/-follow`、`ls -L`、`du -L` は拒否します。`env`/`printenv` はクリアされた最小環境でのみ実行されます。

### 3. Slurm リソースポリシー

- パーティションの許可リスト（既定は空 = すべて拒否）。
- ノード／CPU／メモリ／GPU／時間／同時実行の上限。超過すれば投入を拒否します。
- 作業ディレクトリは root 内でなければなりません。コマンド argv に制御文字を含められません。
- ジョブ出力は必ず `$ROOT/.hpc-mcp/jobs/<id>/` に書き込まれ、逸脱できません。

### 4. ジョブ所有権の分離

- 現在のサービスセッションが投入・登録した job のみ管理できます（tracked_jobs.json の `tool_session` が一致する必要があります）。再起動後の古いセッションのジョブは、既定では新しいインスタンスが引き継ぎません。
- **照会の分離**：`hpc.slurm.queue/status` はこのインスタンスが追跡する job ID に対してのみ `squeue -j <ids>` を実行します。追跡中のジョブがなければ即座に空を返し、アカウント全体の `squeue`/`sacct` を**決して**発行しません。共有アカウントにおける他利用者のジョブメタデータがこのプロセスに入ることはありません。
- 他利用者や他インスタンスのジョブを閲覧・取り消しすることはできません。
- この登録ファイルはアプリケーション層の分離であり、同一 Unix UID における強制アクセス制御ではありません。敵対的な共有アカウントには、独立した UID か特権付きリモートヘルパーが必要です。

### 5. クエリコストの境界（診断ツールと予算）

- `hpc.files.read` は **bounded slice** です：1 回の読み取りは `min(max_bytes, files.max_read_slice_bytes)`（既定 256 KiB）以下。ツール説明は agent を offset=0 から EOF まで導きません（大きなファイルの調査は `hpc.files.search` で位置を特定します）。
- `hpc.files.search` のすべての予算（`max_matches`/`max_context_lines`/`max_scan_bytes`/`max_files`/`max_depth`/`timeout`）はサーバ側でクランプされます。単一ファイルはまず `stat` して予算超過なら拒否します。リモートでは `grep -m` + `head` による打ち切り + `timeout` の保険が効きます。パターンは shlex でクォートされ、制御文字は拒否され、インジェクションを排除します。
- `hpc.files.list` の再帰列挙は 1 階層ずつページングし（`page_size` + `depth:N` カーソル）、リモート出力を `head` で打ち切り、`bash -o pipefail` で SIGPIPE を検出して打ち切りを正確に判定します。ツリー全体をスキャンしてから捨てることは決してありません。
- `hpc.jobs.diagnose` / `hpc.project.snapshot` の内部クエリはすべて有界で、予算付きです。
- 冪等な読み取り専用クエリは `tool + 正規化した引数` で重複排除されます（`cache_ttl_seconds`、既定 2s、0 で無効）。書き込み操作はキャッシュを無効化します。
- `hpc.info` はローカルのパス root（ユーザー名やプロジェクト名を含み得る）を返さず、能力のブール値とリソース上限のみを返します。
- `hpc.shell.run_safe` のすべての正当なコマンドは **command cost policy** により階層的にクランプされます：`ls`/`head`/`pwd` などは LOW（15s/256KiB）、`grep`/`cat`/`git diff` などは MEDIUM（30s/512KiB）、`find`/`du`/`sort`/`git grep` などは HIGH（10s/512KiB）。timeout と output はサーバ側で強制され、agent の自制に依存しません。
- `hpc.files.write` は **楽観的並行性保護** をサポートします：`expected_size`/`expected_mtime`/`expected_sha256` のいずれかが現在のファイル状態と一致しない場合、上書きを拒否します（fail-closed）。共有アカウントで仲間が直前に変更したコードを上書きしてしまうのを防ぎます。

### 6. SSH の境界

- 設定された単一のホストにのみ接続できます。BatchMode、StrictHostKeyChecking、接続タイムアウトが適用されます。
- 秘密鍵の内容は**読み取らず、出力しません**。`~/.ssh/config` での管理を推奨します。
- SSH/SFTP の出力はストリーミングのバイト上限付きです。タイムアウト時は子プロセスを kill して回収します。ControlMaster はサービス終了時に閉じられます。
- `hpc.ssh(command=...)` のような任意コマンドツールは提供しません。
- ポート転送なし、ProxyJump なし、マルチホストなし。

### 7. 資格情報とログ

- 秘密鍵、パスワード、トークンは決してログに書きません。監査ログは機密パターンをマスクし、値を切り詰めます。
- 監査フィールド：timestamp、tool、args（マスク済み）、decision(ALLOW/DENY)、reason、job_id、duration。
- マスキングはフィールド名に基づいて再帰的に行い（password/token/secret/private-key など）、制御文字を除去して切り詰めます。想定外の例外がそのまま agent に返ることはありません。

## 3 層の防御と残るリスク

| 層 | 機構 | 防ぐ対象 |
|---|---|---|
| Layer 1: MCP policy | パスサンドボックス、コマンド許可リスト、Slurm リソースポリシー、ジョブ所有権、クエリ予算、監査 | 振る舞いの壊れた agent |
| Layer 2: Slurm | パーティション許可リスト、リソース上限、同時実行上限、唯一の `sbatch` 入口 | 計算資源の濫用 |
| Layer 3: OS/クラスタ | 独立した Unix UID / Slurm のジョブ分離 + filesystem ACL / コンテナサンドボックス / 特権付きリモートヘルパー | **悪意あるコードの分離**（任意） |

**残るリスク**：共有 Unix UID のもとでは、MCP は *agent が逸脱しないこと*（Layer 1/2）しか保証できません。計算ノードに投入された悪意あるコードが、その UID が読めるデータにアクセスしないことは**保証できません**（Layer 3）。`hpc.slurm.submit` は本質的に「その Unix UID としてプログラムを実行する」ことです。Python の正規表現やポリシーで「悪意あるコードをふるい落とす」ことは避けてください。それは偽りの安心を生みます。真正の悪意あるコードの分離は、Layer 3 の OS レベルの機構に依存しなければなりません。この点は README / QUICKSTART で利用者に明示する必要があります。

## 明示的に実装しないもの（v1）

任意のリモートシェル、任意の SSH ホスト、sudo、リモートポート転送、ジョブの移行、マルチホスト SSH、HPC 常駐デーモン、リモート HTTP MCP、自動アカウント切り替え、自動資格情報管理、`~/.ssh/config` / `authorized_keys` の変更。

## 失敗時の安全（fail-closed）

設定の不足、SSH の異常、パスの解決不能、コマンドの解析失敗、Slurm パラメータの解析不能、パーティション／ホストの不確実性——いずれも **DENY** とし、無制限のシェルにフォールバックすることはありません。

## セキュリティ問題の報告

リポジトリの Issue を通じて非公開で報告するか、メンテナに連絡してください。未修正の詳細を公開の場で開示しないでください。
