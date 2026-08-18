# twinarm

Twin-arm robot control package.

## 依存管理の方針

依存の定義は **`pyproject.toml` 一本**に集約しています。

- `pyproject.toml` — パッケージのメタデータ、依存、ツール設定（black / isort / ruff / pytest / mypy）
- `environment.yml` — conda 環境の骨だけ。Python 本体と pip のみを宣言し、パッケージ依存は書かない

依存を追加するときに触るのは `pyproject.toml` だけです。`environment.yml` を編集するのは Python のバージョンを変えるときに限られます。

## セットアップ

### 前提: conda

conda が未導入の場合は **Miniforge** を推奨します（conda-forge が既定チャンネルで、ライセンス上の制約もありません）。

```bash
# Linux x86_64
curl -fsSLo Miniforge3.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3.sh -b -p "$HOME/miniforge3"
"$HOME/miniforge3/bin/conda" init bash
exec "$SHELL"
```

`conda --version` が通れば準備完了です。

### 環境の作成

このディレクトリ（`twinarm/`）で実行します。

```bash
conda env create -f environment.yml
conda activate twinarm
pip install -e ".[dev]"
```

`pip install -e` は editable install です。`src/twinarm/` を編集した内容が再インストールなしで即反映されます。

### 動作確認

```bash
python -c "import twinarm; print(twinarm.__version__)"
# => 0.1.0
```

## 日常のコマンド

いずれも `conda activate twinarm` 済みの状態で、このディレクトリから実行します。

| 目的 | コマンド |
| --- | --- |
| フォーマット | `black . && isort .` |
| フォーマット確認のみ | `black --check . && isort --check-only .` |
| Lint | `ruff check .` |
| Lint 自動修正 | `ruff check --fix .` |
| 型チェック | `mypy` |
| テスト | `pytest` |

テストは `unit` / `integration` のマーカーで分類します。実機やネットワークに触るものは `integration` を付けてください。

```bash
pytest -m unit          # 実機なしで走るテストのみ
pytest -m integration   # 実機ありのテストのみ
```

## 依存の追加

1. `pyproject.toml` を編集する
   - 実行時に必要なもの → `[project]` の `dependencies`
   - 開発時のみ必要なもの → `[project.optional-dependencies]` の `dev`
2. 再インストールする

```bash
pip install -e ".[dev]"
```

conda 環境を作り直す必要はありません。

### PyPI にしか無いパッケージ

`lerobot` や `dynamixel-sdk` のように conda-forge に無いパッケージも、そのまま `pyproject.toml` の `dependencies` に書けば pip 経由で入ります。`environment.yml` に追記する必要はありません。

### Python のバージョンを変える

`environment.yml` の `python=3.11` と、`pyproject.toml` の `requires-python` / `target-version` / `[tool.mypy] python_version` を合わせて更新し、環境を作り直します。

```bash
conda env remove -n twinarm
conda env create -f environment.yml
conda activate twinarm
pip install -e ".[dev]"
```

## ディレクトリ構成

```
twinarm/
├── environment.yml       # conda 環境（Python + pip のみ）
├── pyproject.toml        # 依存とツール設定の唯一の正
├── src/
│   └── twinarm/          # パッケージ本体
│       └── __init__.py
└── tests/                # pytest のテスト
    └── test_package.py   # インストールが成功しているかのスモークテスト
```

src レイアウトを採用しています。インストールされたパッケージに対してテストが走るため、「ローカルでは通るのに配布すると import できない」という不整合を防げます。

## トラブルシュート

**`pip install -e ".[dev]"` で `zsh: no matches found` になる**

zsh が `[dev]` をグロブとして解釈しています。引用符は必須です（上記のコマンドはすべて引用済み）。

**`conda activate` が `CondaError: Run 'conda init'` で失敗する**

`conda init <あなたのシェル>` を実行し、シェルを開き直してください。

**`import twinarm` が `ModuleNotFoundError` になる**

`conda activate twinarm` を忘れているか、`pip install -e ".[dev]"` を実行していません。`which python` が `~/miniforge3/envs/twinarm/bin/python` を指しているか確認してください。
