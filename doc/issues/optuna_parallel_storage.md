# optuna_parallel_storage

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] **Optuna の storage を並行書き込み可能なバックエンドへ移し、最適化の並列実行（`n_jobs>1`）を解禁する**（2026-08-18 起票）
  - **現状**: `optimization_runner.py` の `--n-jobs` 既定は **1（完全逐次）**。全16戦略 × 200トライアルで**約12時間**かかる。
  - **`n_jobs=1` になっている理由は2つあり、混同しないこと**（`optimization_runner.py:638-644` のコメント）:
    - **① SQLite storage とスレッド並列のレース（未解決・これが本当の blocker）**: 複数スレッドが同一 trial の完了を同時に `study.tell` して `ValueError: Cannot tell a COMPLETE trial` で停止する。**`n_jobs=4` に下げても再発を確認済み（2026-07-17）**。並列度の調整では解消せず、`RDBStorage(SQLite)` + スレッド並列という組み合わせ自体が構造的に不安定。
    - **② `get_cached_data` のキャッシュスタンピードによるメモリ枯渇（解決済み）**: 8スレッドが同一期間を8重ロードし物理メモリ35GB超で `MemoryError`。2026-07-18 に**並列開始前の pre-warm ループ**（`optimization_runner.py:677-687`）で手当て済み。
  - **メモリ削減は①には効かない**: 2026-08-18 の Phase 3d（melt 廃止）でランク保持メモリが 6.6倍減った（Bull 期間 2,315MB → 354MB）が、これは②の余裕を広げただけで①とは無関係。「メモリが空いたから並列にできる」ではない。
  - **対応案**: `optuna.storages.JournalStorage` + `JournalFileBackend`（並行書き込み向けに用意されたバックエンド）への移行。既存 `data/optimization_trials.db` からの study 移行手順と、`Cannot tell a COMPLETE trial` が本当に再発しないことの検証（少トライアルで `n_jobs=2,4,8` を実測）がセットで必要。
  - **注意**: `run_optimization.bat` は戦略ごとに別プロセスを起動するため、**バッチ実行中に設定を変えると次の戦略から条件が変わる**。検証は必ず実行していないときに行う。
