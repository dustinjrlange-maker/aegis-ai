# run-autonomous.ps1 — Launch Claude Code in autonomous mode with logging
#
# Usage:
#   .\run-autonomous.ps1 -Branch "phase-4/task-nlp" -PromptFile "autonomous-prompts.md"
#   .\run-autonomous.ps1 -Branch "tests/foundation" -Prompt "Set up pytest infrastructure..."
#
# This script:
#   1. Creates (or checks out) a feature branch
#   2. Launches Claude with --dangerously-skip-permissions
#   3. Logs all output to autonomous-logs/<timestamp>-<branch>.log
#   4. Shows a summary when done

param(
    [Parameter(Mandatory=$true)]
    [string]$Branch,

    [Parameter(Mandatory=$false)]
    [string]$Prompt,

    [Parameter(Mandatory=$false)]
    [string]$PromptSection
)

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\Users\dusti\Projects\aegis-ai"
$LogDir = Join-Path $ProjectRoot "autonomous-logs"

# Ensure log directory exists
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# Generate log filename
$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm"
$SafeBranch = $Branch -replace "/", "-"
$LogFile = Join-Path $LogDir "${Timestamp}_${SafeBranch}.log"

# Navigate to project
Set-Location $ProjectRoot

# Check for uncommitted changes on current branch
$Status = git status --porcelain
if ($Status) {
    Write-Host "WARNING: You have uncommitted changes on the current branch." -ForegroundColor Yellow
    Write-Host $Status
    $Confirm = Read-Host "Continue anyway? (y/n)"
    if ($Confirm -ne "y") {
        Write-Host "Aborted."
        exit 1
    }
}

# Create or checkout branch
$BranchExists = git branch --list $Branch
if ($BranchExists) {
    Write-Host "Checking out existing branch: $Branch" -ForegroundColor Cyan
    git checkout $Branch
} else {
    Write-Host "Creating new branch: $Branch" -ForegroundColor Green
    git checkout -b $Branch
}

# Build the prompt
if (!$Prompt -and $PromptSection) {
    # Extract a section from autonomous-prompts.md by header
    $PromptsFile = Join-Path $ProjectRoot "autonomous-prompts.md"
    if (Test-Path $PromptsFile) {
        $Content = Get-Content $PromptsFile -Raw
        # Find the section between ``` blocks after the matching header
        if ($Content -match "(?s)## $PromptSection.*?``````\s*\n(.*?)``````") {
            $Prompt = $Matches[1].Trim()
        } else {
            Write-Host "ERROR: Section '$PromptSection' not found in autonomous-prompts.md" -ForegroundColor Red
            Write-Host "Available sections:"
            Select-String -Path $PromptsFile -Pattern "^## " | ForEach-Object { Write-Host "  $($_.Line)" }
            exit 1
        }
    } else {
        Write-Host "ERROR: autonomous-prompts.md not found" -ForegroundColor Red
        exit 1
    }
}

if (!$Prompt) {
    Write-Host "ERROR: Provide -Prompt or -PromptSection" -ForegroundColor Red
    Write-Host ""
    Write-Host "Examples:"
    Write-Host '  .\run-autonomous.ps1 -Branch "tests/foundation" -PromptSection "Testing Foundation"'
    Write-Host '  .\run-autonomous.ps1 -Branch "phase-4/task-nlp" -Prompt "Your prompt here..."'
    exit 1
}

# Log header
$Header = @"
===============================================
AEGIS AI — AUTONOMOUS SESSION
===============================================
Branch:    $Branch
Started:   $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Log:       $LogFile
===============================================

PROMPT:
$Prompt

===============================================
OUTPUT:
"@

$Header | Out-File -FilePath $LogFile -Encoding utf8
Write-Host $Header -ForegroundColor DarkGray

# Launch Claude
Write-Host ""
Write-Host "Launching Claude Code (autonomous mode)..." -ForegroundColor Green
Write-Host "Log file: $LogFile" -ForegroundColor DarkGray
Write-Host "Press Ctrl+C to abort." -ForegroundColor DarkGray
Write-Host ""

# Run Claude and tee output to both console and log
claude --dangerously-skip-permissions -p $Prompt 2>&1 | Tee-Object -FilePath $LogFile -Append

# Footer
$Footer = @"

===============================================
SESSION COMPLETE
Ended:     $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Branch:    $Branch
===============================================

GIT LOG (commits made this session):
"@

$Footer | Out-File -FilePath $LogFile -Append -Encoding utf8

# Append git log
git log --oneline main..HEAD 2>&1 | Out-File -FilePath $LogFile -Append -Encoding utf8

# Show summary
Write-Host ""
Write-Host "===============================================" -ForegroundColor Green
Write-Host "SESSION COMPLETE" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Commits made:" -ForegroundColor Cyan
git log --oneline main..HEAD
Write-Host ""
Write-Host "Changes from main:" -ForegroundColor Cyan
git diff --stat main
Write-Host ""
Write-Host "Full log: $LogFile" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  Review:  git diff main"
Write-Host "  Merge:   git checkout main && git merge $Branch"
Write-Host "  Discard: git checkout main && git branch -D $Branch"
