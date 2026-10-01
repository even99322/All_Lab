# LAB-QEL論文庫：從這台電腦一鍵更新 NAS 上的網站
# 由「一鍵更新-Windows.bat」呼叫。會把這個資料夾的程式傳到 NAS，再在 NAS 上執行 update.sh。
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$cfgFile = Join-Path $env:APPDATA 'paperlib-update.txt'

function Say($t, $c = 'Cyan') { Write-Host $t -ForegroundColor $c }
function Fail($t) { Write-Host "`n✗ $t" -ForegroundColor Red; exit 1 }

Say "LAB-QEL論文庫 一鍵更新" 'White'
if (-not (Test-Path (Join-Path $root 'app\main.py'))) { Fail "請把這個工具放在解壓縮後的 paperlib 資料夾裡執行（找不到 app\main.py）。" }
$ver = (Select-String -Path (Join-Path $root 'app\config.py') -Pattern 'VERSION = "([^"]+)"').Matches[0].Groups[1].Value
Say "  要上傳的版本：$ver"
if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) { Fail "找不到 ssh。請到「設定 → 應用程式 → 選用功能」新增「OpenSSH 用戶端」。" }
if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) { Fail "找不到 tar（需要 Windows 10 1803 以上）。" }

# ── NAS 連線設定（第一次會問，之後記在 %APPDATA%\paperlib-update.txt）
$cfg = @{ host = ''; port = '22'; user = ''; path = '/volume1/docker/paperlib' }
if (Test-Path $cfgFile) { Get-Content $cfgFile -Encoding UTF8 | ForEach-Object { if ($_ -match '^(\w+)=(.*)$') { $cfg[$Matches[1]] = $Matches[2] } } }
$ask = -not $cfg.host
if (-not $ask) {
  Say "  NAS：$($cfg.user)@$($cfg.host):$($cfg.port)  專案資料夾：$($cfg.path)"
  $a = Read-Host "  直接按 Enter 使用這組設定，輸入 c 修改"
  if ($a -eq 'c') { $ask = $true }
}
if ($ask) {
  function Ask($label, $cur) { $v = Read-Host "  $label$(if ($cur) { "（Enter＝$cur）" })"; if ($v) { $v.Trim() } else { $cur } }
  $cfg.host = Ask 'NAS 的 IP 或名稱（例如 192.168.1.20 或 Tailscale 的 100.x.x.x）' $cfg.host
  $cfg.port = Ask 'SSH 埠' $cfg.port
  $cfg.user = Ask 'DSM 管理員帳號' $cfg.user
  $cfg.path = Ask 'NAS 上的專案資料夾' $cfg.path
  if (-not $cfg.host -or -not $cfg.user) { Fail "NAS 位址和帳號不能空白。" }
  ($cfg.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) | Set-Content $cfgFile -Encoding UTF8
}
$target = "$($cfg.user)@$($cfg.host)"

# ── 1. 打包
Say "`n▶ 1/3 打包程式"
$tgz = Join-Path $env:TEMP 'paperlib_update.tgz'
Remove-Item $tgz -ErrorAction SilentlyContinue
& tar.exe -czf $tgz --exclude=./data --exclude=./import --exclude=./.venv --exclude=__pycache__ --exclude=*.pyc -C $root .
if ($LASTEXITCODE -ne 0) { Fail "打包失敗。" }
Say ("  ✓ {0:N1} MB" -f ((Get-Item $tgz).Length / 1MB)) 'Green'

# ── 2. 上傳（需要輸入一次 DSM 密碼；第一次連線會問是否信任這台 NAS，輸入 yes）
Say "`n▶ 2/3 上傳到 NAS（請輸入 $($cfg.user) 的 DSM 密碼，輸入時畫面不會顯示）"
$remote = 'rm -rf /tmp/plu && mkdir -p /tmp/plu && tar -xzf - -C /tmp/plu && echo OK'
cmd /c "ssh -p $($cfg.port) $target `"$remote`" < `"$tgz`""
if ($LASTEXITCODE -ne 0) { Fail "上傳失敗。請確認：DSM 已開啟 SSH（控制台 → 終端機和 SNMP → 啟動 SSH 功能）、IP／帳號／密碼正確。" }
Remove-Item $tgz -ErrorAction SilentlyContinue

# ── 3. 在 NAS 上更新並重建（再輸入一次密碼，sudo 還會再問一次）
Say "`n▶ 3/3 在 NAS 上停止、備份、重建、啟動（約 1–3 分鐘；密碼可能會問兩次）"
ssh -t -p $cfg.port $target "sudo bash /tmp/plu/update.sh /tmp/plu '$($cfg.path)'"
if ($LASTEXITCODE -ne 0) { Fail "NAS 上的更新沒有完成，請看上面的訊息。" }

Say "`n✓ 全部完成。瀏覽器開 http://$($cfg.host):8080 按 Ctrl+F5，頭像選單應顯示「程式版本 $ver」。" 'Green'
