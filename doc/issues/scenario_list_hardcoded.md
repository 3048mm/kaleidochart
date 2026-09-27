# scenario_list_hardcoded

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] **フロントエンドのシナリオ一覧が `SCENARIO_STRATEGIES` のハードコードで、組み合わせジョブが表示されない**（2026-08-25 発見）
  - `backend/api/backtest_router.py:24` の
    `SCENARIO_STRATEGIES = ["A","B1",...,"G3"]` が固定リストで、
    **`B256_union`（複数戦略の和集合）や `B6_prev_params`（手動パラメータ比較）が
    フロントの一覧に出ない**。`:89` の `if strat not in SCENARIO_STRATEGIES` により
    URL を直接叩いても弾かれる。
  - **`data/scenario_batch_jobs.toml` にジョブを追加してもフロントに反映されない**という
    設定とコードの二重管理になっている。組み合わせ運用を本格化するなら解消が必要。
  - **対応案**: `output/scenario/` 配下のディレクトリを実際に走査して列挙する
    （`scenario_batch_jobs.toml` のジョブ名を正とする案もあるが、過去の実行結果が
    ジョブ定義から消えても閲覧できる方が実用的）。ホワイトリスト方式をやめるだけで、
    「設定したのに出てこない」型の問題が構造的に消える。
  - 関連: 2026-08-25 に B2+B5+B6 の和集合が **Calmar 1.14 と全構成で最高**を記録しており、
    組み合わせは今後の主要な運用形態になりうる
