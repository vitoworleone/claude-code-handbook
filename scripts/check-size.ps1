# ============================================
# check-size.ps1
# ============================================
# 用途：检查即将 push 到 GitHub 的文件总大小
# ============================================

$basePath = (Resolve-Path "$PSScriptRoot/..").Path
Push-Location $basePath

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Git 追踪文件大小检查" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 如果已经是 git 仓库，用 git 统计
if (Test-Path "$basePath/.git") {
    $tracked = git ls-files | ForEach-Object {
        $path = $_
        $size = (Get-Item $path -ErrorAction SilentlyContinue).Length
        [PSCustomObject]@{
            Path = $path
            Size = $size
        }
    } | Sort-Object Size -Descending

    $total = ($tracked | Measure-Object -Property Size -Sum).Sum

    Write-Host "Git 追踪文件总数: $($tracked.Count)" -ForegroundColor White
    Write-Host "总大小: $("{0:N1}" -f ($total / 1MB)) MB" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "最大的 20 个文件：" -ForegroundColor Yellow
    Write-Host ""
    $tracked | Select-Object -First 20 | ForEach-Object {
        $sizeStr = if ($_.Size -gt 1MB) {
            "{0:N1} MB" -f ($_.Size / 1MB)
        } elseif ($_.Size -gt 1KB) {
            "{0:N1} KB" -f ($_.Size / 1KB)
        } else {
            "$($_.Size) B"
        }
        Write-Host "  $($sizeStr.PadLeft(10))  $($_.Path)" -ForegroundColor White
    }
} else {
    # 非 git 仓库，用文件系统统计（排除 .gitignore 中的内容）
    Write-Host "尚未初始化 Git 仓库。统计所有非忽略文件..." -ForegroundColor Yellow
    Write-Host ""

    # 简单的排除规则
    $excludePatterns = @('.git', '.venv', '__pycache__', '.pytest_cache', '.env', '.claude', '.antigravitycli', '.sisyphus', '.traces', '*.zip', '*.tar*')

    $allFiles = Get-ChildItem -Path $basePath -Recurse -File | Where-Object {
        $file = $_
        $shouldExclude = $false
        foreach ($pattern in $excludePatterns) {
            if ($file.FullName -like "*$pattern*") {
                $shouldExclude = $true
                break
            }
        }
        -not $shouldExclude
    } | ForEach-Object {
        [PSCustomObject]@{
            Path = $_.FullName.Replace($basePath + '\', '').Replace('\', '/')
            Size = $_.Length
        }
    } | Sort-Object Size -Descending

    $total = ($allFiles | Measure-Object -Property Size -Sum).Sum
    Write-Host "文件总数: $($allFiles.Count)" -ForegroundColor White
    Write-Host "总大小: $("{0:N1}" -f ($total / 1MB)) MB" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "最大的 20 个文件：" -ForegroundColor Yellow
    Write-Host ""
    $allFiles | Select-Object -First 20 | ForEach-Object {
        $sizeStr = if ($_.Size -gt 1MB) {
            "{0:N1} MB" -f ($_.Size / 1MB)
        } elseif ($_.Size -gt 1KB) {
            "{0:N1} KB" -f ($_.Size / 1KB)
        } else {
            "$($_.Size) B"
        }
        Write-Host "  $($sizeStr.PadLeft(10))  $($_.Path)" -ForegroundColor White
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan

Pop-Location
