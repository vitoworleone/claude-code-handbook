# ============================================
# clean-for-github.ps1
# ============================================
# 用途：在 push 到 GitHub 前清理不应上传的文件
# 运行前请确认已提交/备份重要文件
# ============================================

param(
    [switch]$DryRun,      # 预览模式：只显示会删除什么，不实际删除
    [switch]$Force        # 跳过确认直接执行
)

$ErrorActionPreference = "Stop"
$itemsToDelete = @()

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ClaudeCode-Runtime 清理脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---- 定义需要清理的路径 ----
$paths = @(
    # Python 虚拟环境
    "02_Source-Code/01_CC-Python-Runtime/Source/.venv"

    # Python 缓存
    "02_Source-Code/01_CC-Python-Runtime/Source/__pycache__"
    "02_Source-Code/01_CC-Python-Runtime/Source/.pytest_cache"

    # 敏感文件
    "02_Source-Code/01_CC-Python-Runtime/Source/.env"

    # 嵌套 Git 仓库历史
    "03_References/claude-code-sourcemap-main/claude code analysis/.git"

    # 压缩包（与已解压内容重复）
    "03_References/claude-code-sourcemap-main/claude code analysis/src.zip"

    # 原始 TS 源码映射（版权敏感，analysis/ 已包含精华分析）
    "03_References/claude-code-sourcemap-main/claude code analysis/src"

    # 工具运行时目录
    ".antigravitycli"
    ".sisyphus"
    ".traces"
)

$basePath = (Resolve-Path "$PSScriptRoot/..").Path

foreach ($relPath in $paths) {
    $fullPath = Join-Path $basePath $relPath
    if (Test-Path $fullPath) {
        $size = (Get-ChildItem $fullPath -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        $sizeStr = if ($size -gt 1GB) {
            "{0:N1} GB" -f ($size / 1GB)
        } elseif ($size -gt 1MB) {
            "{0:N1} MB" -f ($size / 1MB)
        } elseif ($size -gt 1KB) {
            "{0:N1} KB" -f ($size / 1KB)
        } else {
            "$size B"
        }
        $itemsToDelete += [PSCustomObject]@{
            Path     = $relPath
            FullPath = $fullPath
            Size     = $sizeStr
            SizeBytes= $size
        }
    }
}

if ($itemsToDelete.Count -eq 0) {
    Write-Host "✅ 没有找到需要清理的项目。仓库已经是干净状态。" -ForegroundColor Green
    exit 0
}

# ---- 显示预览 ----
Write-Host "将删除以下项目：" -ForegroundColor Yellow
Write-Host ""
$totalSize = 0
foreach ($item in $itemsToDelete) {
    $marker = if ($item.Path -like "*/src" -or $item.Path -like "*/src.zip") { " [版权敏感]" } else { "" }
    Write-Host "  - $($item.Path) ($($item.Size))$marker" -ForegroundColor Red
    $totalSize += $item.SizeBytes
}

$totalSizeStr = if ($totalSize -gt 1GB) {
    "{0:N1} GB" -f ($totalSize / 1GB)
} elseif ($totalSize -gt 1MB) {
    "{0:N1} MB" -f ($totalSize / 1MB)
} else {
    "{0:N1} KB" -f ($totalSize / 1KB)
}

Write-Host ""
Write-Host "预计释放空间：$totalSizeStr" -ForegroundColor Cyan
Write-Host ""

if ($DryRun) {
    Write-Host "🔍 这是预览模式（-DryRun），未执行任何删除。" -ForegroundColor Green
    Write-Host "   去掉 -DryRun 参数以实际执行清理。" -ForegroundColor Gray
    exit 0
}

# ---- 确认 ----
if (-not $Force) {
    $confirm = Read-Host "确认删除以上项目? 输入 'yes' 继续"
    if ($confirm -ne "yes") {
        Write-Host "❌ 已取消。" -ForegroundColor Yellow
        exit 1
    }
}

# ---- 执行删除 ----
Write-Host ""
Write-Host "正在清理..." -ForegroundColor Yellow

foreach ($item in $itemsToDelete) {
    try {
        Remove-Item $item.FullPath -Recurse -Force -ErrorAction Stop
        Write-Host "  ✅ 已删除: $($item.Path)" -ForegroundColor Green
    } catch {
        Write-Host "  ❌ 删除失败: $($item.Path) — $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  清理完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "下一步建议：" -ForegroundColor Cyan
Write-Host "  1. git add -A && git status 检查剩余文件"
Write-Host "  2. 确保 .env.example 已保留（模板文件）"
Write-Host "  3. git commit -m '初始化仓库'"
Write-Host "  4. git push origin main"
