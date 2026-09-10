# セキュリティレビュー（2026-09-04）

他の言語：[English](../SECURITY_REVIEW.md) | [简体中文](../zh-CN/SECURITY_REVIEW.md) | [한국어](../ko/SECURITY_REVIEW.md) | [繁體中文](../zh-TW/SECURITY_REVIEW.md)

## 検出事項と修正

| 深刻度 | 領域 | 検出事項 | 修正 |
| --- | --- | --- | --- |
| Critical | ファイル転送 | アップロード／ダウンロードが任意のローカルパスを許可し、シンボリックリンクの宛先や資格情報ファイルも含まれていた。 | `local_root`、構成要素の検査、資格情報ファイル名の拒否リスト、バッチ転送時の制御文字拒否、ダウンロード後のサイズ上限。 |
| Critical | Safe shell | 字句上 root 内にあるシンボリックリンクにより `cat`/`grep` がリモートサンドボックス外を読めた。実行ファイルのパスと follow 系オプションがコマンド面をさらに広げていた。 | オペランドに対するリモート canonical 検査、実行ファイルは basename のみを許可するポリシー、`-L/-H/-follow` の拒否。 |
| High | ジョブ追跡 | 保存された `job_dir` とセッションをまたぐエントリが信頼され、別セッションのジョブに対する output/accounting/cancel が可能だった。 | セッションに紐づくスキーマ検証と、数値 job ID から導出するパス。canonical なメタデータディレクトリとロック付きの原子的書き込み。 |
| High | リソース枯渇 | SSH/SFTP が無界の `communicate()` バッファリングを使い、過大なコマンド／環境変数のペイロードも受け付けていた。 | ストリーミングのバイト上限、子プロセスの回収、有界な argv/environment/スクリプトサイズ、検証済みのタイムアウト。 |
| High | 設定 | 注入された `environ` が無視され、不正なネスト YAML と負の上限が実行時エラーや不正なポリシー状態として漏れていた。 | 決定的な環境の出所、型付きのセクション／上限検証、canonical な root、安全なホスト鍵モードのみの許可。 |
| Medium | 監査 | 正規表現のみのマスキングでは、辞書のキーにある秘密（`{"token": "..."}` など）や制御文字を見落としていた。 | 再帰的でキー名を考慮したマスキング、制御文字のエスケープ、切り詰め。 |
| Medium | 並行性 | 並行する submit 呼び出しがアクティブジョブの検査を同時に通過し得た。 | 非同期の投入ロックと、原子的なメタデータロックディレクトリ。 |
| Low | 保守性 | 未使用のヘルパーと import がセキュリティ境界を曖昧にしていた。 | デッドコードを削除し、policy/service/transport の責務を明文化。 |

## 検証

回帰テストはパス経路の横断、シンボリックリンクの脱出、コマンドインジェクション、login/compute の分離、
Slurm の上限、ジョブ所有権、設定、ファイルサービスを網羅します。実行：

```bash
python -m compileall -q src
python -m pytest -q
git diff --check
```

静的検査は、利用可能であれば Bandit/Ruff を追加し、デプロイ環境で `python -m pip check` も実行してください。

## 運用上の要件

可能な限り `StrictHostKeyChecking=yes` を使い、`known_hosts` を事前に用意してください。
`HPC_MCP_ROOT` は対象ユーザーだけが扱えるようにし、`HPC_MCP_LOCAL_ROOT` は転送に必要な最小の
ローカルプロジェクトディレクトリに設定します。新しいプロセスセッションは、より古いセッションが登録した
ジョブを管理できません。これはアプリケーション層の境界です。同等の Unix アカウントは依然として
JSON 追跡ファイルを改ざんしたり、リモートパスの競合を起こしたりできるため、同一アカウントでの
敵対的なデプロイには独立した Unix アカウントか特権付きリモートヘルパーが必要です。

---

# セキュリティ + 効率レビュー（v0.2、2026-09）

## 範囲

今回のラウンドは、既存の境界を一切弱めることなく、サーバを「agent の逸脱を防ぐ」から
「agent が最小のリモート I/O で最大の価値を得られるようにする」へ引き上げました。
以下の検出事項はすべて今回修正され、回帰テストでカバーされています。

## 検出事項と修正

| 深刻度 | 領域 | 検出事項 | 修正 |
| --- | --- | --- | --- |
| Critical | 共有アカウントの Slurm | `_queue_states()` / `queue()` がアカウント全体の `squeue` を実行して Python 側で絞り込んでいたため、他ユーザーのジョブメタデータが MCP プロセスに入っていた。 | すべての所有権検査が `squeue -j <tracked ids>` のみを実行するようになりました（500 件ずつバッチ）。追跡中のジョブがなければ `squeue` は一切実行されません。新しい `tests/security/test_shared_account_isolation.py` が、他者の行が結果に到達しないことと、`-j` なしの照会が決して発行されないことを証明します。 |
| High | ファイル読み取り | ツールの説明がログ全体を `offset=0` から EOF までページングするよう促し、各 `read` が stat+dd+base64 の往復になっていた。 | `hpc.files.read` は `files.max_read_slice_bytes`（既定 256 KiB）で上限を設けた bounded slice になり、説明はまず `hpc.files.search` を使うよう導きます。 |
| High | ディレクトリ一覧 | 再帰 `find` がツリー全体をストリームし、Python が事後に切り詰めていた。 | `hpc.files.list` は 1 回のリモート呼び出しで 1 階層を列挙し、`head` でリモート出力を打ち切り（`bash -o pipefail` による SIGPIPE 検出）、`depth:N` カーソルで再開し、`max_depth`（既定 3）をクランプします。 |
| High | ログ診断 | Agent が `status -> output -> accounting -> read` の列を組み立てる必要があった（ツールのスラッシング）。 | 新しい `hpc.jobs.diagnose`（状態 + 会計 + 有界な末尾 + エラーシグネチャ）と `hpc.jobs.wait_and_diagnose`。内部のすべてのクエリは自所有ジョブに限定され、上限付きです。 |
| High | 検索 | 第一級の有界検索がなく、agent が `grep` パイプラインを即席で組んでいた。 | 新しい `hpc.files.search`：単一ファイルは stat して過大なら拒否、ツリーは `grep -rn` + 除外ディレクトリ + `-m` + `head` + `timeout`。すべての予算はサーバ側でクランプされ、パターンはクォートされた argv として渡ります（インジェクションなし）。 |
| Medium | クエリの重複排除 | 同一の status/list 呼び出しの繰り返しが毎回 SSH を再実行していた。 | `QueryCache`（TTL `cache_ttl_seconds`、既定 2 s、0 で無効）が冪等な読み取り専用ツールを正規化した tool+args で重複排除します。書き込み操作はキャッシュ全体を無効化します。 |
| Medium | コマンドコスト | 正規のコマンド（`find`/`du`/`sort`/`git grep`）がログインノードを飽和させ得た。 | `security/command_cost.py` がすべての safe コマンドを階層化（LOW 15s/256KiB、MEDIUM 30s/512KiB、HIGH 10s/512KiB）。`SafeExec` で強制され、`cost_tier`/`timeout_seconds` を返します。 |
| Medium | 応答の漏えい | 監査のマスキングは *agent への応答* を保護していなかった（例：ジョブの stderr にある `password=`）。 | `response_redactor.py` が `files.read/search`、`slurm.output`、`shell.run_safe`、`diagnose`、`snapshot` で明白な秘密（`password=`/`token=`/`api_key`/`Bearer`/AWS/PEM ブロック）をマスクします。パターンは最小限に留め、科学技術系のログ内容を壊しません。 |
| Medium | 情報露出 | `hpc.info` が生の `local_roots` パス（ユーザー名やプロジェクト名を含み得る）を返していた。 | `workspace_available`/`transfer_enabled` のブール値に置き換え。回帰テストがローカルのユーザー名が決して現れないことを表明します。 |
| Medium | 書き込み競合 | 他プロセスが変更したファイルの上書きが、作業を静かに破壊し得た。 | `hpc.files.write` が `expected_size`/`expected_mtime`/`expected_sha256` を受け付けます。不一致（またはファイル欠落）は fail-closed で書き込みを拒否します。 |
| Low | 高レベル実行 | エキスパートと agent が 1 つの生 argv 投入面を共有していた。 | 新しい `hpc.job.run` が固定の runtime profile（julia/python/moose/bash）から argv を組み立て、投入ポリシーを完全に再利用します。`hpc.slurm.submit` はエキスパート向けに残ります。 |

## 新しいセキュリティ回帰スイート（今回）

- `test_shared_account_isolation.py` —— アカウント全体の `squeue` が存在しないこと。
  他者のジョブが結果に到達しないこと。追跡対象がなければスキャンしないこと。
  他者の cancel/accounting/output が拒否されること。
- `test_files_search.py` —— 検索予算、サンドボックス脱出、制御文字とインジェクションの拒否、
  `-m`/`head` の上限、コンテキストの解析。
- `test_command_cost.py` —— 階層の分類、git サブコマンドの細分化、
  `SafeExec` で強制されるタイムアウト／出力のクランプ。
- `test_response_redaction.py` —— password/token/API key/Bearer/AWS/PEM のマスク、
  通常のログ内容の保持、有界な再帰。
- `test_info_minimal_exposure.py` —— `hpc.info` がローカル root と SSH の詳細を隠すこと。
- `test_cache.py` —— 重複排除キー、TTL の失効、サイズ上限、書き込みによる無効化。
- `test_project_snapshot.py` —— 有界なツリー、git サマリ、自所有ジョブのみ。
- `test_files_service.py` の追加分 —— 読み取りスライスの予算、list のページング／カーソル、
  書き込みの並行保護。
- `test_slurm_manager.py` の追加分 —— diagnose の集約と上限、wait_and_diagnose、
  job.run のポリシー再利用。

## 残るリスク（変更なし、3 層として明示）

Layer 1（MCP policy）と Layer 2（Slurm リソースポリシー）は*振る舞いの壊れた agent*を
無害化しますが、共有 Unix UID で計算ノード上を実行する*悪意あるコード*を分離することはできません。
`hpc.slurm.submit` が与えるのは、まさにその UID の権限です。その脅威モデルには Layer 3
（独立した Unix UID、Slurm のジョブ分離 + filesystem ACL、コンテナ／サンドボックス、
または特権付きリモートヘルパー）が必要です。「明らかに悪意がある」プログラムを正規表現や
ポリシーでフィルタすることは、OS レベルの分離機構として明示的に扱いません。参照：
`README.md` / `SECURITY.md` / `docs/ARCHITECTURE.md`。
