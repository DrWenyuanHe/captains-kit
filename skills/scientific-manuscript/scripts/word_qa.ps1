#Requires -Version 5.1
<#
.SYNOPSIS
    Native Word checks for the scientific-manuscript skill. Run through `msw.py word-qa`.

.DESCRIPTION
    Reads the JSON input file written by mswlib/proof.py and drives a new, hidden Word
    instance on a read-only copy of the document:

      - snapshots the running WINWORD process ids, creates Word.Application and requires
        exactly one new WINWORD process (the only one this script may ever quit);
      - Visible = false, DisplayAlerts = none, AutomationSecurity = force-disable macros;
      - opens the copy read-only without adding it to the recent-files list;
      - records revisions (total, by type, authors), fields, inline shapes and shapes;
      - Accept All: remaining revisions, paragraphs, pages, words, headings and heading
        defects, probe hits and the full text; Undo; Reject All: remaining revisions,
        paragraphs and the full text;
      - closes without saving, quits, releases COM and waits for its own process to exit.

    It never saves a document, never touches another Word process and creates every output
    with CreateNew (existing files are never replaced). JSON and text are written as UTF-8
    without a byte-order mark. This file itself is saved as UTF-8 with a byte-order mark so
    that Windows PowerShell 5.1 reads any non-ASCII text in it correctly (for example "§").

    A hang (a modal dialog raised by an add-in) cannot be interrupted from inside this
    script. The Python caller is the watchdog: it stops this PowerShell process after its
    timeout and, with --reap-own-process, stops only the Word PID recorded in pid_file.
#>
param(
    [Parameter(Mandatory = $true)][string]$InputJson
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
$cfg = [System.IO.File]::ReadAllText($InputJson, $utf8) | ConvertFrom-Json

function New-TextFile([string]$Path, [string]$Text) {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read)
    try {
        $bytes = $utf8.GetBytes($Text)
        $stream.Write($bytes, 0, $bytes.Length)
    } finally {
        $stream.Dispose()
    }
}

New-TextFile $cfg.progress_log ''
function Write-Log([string]$Message) {
    $line = '{0} {1}' -f [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ'), $Message
    [System.IO.File]::AppendAllText($cfg.progress_log, $line + "`n", $utf8)
}

$wsRegex = [regex]::new([string]$cfg.normalize.whitespace)
$removeRegex = [regex]::new([string]$cfg.normalize.remove)
$hyphenRegex = [regex]::new([string]$cfg.normalize.hyphen)
$translate = @{}
if ($null -ne $cfg.normalize.translate) {
    foreach ($entry in $cfg.normalize.translate.PSObject.Properties) { $translate[[char]$entry.Name] = [string]$entry.Value }
}
$puaRegex = [regex]::new('[\uF020-\uF0FF]')
function Get-Normalized([string]$Text) {
    if ($null -eq $Text) { return '' }
    if ($translate.Count -gt 0 -and $puaRegex.IsMatch($Text)) {
        # Symbol-font characters: Word reports them in the private-use area.
        $sb = [System.Text.StringBuilder]::new($Text.Length)
        foreach ($c in $Text.ToCharArray()) {
            if ($translate.ContainsKey($c)) { [void]$sb.Append($translate[$c]) } else { [void]$sb.Append($c) }
        }
        $Text = $sb.ToString()
    }
    $t = $removeRegex.Replace($Text, '')
    $t = $hyphenRegex.Replace($t, '-')
    return $wsRegex.Replace($t, ' ').Trim(' ')
}

$probes = @()
if ($null -ne $cfg.probes) { $probes = @($cfg.probes | Where-Object { $_ }) }

# WdRevisionType names.
$revisionTypes = @{
    0 = 'no_revision'; 1 = 'insert'; 2 = 'delete'; 3 = 'property'; 4 = 'paragraph_number';
    5 = 'display_field'; 6 = 'reconcile'; 7 = 'conflict'; 8 = 'style'; 9 = 'replace';
    10 = 'paragraph_property'; 11 = 'table_property'; 12 = 'section_property'; 13 = 'style_definition';
    14 = 'moved_from'; 15 = 'moved_to'; 16 = 'cell_insertion'; 17 = 'cell_deletion'; 18 = 'cell_merge';
    19 = 'cell_split'; 20 = 'conflict_insert'; 21 = 'conflict_delete'
}

$windowCheck = $true
try {
    Add-Type -Namespace MswWordQa -Name Native -MemberDefinition @'
[DllImport("user32.dll")]
public static extern uint GetWindowThreadProcessId(System.IntPtr hWnd, out uint lpdwProcessId);
'@
} catch {
    $windowCheck = $false
}

$result = [ordered]@{
    schema = 'msw-word-qa-raw/1'
    success = $false
    stage = 'start'
    error = $null
    started_utc = [DateTime]::UtcNow.ToString('o')
    powershell_version = $PSVersionTable.PSVersion.ToString()
    prior_word_process_ids = @()
    new_word_process_ids = @()
    own_word_pid = $null
    window_pid = $null
    word_version = $null
    word_build = $null
    opened_read_only = $null
}

$word = $null
$doc = $null
$ownPid = $null
$owned = $false

function Get-WordPids {
    return @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
}

function Get-HeadingStyleNames($Document) {
    $names = @()
    for ($k = -2; $k -ge -10; $k--) {       # wdStyleHeading1 .. wdStyleHeading9
        try { $names += [string]$Document.Styles.Item($k).NameLocal } catch { }
    }
    return $names
}

function Get-ParagraphStyleName($Paragraph) {
    try {
        $style = $Paragraph.Style
        if ($style -is [string]) { return $style }
        return [string]$style.NameLocal
    } catch {
        return ''
    }
}

function Get-RangeText($Range) {
    $Range.TextRetrievalMode.IncludeFieldCodes = $false
    $Range.TextRetrievalMode.IncludeHiddenText = $true
    return [string]$Range.Text
}

# Office interop binds Close/Quit with [ref] optional arguments; plain IDispatch takes values.
function Close-Document($Document) {
    try { $Document.Close([ref]0) } catch { $Document.Close(0) }      # wdDoNotSaveChanges
}

function Stop-Word($Application) {
    try { $Application.Quit([ref]0) } catch { try { $Application.Quit(0) } catch { $Application.Quit() } }
}

function Open-Copy {
    $opened = $word.Documents.Open([string]$cfg.copy_path, $false, $true, $false)
    if (-not $opened.ReadOnly) { throw 'the copy did not open read-only; refusing to continue' }
    return $opened
}

try {
    $result.stage = 'snapshot'
    $prior = @(Get-WordPids)
    $result.prior_word_process_ids = $prior
    Write-Log ('existing WINWORD processes: {0}' -f ($prior -join ', '))

    $result.stage = 'create'
    $word = New-Object -ComObject Word.Application
    $after = @(Get-WordPids)
    $new = @($after | Where-Object { $prior -notcontains $_ })
    $result.new_word_process_ids = $new
    if ($new.Count -ne 1) {
        throw ('expected exactly one new WINWORD process, found {0}; refusing to drive Word' -f $new.Count)
    }
    $ownPid = [int]$new[0]
    $owned = $true
    $result.own_word_pid = $ownPid
    $startTime = (Get-Process -Id $ownPid).StartTime.ToUniversalTime().ToString('o')
    New-TextFile $cfg.pid_file ("{0}`n{1}`n" -f $ownPid, $startTime)
    Write-Log "own Word PID $ownPid"

    $result.stage = 'configure'
    $word.Visible = $false
    $word.DisplayAlerts = 0          # wdAlertsNone
    $word.AutomationSecurity = 3     # msoAutomationSecurityForceDisable
    $word.ScreenUpdating = $false
    $result.word_version = [string]$word.Version
    $result.word_build = [string]$word.Build
    if ($word.Documents.Count -ne 0) { throw 'the new Word instance already has documents open; refusing' }

    $result.stage = 'open'
    Write-Log 'opening the read-only copy'
    $doc = Open-Copy
    $result.opened_read_only = [bool]$doc.ReadOnly
    if ($windowCheck) { try {
        $hwnd = [IntPtr]::new([long]$doc.ActiveWindow.Hwnd)
        [uint32]$windowPid = 0
        [void][MswWordQa.Native]::GetWindowThreadProcessId($hwnd, [ref]$windowPid)
        $result.window_pid = [int]$windowPid
    } catch {
        Write-Log ('window handle unavailable: {0}' -f $_.Exception.Message)
    } }
    if ($result.window_pid -and $result.window_pid -ne $ownPid) {
        throw ('the document window belongs to PID {0}, not {1}; refusing' -f $result.window_pid, $ownPid)
    }

    $result.stage = 'tracked'
    Write-Log 'reading the tracked view'
    $result.revisions_total = [int]$doc.Revisions.Count
    $types = [ordered]@{}
    $authors = [System.Collections.Hashtable]::new([System.StringComparer]::Ordinal)
    $scanned = 0
    foreach ($rev in $doc.Revisions) {
        $scanned++
        if ($scanned -gt [int]$cfg.revision_scan_limit) { $result.revision_scan_truncated = $true; break }
        $code = [int]$rev.Type
        $name = if ($revisionTypes.ContainsKey($code)) { $revisionTypes[$code] } else { "type_$code" }
        if ($types.Contains($name)) { $types[$name] = $types[$name] + 1 } else { $types[$name] = 1 }
        $authors[[string]$rev.Author] = $true
    }
    $result.revisions_by_type = $types
    $result.revision_authors = @($authors.Keys | Sort-Object)
    $result.fields = [int]$doc.Fields.Count
    $result.inline_shapes = [int]$doc.InlineShapes.Count
    $result.shapes = [int]$doc.Shapes.Count
    $result.paragraphs = [int]$doc.Paragraphs.Count

    $result.stage = 'accept'
    Write-Log 'Accept All'
    $doc.Revisions.AcceptAll()
    $result.accepted_revisions_remaining = [int]$doc.Revisions.Count
    $result.accepted_paragraphs = [int]$doc.Paragraphs.Count
    $result.accepted_inline_shapes = [int]$doc.InlineShapes.Count
    $result.accepted_shapes = [int]$doc.Shapes.Count
    $result.accepted_pages = [int]$doc.ComputeStatistics(2)     # wdStatisticPages
    $result.accepted_words = [int]$doc.ComputeStatistics(0)     # wdStatisticWords

    $headingNames = @(Get-HeadingStyleNames $doc)
    $headings = @()
    $empty = @()
    $trailingSpace = @()
    $trailingColon = @()
    $hits = [System.Collections.Hashtable]::new([System.StringComparer]::Ordinal)
    $firstHit = [System.Collections.Hashtable]::new([System.StringComparer]::Ordinal)
    foreach ($probe in $probes) { $hits[$probe] = 0; $firstHit[$probe] = $null }
    foreach ($paragraph in $doc.Paragraphs) {
        $raw = Get-RangeText $paragraph.Range
        $text = $raw.TrimEnd([char]13, [char]7)
        $normalized = $null
        if ($probes.Count -gt 0) {
            $normalized = Get-Normalized $text
            foreach ($probe in $probes) {
                if ($normalized.Contains([string]$probe)) {
                    $hits[$probe] = $hits[$probe] + 1
                    if ($null -eq $firstHit[$probe]) {
                        $firstHit[$probe] = if ($normalized.Length -gt 300) { $normalized.Substring(0, 300) } else { $normalized }
                    }
                }
            }
        }
        $styleName = Get-ParagraphStyleName $paragraph
        $isHeading = ($headingNames -contains $styleName) -or ($styleName -like 'Heading*') -or
            ($styleName -like '*berschrift*')
        if ($isHeading) {
            $headings += [ordered]@{ style = $styleName; text = $text }
            if ((Get-Normalized $text) -eq '') { $empty += $text }
            elseif ($text -match '[\s\u00A0]$') { $trailingSpace += $text }
            if ($text.TrimEnd() -match '[:\uFF1A]$') { $trailingColon += $text }
        }
    }
    $result.accepted_headings = $headings
    $result.heading_empty = $empty
    $result.heading_trailing_whitespace = $trailingSpace
    $result.heading_trailing_colon = $trailingColon
    $result.probe_hits = @($probes | ForEach-Object {
            [ordered]@{ probe = $_; hits = $hits[$_]; first_text = $firstHit[$_] } })
    New-TextFile $cfg.accepted_text_path (Get-RangeText $doc.Content)

    $result.stage = 'undo'
    Write-Log 'Undo'
    $undoSteps = 0
    while ($undoSteps -lt 100000 -and $doc.Undo()) { $undoSteps++ }
    $result.undo_steps = $undoSteps
    $result.after_undo_revisions = [int]$doc.Revisions.Count
    $result.reopened_for_reject = $false
    if ($result.after_undo_revisions -ne $result.revisions_total) {
        Write-Log 'Undo did not restore every revision; reopening the copy for Reject All'
        Close-Document $doc
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)
        $doc = Open-Copy
        $result.reopened_for_reject = $true
    }

    $result.stage = 'reject'
    Write-Log 'Reject All'
    $doc.Revisions.RejectAll()
    $result.rejected_revisions_remaining = [int]$doc.Revisions.Count
    $result.rejected_paragraphs = [int]$doc.Paragraphs.Count
    $result.rejected_inline_shapes = [int]$doc.InlineShapes.Count
    $result.rejected_shapes = [int]$doc.Shapes.Count
    New-TextFile $cfg.rejected_text_path (Get-RangeText $doc.Content)

    $result.stage = 'done'
    $result.success = $true
} catch {
    $result.error = $_.Exception.Message
    Write-Log ('ERROR at stage {0}: {1}' -f $result.stage, $result.error)
} finally {
    if ($null -ne $doc) {
        try { Close-Document $doc } catch { Write-Log ('close failed: {0}' -f $_.Exception.Message) }
        try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) } catch { }
        $doc = $null
    }
    if ($null -ne $word) {
        $quit = $false
        if ($owned) {
            try { Stop-Word $word; $quit = $true } catch { Write-Log ('quit failed: {0}' -f $_.Exception.Message) }
        } elseif ($result.new_word_process_ids.Count -gt 0) {
            # Not verified as ours, but created by this call: quit only if it holds no documents.
            try {
                if ($word.Documents.Count -eq 0) { Stop-Word $word; $quit = $true }
            } catch { }
        }
        $result.quit_called = $quit
        try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) } catch { }
        $word = $null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    if ($null -ne $ownPid) {
        $deadline = [DateTime]::UtcNow.AddSeconds([int]$cfg.exit_wait_seconds)
        $exited = $false
        $waitStart = [DateTime]::UtcNow
        while ($true) {
            if (-not (Get-Process -Id $ownPid -ErrorAction SilentlyContinue)) { $exited = $true; break }
            if ([DateTime]::UtcNow -ge $deadline) { break }
            Start-Sleep -Milliseconds 250
        }
        $result.own_word_process_exited = $exited
        $result.own_word_exit_wait_seconds = [math]::Round(([DateTime]::UtcNow - $waitStart).TotalSeconds, 1)
        Write-Log ('own Word process exited: {0}' -f $exited)
    }
    $result.finished_utc = [DateTime]::UtcNow.ToString('o')
    New-TextFile $cfg.result_json ($result | ConvertTo-Json -Depth 8)
    Write-Log 'result written'
}

if ($result.success) { exit 0 } else { exit 1 }
