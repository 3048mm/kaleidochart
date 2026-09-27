# weekly_split_consistency_scan

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🔵 **週次メンテに `scan_split_consistency.py` を組み込む（根本対応・2026-09-11 分離起票）**
  - 上記「分割記録の鮮度」issue の対応案のうち**前者**（レポート側の警告より
    根本的とされていた案）。今回は見送った側。
  - ネットワーク呼び出し（yfinance、2,984銘柄で約9分）を無人の週次ジョブに
    新規注入することになり、検討が要る論点が複数ある:
    - レート制限・タイムアウト時の扱い（`--skip-sec` 相当のオフライン分岐が要るか）
    - 週次メンテの実行時間予算にこの9分を乗せてよいか
    - 失敗時（ネットワーク不通等）に週次メンテ本体を止めない設計
      （`weekly_maintenance.py` の `load_split_records` 例外握り潰しと同じ配慮が要る）
  - **関連**: `doc/issue_list.md`（本ファイル）の1つ上の項目、
    `backend/scripts/scan_split_consistency.py`
