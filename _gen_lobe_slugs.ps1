# Precompute LobeHub icon coverage for every provider id we currently
# emit at /api/providers/list. Writes a TypeScript const so the
# component can drop runtime fuzzy matching and do one O(1) lookup.

$ErrorActionPreference = "Stop"

Write-Host "Fetching LobeHub icon list..."
$list = Invoke-RestMethod -Uri "https://api.github.com/repos/lobehub/lobe-icons/contents/packages/static-svg/icons" -TimeoutSec 30
$lobeColor = ($list | Where-Object { $_.name -match '-color\.svg$' } | ForEach-Object { ($_.name -replace '-color\.svg$').ToLower() }) | Sort-Object -Unique
$lobeMono  = ($list | Where-Object { $_.name -match '\.svg$' -and $_.name -notmatch '-(color|text|brand|brand-color)\.svg$' } | ForEach-Object { ($_.name -replace '\.svg$').ToLower() }) | Sort-Object -Unique
Write-Host "  color icons: $($lobeColor.Count) | mono icons: $($lobeMono.Count)"

Write-Host "Fetching our provider list..."
$providers = (Invoke-RestMethod -Uri "http://localhost:8109/api/providers/list" -TimeoutSec 15).providers
Write-Host "  providers: $($providers.Count)"

# Explicit "brand alias" overrides — same as SLUGS in provider-icon.tsx.
# Each entry is a case where "provider X is conceptually brand Y, but
# the suffix-strip rules can't infer that connection from the id alone".
$slugOverrides = @{
    "openai-codex"           = "openai"
    "chatgpt-subscription"   = "openai"
    "anthropic"              = "claude"
    "claude-code"            = "claude"
    "claude-max-proxy"       = "claude"
    "google"                 = "gemini"
    "google-gemini-cli"      = "gemini"
    "gemini-cli"             = "gemini"
    "gemini-subscription"    = "gemini"
    "azure-openai-responses" = "azure"
    "vercel-ai-gateway"      = "vercel"
    "github-copilot"         = "githubcopilot"
    # Llama is Meta's LLM family — LobeHub doesn't ship a Llama-specific
    # icon, so use the Meta parent-company logo.
    "llama"                  = "meta"
}

$stripSuffixes = @(
    "-token-plan-cn", "-token-plan-ams", "-token-plan-sgp", "-token-plan",
    "-coding-plan-cn", "-coding-plan",
    "-ai-gateway", "-workers-ai",
    "-for-coding", "-coding",
    "-responses",
    "-cloud",                       # ``ollama-cloud`` -> ``ollama``
    "-ai", "-cn"
)

function Resolve-Slug($id) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($slugOverrides.ContainsKey($id)) { $candidates.Add($slugOverrides[$id]) | Out-Null }
    $candidates.Add($id) | Out-Null

    # Iteratively strip qualifier suffixes. Each round tries (a) the
    # hyphen-separated suffix list, then (b) the "no-hyphen ``ai`` glue"
    # regex. (b) inside the loop catches cases like
    # ``zhipuai-coding-plan`` -> strip ``-coding-plan`` -> ``zhipuai``
    # -> strip glued ``ai`` -> ``zhipu``. Without it, only the original
    # id was tested against (b), so any provider that needed both
    # a -coding-plan strip *and* the noai strip never resolved.
    $cur = $id
    for ($i = 0; $i -lt 4; $i++) {
        $stripped = $cur
        foreach ($sfx in $stripSuffixes) {
            if ($stripped.EndsWith($sfx)) {
                $stripped = $stripped.Substring(0, $stripped.Length - $sfx.Length)
                break
            }
        }
        # No hyphen-separated suffix peeled? Try the glued "ai" suffix.
        if ($stripped -eq $cur -and $cur -match '^([a-z][a-z0-9]+?)ai$' -and $matches[1].Length -ge 3) {
            $stripped = $matches[1]
        }
        if ($stripped -eq $cur -or [string]::IsNullOrEmpty($stripped)) { break }
        $candidates.Add($stripped) | Out-Null
        $cur = $stripped
    }
    # ``amazon-bedrock`` -> ``bedrock``.
    if ($id.StartsWith("amazon-")) { $candidates.Add($id.Substring("amazon-".Length)) | Out-Null }
    # Digit/letter prefix-suffix swap: ``302ai`` <-> ``ai302``,
    # ``360ai`` <-> ``ai360``, ``ai21`` <-> ``21ai``.
    if ($id -match '^(\d+)([a-z][a-z0-9]*)$') {
        $candidates.Add($matches[2] + $matches[1]) | Out-Null
    }
    if ($id -match '^([a-z]+)(\d+)$') {
        $candidates.Add($matches[2] + $matches[1]) | Out-Null
    }

    foreach ($c in ($candidates | Select-Object -Unique)) {
        $clower = $c.ToLower()
        $hasColor = $lobeColor -contains $clower
        $hasMono  = $lobeMono  -contains $clower
        if ($hasColor -or $hasMono) {
            return @{ slug = $clower; hasColor = $hasColor; hasMono = $hasMono }
        }
    }
    return $null
}

Write-Host "Resolving matches..."
$entries = @()
$colorHits = 0
$monoOnlyHits = 0
$misses = 0
foreach ($p in ($providers | Sort-Object id)) {
    $m = Resolve-Slug $p.id
    if ($m) {
        if ($m.hasColor) {
            $entries += "  `"$($p.id)`": { slug: `"$($m.slug)`", hasColor: true, hasMono: $($m.hasMono.ToString().ToLower()) },"
            $colorHits++
        } else {
            $entries += "  `"$($p.id)`": { slug: `"$($m.slug)`", hasColor: false, hasMono: true },"
            $monoOnlyHits++
        }
    } else {
        $misses++
    }
}

Write-Host "  color: $colorHits | mono-only: $monoOnlyHits | no LobeHub: $misses"

$out = @"
// AUTO-GENERATED by ``_gen_lobe_slugs.ps1`` -- do not hand-edit.
// Rerun the script whenever ``/api/providers/list`` grows or LobeHub
// ships new icons. Lookups against this table are O(1) and avoid the
// 404-walk the component used to do at runtime.

export interface LobeMatch {
  slug: string;
  hasColor: boolean;
  hasMono: boolean;
}

export const LOBE_ICONS: Record<string, LobeMatch> = {
$($entries -join "`n")
};
"@

$outPath = "$PSScriptRoot\web\components\settings\lobe-icons.ts"
# Write as UTF-8 without BOM (Set-Content with explicit utf8 emits
# BOM on PowerShell 5.1; use .NET to bypass).
[System.IO.File]::WriteAllText($outPath, $out, [System.Text.UTF8Encoding]::new($false))
Write-Host ""
Write-Host "Wrote $outPath"
