# moomoo_usage_ideas

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] **moomoo API 知見の活用アイデア（2026-09-12 起票、未検証・要判断）**
  - **背景**: P1「分割・統合の検証手段」第3段階で `MoomooClient` Wrapper
    （`backend/data_collection/moomoo_client.py`）を実装し、口座開設不要・米国株LV3
    データ無料・`get_rehab()`でコーポレートアクションが取れる、といった知見が
    `.claude/skills/moomoo-api/SKILL.md` に蓄積された。yfinanceの既知の限界
    （`.claude/skills/upstream-data-diagnosis/SKILL.md`）の多くを埋められる可能性がある。
    **いずれも思いつきの段階で、実装するかは個別に判断が要る。**

  - **A. 404/429の切り分けにmoomooを使う（upstream-data-diagnosis 限界A対策）**
    yfinanceは「ティッカーが存在しない」と「レート制限で弾かれた」を同じ
    `possibly delisted` に畳んでしまい、2026-07-29 に本番55行を誤削除する事故が
    起きている。**独立した配信元であるmoomooに同じティッカーを問い合わせ、
    データの有無で切り分ける**（moomooも無ければ本当に存在しない可能性が高く、
    moomooにはあるがyfinanceだけ失敗しているなら供給側の一時障害と判断できる）。
    現在の decision tree（`upstream-data-diagnosis` §0/§5）に第2ソースとして
    組み込める。`retire_stale_symbols.py` の供給側検証を補強する形が有力。

  - **B. 分割以外のコーポレートアクションの検出拡張**
    `get_rehab()` は分割・併合だけでなく、配当(`per_cash_div`)・特別配当
    (`special_dividend`)・株式配当(`bonus_*`)・増資(`add_*`/`stk_spo_*`)・
    配株(`allot_*`)・分立/スピンオフ(`spin_off_*`)も同じ呼び出しで返す。
    現状 `scan_split_consistency.py` は分割にしか使っていないが、スピンオフや
    増資も価格に段差を作りうる（分割と同じ「未調整」パターンを起こしうる）。
    `scan_price_anomalies.py` の `undecided`/`market_wide` に紛れている段差の中に、
    分割ではなくこれらが原因のものがないか、既存の`split_records.json`の枠組みを
    拡張して調べる価値がある。

  - **C. 上流待ち（STALE）銘柄の切り分け強化**
    `db_health_check.py` が「上流が系列を切り落とした」として保留する STALE 銘柄
    （2026-09-12時点で20件）は、yfinance側の供給停止としてこちらでは対応不能と
    されている。moomooで同じティッカーの直近データが取れるか確認すれば、
    「本当に上場廃止・改称」と「yfinanceだけの供給遅延」を区別できる可能性がある。

  - **D. 配当・特別分配の正確性チェックの一般化**
    `VISN`（2026年の特別分配 $10.00/$5.00 から調整比を厳密に説明できたケース）のような
    手作業の検算を、`get_rehab()`の`per_cash_div`/`special_dividend`を使って
    半自動化できる可能性がある。RS計算・バックテストの前提となる価格調整の
    信頼性を上げられる。

  - **E. FXレートのクロスチェック**
    `fx_rates`は過去に土日混入・データ消失などの品質問題があった
    （`.claude/skills/upstream-data-diagnosis/SKILL.md`関連）。moomooは`Market.FX`
    で為替も扱えるため、独立ソースとしての定期照合に使える可能性がある。

  - **F（大掛かり・優先度低）. リアルタイム相場の活用**
    moomooはリアルタイム気配・板情報（`subscribe`/`get_rt_data`/`get_order_book`）
    も提供する。現状の日次バッチ完結アーキテクチャとは設計思想が異なり、
    ダッシュボード/ウォッチリストへのリアルタイム表示は**種別Bの大きな変更**になる。
    ポートフォリオのmoomoo証券API連携（別項）と合わせて検討する場合のみ価値が出る。

  - **その他、未調査だが存在を確認したAPI面**（`moomoo-api` SKILL.md 未記載分。
    このプロジェクトで使う価値があるかは個別評価が要る）:
    `EarningsCalendar`系（決算日・予想）、`DividendRank`系、`get_owner_plate`
    （銘柄が属するセクター/テーマ分類）、`get_stock_basicinfo`。
    stocktoolの「テーマ」分類・決算関連機能と重なる可能性があるが未検証。

  - **優先度の目安**: A・Cは既存の`upstream-data-diagnosis`運用に直結し費用対効果が
    高そうだが、いずれも「moomoo認証の手動性」という制約（週次自動化は時期尚早との
    既存判断）を踏まえると、まずは個別調査ツールとしての位置づけになる。
