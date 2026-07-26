# Windows 转发器。只做转发，不维护第二套验证逻辑（两套必然漂移）。
# 需要 Git Bash（随 Git for Windows 安装）。
$bash = "$env:ProgramFiles\Git\bin\bash.exe"
if (-not (Test-Path $bash)) { $bash = "bash" }
& $bash "$PSScriptRoot/verify.sh"
exit $LASTEXITCODE
