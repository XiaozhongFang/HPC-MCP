# クイックスタートとトラブルシューティング（ハンズオン）

他の言語：[English](../QUICKSTART.md) | [简体中文](../zh-CN/QUICKSTART.md) | [한국어](../ko/QUICKSTART.md) | [繁體中文](../zh-TW/QUICKSTART.md)

このドキュメントは、hpc-mcp をゼロから動かせるようにするための手順と、各パラメータの説明、よくある「固まる」現象の原因をまとめたものです。

---

## 0. 1 分でわかる全体像

`hpc-mcp` は **MCP サーバ（stdio）** であり、単発のコマンドではありません。

- `hpc-mcp ...` で起動すると、`starting: ...` の 1 行を出力したあと、**入力待ちのまま止まります**。
  **これは正常です。** MCP クライアント（Codex / Reasonix）が標準入力からリクエストを送ってくるのを待っています。
  この時点では**まだ SSH 接続していない**ため、起動ログだけでは SSH が通るかどうか判断できません。
- 実際の SSH 接続は、クライアントが最初にツール（例：`hpc.info`）を呼び出したときに発生します。
- **設定とネットワークが通っているかを今すぐ確認したい**場合は `--check`（手順 3）を使います。能動的に 1 回 SSH 接続し、結果を出力します。

---

## 1. インストール

**専用の仮想環境にインストールすることを強く推奨します。** `pip install --user` でシステムの `~/.local` に直接入れると、すでにあるパッケージ（torch、httpcore など）と衝突し、`UNKNOWN-0.0.0` という中身のないパッケージや `pip's dependency resolver does not take into account...` の警告が出ます。

### 方法 A（推奨）：virtualenv による分離環境

システムに `python3-venv` がない場合は virtualenv を使います（ユーザー権限のみで完結、root 不要）。

```bash
# 1. virtualenv をインストール（1 回だけ）
python3 -m pip install --user virtualenv

# 2. プロジェクト専用環境を作成（1 回だけ、以後は使い回す）
python3 -m virtualenv ~/venvs/hpc-mcp

# 3. 有効化して hpc-mcp をインストール
source ~/venvs/hpc-mcp/bin/activate
cd ~/git_repo/HPC-MCP
pip install .

# 4. 使用
hpc-mcp --version        # hpc-mcp 0.1.0 と表示されれば OK
hpc-mcp --help
```

> `python3-venv` が入っている環境なら、標準ライブラリの
> `python3 -m venv ~/venvs/hpc-mcp` でも同じ手順で構いません。

インストール後に `hpc-mcp` が見つからない場合は、環境を有効化しているか確認するか、フルパスで実行してください。

```bash
~/venvs/hpc-mcp/bin/hpc-mcp --version
```

Codex / Reasonix に登録するときは、そのフルパスをコマンドとして指定します。

```bash
codex mcp add hpc ... -- ~/venvs/hpc-mcp/bin/hpc-mcp
```

### 方法 B：conda

すでに conda を使っている場合：

```bash
conda create -n hpc-mcp python=3.10
conda activate hpc-mcp
cd ~/git_repo/HPC-MCP
pip install .
```

### 方法 C：~/.local に直接入れる（非推奨、一時利用のみ）

```bash
cd ~/git_repo/HPC-MCP
python3 -m pip install --user .
```

> `UNKNOWN-0.0.0` や依存関係の競合警告が出る場合は、pip/setuptools が古いか
> `~/.local` がすでに混在しています。方法 A に切り替えてください。

### 問題が起きたら

- `pip install .` で `UNKNOWN-0.0.0` になる → pip/setuptools が古く、環境が分離されていない。
- `ERROR: pip's dependency resolver does not currently take into account...` が出る
  → システムの `~/.local` に別のパッケージ（torch/httpcore など）が混在している。**hpc-mcp の動作には影響しません**が、その環境は混在状態なので仮想環境に切り替えることを推奨します。

### ⚠️ インストール後の最初の作業：パスワードなし SSH の確認

**hpc-mcp はパスワードなしログインを強制します（`BatchMode=yes`、パスワード入力は不可）。** 設定していないと、サーバ起動後すべてのツール呼び出しが `Permission denied` で失敗し、しかも**パスワードのプロンプトは一切出ません**。そのため、パッケージを入れてクラスタに接続する前に、**必ず手動で鍵認証が通ることを確認**してください。

```bash
# ポイント：-o BatchMode=yes で hpc-mcp と同じ接続方法を再現する。
# パスワードを聞かれずに OK が返れば鍵認証は準備完了：
ssh -o BatchMode=yes -o ConnectTimeout=10 username@192.168.12.12 "echo OK"
```

- `OK` と表示される → 鍵認証済み。手順 3 に進んでください。
- `Permission denied (publickey...)` → **まだ鍵認証が未設定**。先に設定します：
  ```bash
  ssh-copy-id username@192.168.12.12   # 1 回だけパスワードを聞かれ、以後は不要
  ```
  そのあと上の BatchMode 確認コマンドを再実行してください。
- `Connection timed out` → ネットワークが不通。先に VPN 接続／踏み台サーバの設定を行ってください（手順 2）。

> `~/.ssh/config` で接続を管理している場合は、コマンド内のホストを `Host` 別名に置き換えます：
> `ssh -o BatchMode=yes my-hpc "echo OK"`

---

## 2. SSH の準備：まず「素の ssh がパスワードなしで通る」状態にする

hpc-mcp は内部でシステムの OpenSSH を使うため、**ssh の問題はまずターミナルで再現してください**。手動でテストします。

```bash
ssh username@192.168.12.12 "echo OK && hostname"
```

結果は 3 通り：

| 結果 | 意味 | 対処 |
|---|---|---|
| `OK` とホスト名が表示される | 鍵認証もネットワークも OK | 手順 3 へ |
| `password:` で止まる | 鍵認証が未設定 | 鍵を設定：`ssh-copy-id username@192.168.12.12` |
| `Connection timed out` / 100% パケットロス | **ネットワークが不通** | 下記「ネットワークが不通」を参照 |

### ネットワークが不通（最も多いケース）

`192.168.12.12` は**プライベートネットワークのアドレス**です。学内ネットワークや VPN に接続していなければ、外部からは到達できません。

- まず普段どうやって接続しているかを確認します。VPN が先に必要か？踏み台サーバが必要か？
- VPN が必要な場合：接続してから再テストします。
- 踏み台が必要な場合：手順 4 の `~/.ssh/config`（`ProxyJump`）の設定を参照してください。

### 強く推奨：`~/.ssh/config` で接続を管理する

接続の詳細を config に書いておけば、hpc-mcp の `--host` は別名を参照するだけで済み、最も楽です。

```sshconfig
# ~/.ssh/config
Host 192.168.12.12
    HostName 192.168.12.12
    User username
    IdentityFile ~/.ssh/id_ed25519
    # 踏み台サーバが必要な場合は次の行を有効化（jump-host を置き換える）：
    # ProxyJump jump-host
```

設定後にターミナルで `ssh 192.168.12.12 "echo OK"` が通れば、hpc-mcp では `--host 192.168.12.12` を使います。

---

## 3. `--check` で検証する（重要！省略しないこと）

`--check` は**能動的に 1 回 SSH 接続**してリモートの情報を出力するので、設定やネットワークの正否がすぐにわかります。

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

- 成功：`Configuration OK. Remote probe succeeded:` と、hostname / リモートユーザー / Slurm が利用可能かどうかが表示されます。
- 失敗：`Connection check FAILED: ...` と原因（タイムアウト / 接続拒否 / 認証失敗）が表示されるので、それに沿って切り分けます。

> **判断基準**：`--check` が成功すれば、ネットワークと設定はどちらも問題なく、次に Codex/Reasonix へ接続すれば使えます。
> `--check` が失敗するなら、まず SSH／ネットワークを解決してください。クライアントの設定は後回しで構いません。

---

## 4. パラメータ一覧

> このセクションは**起動・セルフチェック関連**の CLI パラメータのみを扱います。
> 設定ファイルの全キー（`slurm.*`、`files.*`、`topology.*` とすべての環境変数）は
> [CONFIGURATION.md](CONFIGURATION.md) を、**各 MCP ツールのパラメータ**は
> [TOOLS.md](TOOLS.md) を参照してください。

| パラメータ | 役割 | 環境変数 | 必須 |
|---|---|---|---|
| `--host` | HPC ホスト（IP または `~/.ssh/config` の別名） | `HPC_MCP_HOST` | **必須** |
| `--user` | SSH ログインユーザー（ここでは共有アカウント） | `HPC_MCP_USER` | 推奨 |
| `--root` | **リモート**のサンドボックスルート。agent はこの配下しか操作できない | `HPC_MCP_ROOT` | **必須** |
| `--local-root` | **ローカル**でアップロード／ダウンロードを許可するディレクトリ（既定＝起動時のディレクトリ + `/tmp`、複数指定は `local_roots`） | `HPC_MCP_LOCAL_ROOT` / `HPC_MCP_LOCAL_ROOTS` | 任意 |
| `--port` | SSH ポート（既定 22） | `HPC_MCP_PORT` | 任意 |
| `--identity-file` | 秘密鍵のパス（既定は `~/.ssh/config`） | `HPC_MCP_IDENTITY_FILE` | 任意 |
| `--ssh-bin` | ssh 実行ファイルのパス。例：`/usr/bin/ssh`、`@/usr/bin/ssh`、`@/mnt/c/.../ssh.exe` | `HPC_MCP_SSH_BIN` | 任意 |
| `--sftp-bin` | sftp 実行ファイルのパス（書式は `--ssh-bin` と同じ） | `HPC_MCP_SFTP_BIN` | 任意 |
| `--config` | YAML 設定ファイルのパス | — | 任意 |
| `--check` | 接続確認だけを行って終了 | — | 任意 |
| `--log-file` | ログを追記するファイル（stderr にも出力） | `HPC_MCP_LOG_FILE` | 任意 |
| `--log-level` | ログレベル：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`（既定 `INFO`） | `HPC_MCP_LOG_LEVEL` | 任意 |
| `--version` | バージョンを表示して終了 | — | 任意 |

サブコマンド `hpc-mcp mcp-add`（Codex / Reasonix の MCP 設定を書き込む。冪等で、既存設定を壊しません）：

| パラメータ | 役割 | 既定 |
|---|---|---|
| `--client` | 更新するクライアント：`codex`、`reasonix`、`all` | `all` |
| `--config` | YAML 設定のパス（その host/root などがクライアントの env に書き込まれる） | — |
| `--host` / `--user` / `--root` | 設定ファイルを使わず直接指定 | — |

**`--local-root` について**：`hpc.files.upload`/`download` がアクセスできる**ローカル**ディレクトリの範囲を制限し、agent がローカルの `.ssh` などの機密ディレクトリを読むのを防ぎます。通常は現在のプロジェクトディレクトリ（`$PWD`）で十分です。**必須ではありません**。指定しない場合は現在のディレクトリが既定になります。

**`--root` について**：これはリモートクラスタ上で**あなた専用のサブディレクトリ**です（ログインは共有アカウント `username` を使うため）。`/home/username/alice` はその共有アカウント配下のあなたの個人領域であり、正しい指定です。

---

## 5. 長いパラメータ列の代わりに設定ファイルを使う（推奨）

パラメータを YAML に固定すれば、以後は 1 コマンドで起動できます。

```bash
mkdir -p ~/.config/hpc-mcp
cat > ~/.config/hpc-mcp/192.168.12.12.yaml <<'EOF'
host: 192.168.12.12                                  # ~/.ssh/config の Host 別名
user: username
root: /home/username/alice
local_root: /home/alice               # 許可するローカルディレクトリ
ssh_bin: /mnt/c/Windows/System32/OpenSSH/ssh.exe
sftp_bin: /mnt/c/Windows/System32/OpenSSH/sftp.exe

slurm:
  allowed_partitions: [thcp1]               # 実際のパーティション名に必ず変更！
  max_cpus: 64
  max_nodes: 2
  max_time: "24:00:00"

# 任意：計算ノードのトポロジ取得（hpc.cluster.topo が使用）
# topology:
#   enabled: true             # false = ツールを登録せず、収集ジョブも一切投入しない
#   cache_ttl_seconds: 86400  # トポロジキャッシュ 24h
EOF
```

起動 / セルフチェック：

```bash
hpc-mcp --config ~/.config/hpc-mcp/192.168.12.12.yaml --check
```

> 注意：`allowed_partitions` の既定値は**空**です（＝すべての投入を拒否、fail-closed）。
> 必ず実際のクラスタのパーティション名に変更してください（クラスタ上で `sinfo` により確認できます）。

> **キー名のルール**：設定ファイルのキーは**アンダースコア**（`ssh_bin`、`local_root`、
> `allowed_partitions`）で、`config/example.yaml` と一致させます。ハイフン形式
> （`ssh-bin`）も別名として認識されますが推奨しません。
> 綴りを間違えたキーはエラーにならず、`Ignoring unknown config key 'xxx'` の警告が
> 1 行出て無視されるだけです。この行が出たら設定を修正してください。

> **ソースや設定を変えたのに反映されない？** `pip install .` でインストールした場合、
> 仮想環境内には**コピー**が入っています。リポジトリのソースを編集しても自動では反映されず、
> 再インストールが必要です：
> ```bash
> source ~/venvs/hpc-mcp/bin/activate
> cd ~/git_repo/HPC-MCP && pip install .
> # 以後の編集を即反映させたい場合は editable でインストール：pip install -e .
> ```

---

## 6. Codex / Reasonix への接続

`--check` が通ったら、クライアントに登録します。

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

登録すると、クライアントは stdio で `hpc-mcp` を起動します。以後は「プロジェクトのディレクトリを一覧して」「Slurm ジョブを投入して」と依頼できます。

---

## 7. 「固まる」現象の対応表

| 見えている現象 | 実際の原因 | 対処 |
|---|---|---|
| `starting: ...` で止まったまま動かない | **正常**。stdio サーバがクライアント入力を待っている | 何もしなくてよい。クライアントからツールを呼ぶか、`--check` で確認する |
| `--check` が `Connection timed out` | **ネットワーク不通**（プライベート IP は VPN が必要） | VPN に接続／踏み台を設定し、手動で `ssh` を試す |
| コマンドラインでは接続できるのに設定ファイルだと繋がらない | 設定の `ssh_bin` が効いていない（キー名の綴りミス／古いコピーをインストールしている）ため、PATH 上の `ssh` にフォールバックしている（WSL の Linux 版 ssh は Windows の VPN 経路を使わない） | ログの `using ssh executable: ...` が設定したものか確認。キー名を修正し（手順 5）、`pip install .` で再インストール |
| `--check` が `Permission denied` | 鍵認証が未設定 | `ssh-copy-id` で鍵を設定 |
| `--check` が `getsockname failed: Not a socket` | SSH の制御ソケット再利用の異常（WSL や古いインストールでよくある） | hpc-mcp を更新して再インストール。新しい版は常に独立した通常の `ssh` プロセスを使います |
| 初回接続で `Are you sure ... yes/no?` と聞かれる | ホスト鍵が未固定 | 手動で `ssh` を 1 回実行して yes を入力。または `strict_host_key_checking: accept-new` を設定 |
| すべてのツールが `No Slurm partitions are allowed` で拒否される | パーティションの許可リストが空 | `slurm.allowed_partitions` を設定 |
| `pip install .` で `UNKNOWN-0.0.0` になる | pip/setuptools が古い | 仮想環境に入れ直す（手順 1 の方法 A） |
| `ERROR: pip's dependency resolver does not currently take into account...`（torch/httpcore の競合） | システムの `~/.local` に他のパッケージが混在 | **hpc-mcp の動作には影響しません**。分離した仮想環境への切り替えを推奨（手順 1 の方法 A） |

---

## 8. ログを見る

- すべてのログは **stderr** に出力されます（stdout は MCP プロトコル専用）。
- ファイルに残す場合：`--log-file ~/.local/share/hpc-mcp/hpc-mcp.log`。
- 各呼び出しの許可／拒否を見る場合：`--log-level DEBUG`。
