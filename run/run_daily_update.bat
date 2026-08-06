@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: Norton 等のセキュリティ製品が HTTPS を傍受していると curl_cffi（yfinance が
:: cookie/crumb 取得に使う）の証明書検証が失敗し、全銘柄が "possibly delisted" に
:: なる（2026-08-06 に全期間再構築が全滅）。傍受用のルート証明書があれば curl に教える。
:: 該当ファイルが無い環境では何もしない。
if not defined CURL_CA_BUNDLE if exist "C:\ProgramData\Norton\Antivirus\wscert.pem" set "CURL_CA_BUNDLE=C:\ProgramData\Norton\Antivirus\wscert.pem"

:: 残留パイプラインプロセスのチェックと強制終了
powershell -Command "try { Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*update_pipeline.py*' -or $_.CommandLine -like '*daily_sync_job.py*' } | ForEach-Object { Write-Host 'Terminating dangling pipeline process ID:' $_.ProcessId; Stop-Process -Id $_.ProcessId -Force } } catch {}"

cd /d "%~dp0.."
call venv\Scripts\activate.bat
python backend\scripts\daily_sync_job.py
