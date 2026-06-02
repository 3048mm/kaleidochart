@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ===================================================
echo   stocktool 自動環境構築スクリプト (Windows用)
echo ===================================================
echo.

:: 1. Python の確認
echo [1/4] Python 3.12+ の確認中...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 【警告】Python がインストールされていないか、PATHが通っていません。
    echo winget を使用して Python 3.12 を自動インストールしますか？(Y/N)
    set /p install_py="選択: "
    if /i "!install_py!"=="Y" (
        echo Python 3.12 をインストールしています...
        winget install -e --id Python.Python.3.12
        echo インストールが完了しました。環境変数を反映させるため、このウィンドウを一度閉じて再度実行してください。
        pause
        exit /b
    ) else (
        echo 手動で Python 3.12+ をインストールしてください。
        pause
        exit /b
    )
) else (
    echo Python はインストールされています。
    python --version
)
echo.

:: 2. Node.js の確認
echo [2/4] Node.js と npm の確認中...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 【警告】Node.js がインストールされていないか、PATHが通っていません。
    echo winget を使用して Node.js (LTS) を自動インストールしますか？(Y/N)
    set /p install_node="選択: "
    if /i "!install_node!"=="Y" (
        echo Node.js LTS をインストールしています...
        winget install -e --id OpenJS.NodeJS.LTS
        echo インストールが完了しました。環境変数を反映させるため、このウィンドウを一度閉じて再度実行してください。
        pause
        exit /b
    ) else (
        echo 手動で Node.js をインストールしてください。
        pause
        exit /b
    )
) else (
    echo Node.js はインストールされています。
    echo Node: & node --version
    echo npm:  & call npm --version
)
echo.

:: 3. バックエンドの環境構築
echo [3/4] バックエンドのセットアップ中...
if not exist "venv" (
    echo 仮想環境 (venv) を作成しています...
    python -m venv venv
) else (
    echo 仮想環境 (venv) は既に存在します。
)

echo 仮想環境にパッケージをインストールしています...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo 【エラー】バックエンドのパッケージインストールに失敗しました。
    pause
    exit /b
)
echo バックエンドのセットアップが完了しました。
echo.

:: 4. フロントエンドの環境構築
echo [4/4] フロントエンドのセットアップ中...
if exist "frontend" (
    cd frontend
    echo frontend フォルダ内で npm install を実行しています...
    call npm install
    if !errorlevel! neq 0 (
        echo 【エラー】フロントエンドのパッケージインストールに失敗しました。
        cd ..
        pause
        exit /b
    )
    cd ..
) else (
    echo 【エラー】frontend フォルダが見つかりません。
)
echo フロントエンドのセットアップが完了しました。
echo.

echo ===================================================
echo   環境構築が正常に完了しました！
echo   以下の手順でシステムを起動できます：
echo.
echo   1. バックエンドの起動:
echo      (このフォルダで) .\venv\Scripts\activate.bat を実行し、
echo      python main.py などのバックエンドスクリプトを実行します。
echo.
echo   2. フロントエンドの起動:
echo      cd frontend
echo      npm run dev
echo ===================================================
pause
