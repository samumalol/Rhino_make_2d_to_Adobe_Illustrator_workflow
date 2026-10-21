<#
run_illustrator.ps1 - run the DWG_* Illustrator scripts without dialogs, over COM.

  setup      open a Rhino SVG, run DWG_Setup, save as .ai
             -In plan.svg -Out plan.ai [-Preset "Solid poche"|"Outline cut"|"Grey poche"] [-Mult 1.0] [-Unlocked]
  entourage  run DWG_Entourage in an .ai and save it
             -In plan.ai [-Out other.ai] [-Scale '1/4" = 1''-0"'] [-NoSymbols] [-NoScaleBar]
  update     swap new Rhino linework into a master .ai and save it
             -In plan.ai -From plan.svg
  preview    export a PNG (artboard, or the whole document with -WholeDoc)
             -In plan.ai|plan.svg -Out plan.png [-Zoom 300] [-WholeDoc]

Only touches documents it opens itself. Refuses to run on a file that is already open in
Illustrator (closing it here could lose unsaved work). Never quits Illustrator.
Prints the script's result message; exit code 1 on error.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('setup', 'entourage', 'update', 'preview')][string]$Action,
    [Parameter(Mandatory = $true)][string]$In,
    [string]$Out,
    [string]$From,
    [string]$Preset = 'Solid poche',
    [double]$Mult = 1.0,
    [switch]$Unlocked,
    [string]$Scale,
    [switch]$NoSymbols,
    [switch]$NoScaleBar,
    [int]$Zoom = 300,
    [switch]$WholeDoc
)
$ErrorActionPreference = 'Stop'

function Js([string]$s) { ConvertTo-Json $s }                 # safe JS string literal
function JsPath([string]$p) { Js ((Resolve-Path -LiteralPath $p).Path -replace '\\', '/') }
function JsOutPath([string]$p) { Js ([System.IO.Path]::GetFullPath($p) -replace '\\', '/') }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path -LiteralPath $In)) { Write-Error "Input not found: $In" }
$inFull = (Resolve-Path -LiteralPath $In).Path

switch ($Action) {
    'setup' {
        if (-not $Out) { $Out = [System.IO.Path]::ChangeExtension($inFull, '.ai') }
        $auto = "{preset: $(Js $Preset), mult: $Mult, lockLinework: $(if ($Unlocked) {'false'} else {'true'})}"
        $body = @"
`$.global.DWG_AUTO = $auto;
`$.evalFile(new File(WF + "DWG_Setup.jsx"));
R.push(`$.global.DWG_RESULT);
doc.saveAs(new File($(JsOutPath $Out)), new IllustratorSaveOptions());
R.push("saved " + $(JsOutPath $Out));
"@
    }
    'entourage' {
        $save = if ($Out) { "doc.saveAs(new File($(JsOutPath $Out)), new IllustratorSaveOptions());" } else { 'doc.save();' }
        $scaleJs = if ($Scale) { ", scale: $(Js $Scale)" } else { '' }
        $body = @"
`$.global.DWG_AUTO = {symbols: $(if ($NoSymbols) {'false'} else {'true'}), scaleBar: $(if ($NoScaleBar) {'false'} else {'true'})$scaleJs};
`$.evalFile(new File(WF + "DWG_Entourage.jsx"));
R.push(`$.global.DWG_RESULT);
$save
R.push("saved");
"@
    }
    'update' {
        if (-not $From) { Write-Error "update needs -From <new Rhino SVG>" }
        $body = @"
`$.global.DWG_AUTO = {update: $(JsPath $From)};
`$.evalFile(new File(WF + "DWG_UpdateLinework.jsx"));
doc = app.activeDocument;
R.push(`$.global.DWG_RESULT);
doc.save();
R.push("saved");
"@
    }
    'preview' {
        if (-not $Out) { $Out = [System.IO.Path]::ChangeExtension($inFull, '.png') }
        $body = @"
var o = new ExportOptionsPNG24();
o.artBoardClipping = $(if ($WholeDoc) {'false'} else {'true'});
o.horizontalScale = $Zoom; o.verticalScale = $Zoom; o.antiAliasing = true;
doc.exportFile(new File($(JsOutPath $Out)), ExportType.PNG24, o);
R.push("exported " + $(JsOutPath $Out));
"@
    }
}

# Illustrator silently fails to save into a folder that doesn't exist, so make it first
$expect = if ($Action -eq 'update' -or ($Action -eq 'entourage' -and -not $Out)) { $inFull } else { [System.IO.Path]::GetFullPath($Out) }
$outDir = Split-Path -Parent $expect
if (-not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
$before = if (Test-Path -LiteralPath $expect) { (Get-Item -LiteralPath $expect).LastWriteTime } else { $null }

$log = Join-Path $env:TEMP ("dwg_run_" + [guid]::NewGuid().ToString('N') + ".txt")
$jsx = [System.IO.Path]::ChangeExtension($log, '.jsx')
$wf = Js (($here -replace '\\', '/') + '/')
$script = @"
#target illustrator
var WF = $wf;
var R = [];
var IN = $(JsPath $inFull);
var prevUI = app.userInteractionLevel;
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
try {
    for (var i = 0; i < app.documents.length; i++) {
        if (app.documents[i].fullName && app.documents[i].fullName.fsName.replace(/\\/g, "/").toLowerCase() === IN.toLowerCase())
            throw new Error("That file is open in Illustrator. Save + close it first (or run the script from File > Scripts).");
    }
    var doc = app.open(new File(IN));
    try {
$body
    } finally {
        try { if (doc) doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
    }
} catch (e) {
    R.push("ERROR: " + e + (e.line ? " (line " + e.line + ")" : ""));
} finally {
    `$.global.DWG_AUTO = undefined;
    app.userInteractionLevel = prevUI;
}
var lf = new File($(Js ($log -replace '\\', '/'))); lf.encoding = "UTF-8"; lf.open("w"); lf.write(R.join("\n")); lf.close();
"@
Set-Content -LiteralPath $jsx -Value $script -Encoding UTF8

$ai = New-Object -ComObject Illustrator.Application
[void]$ai.DoJavaScriptFile($jsx)
Remove-Item -LiteralPath $jsx -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath $log)) { Write-Error "Illustrator did not report back (script failed to start)." }
$result = Get-Content -LiteralPath $log -Raw -Encoding UTF8
Remove-Item -LiteralPath $log -ErrorAction SilentlyContinue
Write-Output $result
if ($result -match '^ERROR:' -or $result -match "`nERROR:") { exit 1 }
$after = if (Test-Path -LiteralPath $expect) { (Get-Item -LiteralPath $expect).LastWriteTime } else { $null }
if (-not $after -or ($before -and $after -le $before)) { Write-Output "ERROR: Illustrator reported success but $expect was not written."; exit 1 }
exit 0
