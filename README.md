# Stock Analyzer

個人の株式投資を支援するためのWebベースの株価分析・スクリーニングツールです。
「どの銘柄を購入すべきか」を判断するため、S&P500 等に対する相対的強さ（Relative Strength）、ATR乖離、Volume Surge、およびマーク・ミネルヴィニのトレンドテンプレートに基いたデータ収集と分析を行います。

## プロジェクト構成
プロジェクトはクリーンアーキテクチャの思想に基づき、大きく2つのコンポーネントに分かれています。

- **`backend/`**: Pythonを用いたFastAPIサーバー、およびデータ収集・指標計算を行うバッチスクリプト群。
- **`frontend/`**: Vite + React(TypeScript) で構築されたSPA（シングルページアプリケーション）。

また、以下のフォルダ構成として以下が存在する。
- **`doc/`**: アーキテクチャ設計および各機能の詳細仕様書。
- **`data/`**: 本番用データベース（`stocktool.db`）および生のデータファイル。
- **`run/`**: 日々のデータ更新やタスクスケジューラ登録用の手動起動バッチファイル(`.bat`, `.sh`)。
- **`tools/`**: DBメンテナンスや補助的なユーティリティスクリプト。
- **`tmp/`**: 実験的なスクリプト、一時的なログ。

---

## 初回セットアップ手順

プロジェクト直下に用意された `setup.bat` をダブルクリック（またはダブルクリックして実行）します。
1. プロジェクトルートにある `setup.bat` をダブルクリックして実行します。
2. 画面の指示に従って処理が進むのを待ちます。（もしPythonやNode.jsが入っていない場合は、自動インストールするか確認が出ます）
3. 完了まで待機します。

### setup.bat でインストールされるソフトウェア
* Python (3.12以降)
* Node.js (18以降)
* Pip
* Npm
* Venv


---

### 手動で行う場合のセットアップ手順

#### 1. 前提条件 (Prerequisites)
- **Python 3.12+** がインストールされていること。
- **Node.js (npm)** がインストールされていること。

#### 2. バックエンドのセットアップ
1. コマンドプロンプトまたはPowerShellでこのフォルダ（プロジェクトルート）を開きます。
2. 仮想環境を作成し、アクティベートします。
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   ```
3. 依存パッケージをインストールします。
   ```powershell
   pip install -r requirements.txt
   ```

#### 3. フロントエンドのセットアップ
1. `frontend` フォルダに移動します。
   ```powershell
   cd frontend
   npm install
   ```


---

## 日々の運用・実行手順 (User Workflow)

### ① 日次データの更新（Daily Sync）
毎日の市場終了後（またはツール利用前）に、最新の株価データを取得して各種インジケータを再計算します。

1. **`run/`** フォルダの中にある **`run_daily_update.bat`** をダブルクリックして実行します。
2. コマンドプロンプトが立ち上がり、Google Spreadsheetからの銘柄同期、yfinanceからのデータ取得、計算処理が走ります。（※自動的に閉じるか、終了メッセージが出たら完了です）

> **💡 自動化したい場合**:  
> `run/register_daily_task.bat` を管理者権限で実行すると、Windowsのタスクスケジューラに毎朝7時(速報用)と13時(後場出来高確定用)に自動実行されるようジョブが登録されます。
開始時刻は`run/register_daily_task.bat`で変更可能。

### ② アプリケーションの起動（Start Application）
分析ツール画面を見るためには、バックエンドAPIとフロントエンドUIの両方を起動します。

**Terminal 1 (バックエンドAPI)**
プロジェクトルートで以下を実行します。（Pythonのパスを通すため）
```powershell
$env:PYTHONPATH="backend"
.\venv\Scripts\python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8001
```

**Terminal 2 (フロントエンドGUI)**
`frontend` フォルダに移動して以下を実行します。
```powershell
cd frontend
npm run dev
```

### 分析ツールへアクセス
ブラウザを開き、Terminal 2 に表示されたURL（通常は `http://localhost:5173` または `http://localhost:5174`）にアクセスしてください。ダッシュボードやスクリーナーが利用可能になります。

### (Optional) VPN(Tailscale) でスマホから分析ツールにアクセスする場合は以下を実施する。
1.  Tailscale をPCとスマホにそれぞれインストールする。
2.  PCとスマホそれぞれでTailscaleにログインし、自分のTailscaleネットワークに参加する。
3.  スマホに表示されている PC のドメイン (....ts.net)とポート番号(5173)をブラウザに入力する。
    例： `http://xxxx.ts.net:5173`
    * Optuna Dashboard も同様にアクセス可能（`run_optuna_dashboard.bat` は `--host 0.0.0.0` で起動するため）。例： `http://xxxx.ts.net:8080`
      ※初回起動時に Windows ファイアウォールの許可ダイアログが出たら「アクセスを許可」すること。
    * スマホのVPNは一定時間で切断される為、つながらない場合は TailScale アプリを再度起動後にアクセス


---

## アーキテクチャや仕様の確認
より詳細な内部仕様やロジックを確認したい場合は、`doc/` フォルダ内の仕様書をご参照ください。

- `doc/architecture.md`: システム全体の構成とデータの流れ
- `doc/backend_specification.md`: データベース設計や指標計算のロジック仕様
- `doc/frontend_specification.md`: UIコンポーネントやチャート機能の実装仕様
