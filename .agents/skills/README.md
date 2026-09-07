# .agents/skills — ミラー（編集しないこと）

このディレクトリは **`.claude/skills/` のミラー**です。他の AI ツール（Antigravity 等）から
参照するために置いてあります。**正本は `.claude/skills/`** であり、同期方向は常に
`.claude` → `.agents` の一方向です。

## 編集手順

1. `.claude/skills/<name>/SKILL.md` を編集する
2. 同期する

   ```powershell
   .\venv\Scripts\python.exe tools\sync_skills.py --apply
   ```

3. `git add` するときは**両方のパスを指定する**（`.claude/skills/...` と `.agents/skills/...`）

こちら側を直接編集しても、次の `--apply` で上書きされます。

## なぜリンクで共有していないのか

Windows では、このディレクトリを `.claude/skills/` へのリンクにする手段が**すべて
サイレントに壊れる**ため（実測結果は `doc/agent_execution_rules.md` §11）:

| 手段 | 結果 |
| :--- | :--- |
| シンボリックリンク | 管理者権限または開発者モードが必要で**作成できない**。加えて `core.symlinks=false` のため、コミットされてもチェックアウト時にパス文字列を書いた普通のファイルになる |
| Git Bash の `ln -s` | **exit 0 を返して実体はコピー**。「リンクしたつもり」だけが残る |
| ジャンクション (`mklink /J`) | 作成はできるが**リポジトリに乗らない**。clone やワークツリーでは実ファイル2部に戻り、その機械でしか効かない |
| ファイル単位のハードリンク | 作成はできるが、**エディタと `git checkout` が黙ってリンクを切る**（temp+rename で別 inode になる） |

対象は10数ファイルのテキストなので、重複そのもののコストは無視できます。
問題は重複ではなく**ドリフトに誰も気づかないこと**だったため、
実ファイル2部のまま**検出可能にする**方針を採っています。

## 検出

```powershell
.\venv\Scripts\python.exe tools\sync_skills.py --check   # ドリフトがあれば exit 1
.\tools\check_worktrees.ps1                              # 棚卸しの一部として自動で実行される
```

`--check` は同期状態に加えて **YAML frontmatter の有無**も検査します。
`## Metadata` 見出しは解析されず、**skill が発火条件を失って自動起動しなくなる**
（2026-09-07 以前、2本が実際にこの状態だった）ためです。
