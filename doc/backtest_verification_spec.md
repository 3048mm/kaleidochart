# バックテスト検証仕様書 (Backtest Verification Spec)

## 1. 目的
本ドキュメントは、バックテストエンジンの正確性、再現性、およびパフォーマンスを担保するための検証項目と手順を定義する。

---

## 2. 検証基本方針
- **整合性重視**: DB 直接実行と高速化キャッシュ（Parquet）実行の結果が 1:1 で一致することを最優先とする。
- **型安全性の徹底**: Pandas の `Timestamp` 型と Python の `datetime.date` 型の不一致によるサイレントな失敗（0件ヒット）を未然に防ぐ。
- **実環境での検証**: 常に `data/stocktool.db` のスナップショットを用いた `verify_db_vs_cache.py` による実証を行う。

---

## 3. 検証項目

### 3.1 データロードと型正規化
- [ ] **日付型の自動変換**: `preload_data` 実行後、 `df_prices`, `df_indicators`, `df_ranks` の `date` カラムが `datetime.date` 型（dtype: object）に変換されているか。
- [ ] **メモリ安全ロード**: 大規模データ取得時、 `chunksize` が指定され OOM（メモリ不足）が発生しないか。
- [ ] **時価総額の補完**: 過去の欠損データに対し、最新の `market_cap` が正読にバックフィルされているか。

### 3.2 スクリーニングロジック (backtest_screener.py)
- [ ] **動的カラム算出**: `gain_1d_pct` （1日騰落率）などの DB 未保持カラムが、始値・終値から正しく算出されているか。
- [ ] **フィルタリングの連鎖**: ADR, Volume Surge, RS Rank などの複合条件が期待通りに（AND条件として）機能しているか。
- [ ] **テーマ RS 判定**: 戦略 B において、上位テーマ（RS21 > RS63）の絞り込みと、その構成銘柄の抽出が正確か。

### 3.3 シミュレーションと出口ルール (backtest_simulator.py)
- [ ] **エントリー価格**: 指定日の終値で約定しているか。
- [ ] **出口ルールの優先度**: `Stop Loss`, `Take Profit`, `EMA21 割れ` 等のルールが設計通りの優先度で適用されているか。
- [ ] **利益計算**: 手数料やスリッページを考慮しない理論上の損益計算が正確か。

### 3.4 最適化ランナー (optimization_runner.py)
- [ ] **戦略名マッピング**: `B` -> `B_theme_momentum` のような短縮名がバックテスト設定と正しく紐付いているか。
- [ ] **パラメータ継承**: TOML で定義された基本設定（market_cap 閾値など）が、Optuna の試行中に失われず維持されているか。
- [ ] **ペナルティ勾配**: トレード 0 件時に、パラメータ探索を継続させるための適切なスコア勾配が付与されているか。

---

## 4. 検証手順 (Standard Verification Flow)

### 4.1 整合性テスト
変更を加えた際は、必ず以下のコマンドを実行し、DB とキャッシュの結果が一致することを確認する。
```powershell
$env:PYTHONPATH="backend"; python backend/backtest/verify_db_vs_cache.py
```

### 4.2 証跡の確認
- [ ] `backend/backtest/results/` 配下の JSON ファイルを開き、 `total_trades` が 0 以外であることを確認。
- [ ] `verify_db_vs_cache.py` の出力ログに `VERIFICATION SUCCESS: Data consistency PROVEN.` が表示されていることを確認。

---

## 6. 更新履歴
- 2026-04-05: 初版作成（バックエンド検証仕様書より分離独立）
