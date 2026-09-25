---
description: ワークツリー・ブランチ・計画書の棚卸しを行い、撤収まで安全に片付ける
argument-hint: "[worktree名]（省略時は棚卸しのみ）"
---

未取り込みの作業と、片付いていないワークツリー・ブランチ・計画書を棚卸しする。
引数でワークツリー名が指定された場合は、その撤収まで行う（`doc/workflow.md` の **G6**）。

**撤収はユーザーの指示で、エージェントが実行する**（G6 の判断者の定義）。
棚卸しだけならユーザーの指示は要らない。

## 1. 棚卸し

```powershell
.\tools\check_worktrees.ps1
```

出力の各節を読み、**`[!]` が付いた行だけ**をユーザーに報告する。全部を貼り直さない。

| 節 | 見るポイント |
| :--- | :--- |
| Worktrees | `dirty>0`（未コミット）／`ahead-of-main>0`（未取り込みコミット）／`NOT provisioned` なのに GB 単位のデータを持つ（状態の乖離）／prune 済み Parquet 世代の保持（ディスク拘束） |
| Unregistered leftovers | 撤収が途中で止まった残骸。`git worktree list` に載らないのでここでしか見えない |
| Agent branches NOT merged | **未取り込みの作業**。撤収より先にレビュー・マージの判断が要る |
| Agent branches already merged | 撤収可能。ただし `[!]` 付きは未コミット変更があるので撤収しない |
| Skills mirror | ドリフトや frontmatter 不備。`.\venv\Scripts\python.exe tools\sync_skills.py --apply` で解消 |
| Stale plans | 14日以上更新の無い計画書。完了させるか `doc/completed/` へ移す |

`data/_bk_*`（バックアップ）の削除を提案するときは、**Parquet を含む最新のバックアップを対象から外す**。
バックテスト・最適化の既定の参照先になっている（`doc/backend_specification.md` §6.6.1）。

## 2. 撤収（引数が指定された場合、または上の報告後にユーザーが指示した場合）

> [!IMPORTANT]
> **素の `git worktree remove` は使わない。** ジャンクションを辿ってリンク先を全削除する
> （`doc/agent_execution_rules.md` §11.3。2026-09-09 にカナリアで再現済み。
> 実際に本体の `frontend/node_modules` を指すジャンクションが2本存在した）。
> PreToolUse フックが `ask` に落とすが、フックに頼らず最初からスクリプトを使うこと。

```powershell
# まず何が起きるか確認する
.\tools\remove_worktree.ps1 <名前> -DryRun

# 実行（未コミット変更を捨てる場合は -Force、マージ済みブランチも消す場合は -DeleteBranch）
.\tools\remove_worktree.ps1 <名前> -DeleteBranch
```

スクリプトは①作業が失われる条件で中断 ②reparse point のリンクだけ外してターゲットの生存を確認
③撤収 ④マージ済みのときだけブランチ削除、の順で動く。
**未コミット変更があるときはリンクを外す前に中断する**ので、中断しても状態は変わらない。

### 判断しないこと

- **未コミット変更の破棄はエージェントが判断しない。** 差分を提示してユーザーの指示を仰ぐ。
- **未マージのブランチを持つワークツリーは撤収しない。** 先に取り込み（G4）の判断が要る。

どちらも `doc/workflow.md` §2.1 のエスカレーション条件3（破壊的操作）に当たる。

## 3. 完了報告

撤収した場合は、**本番データが無傷であることを実測で示す**。

```powershell
Get-ChildItem "data\parquet_master" -File | Measure-Object Length -Sum
```

撤収前後でファイル数と合計バイト数が一致することを確認して報告する
（ハードリンクを1本消しても実体は残る、という設計どおりであることの確認）。
