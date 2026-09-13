# Bascule la session interactive sur la CONSOLE, pour que le bureau reste dessine.
#
# POURQUOI. Windows Server cesse de dessiner le bureau des que la session RDP est
# deconnectee : plus d'ecran a capturer, plus de fenetre ou cliquer, donc plus de mains
# pour l'agent. La session console, elle, reste rendue meme sans personne devant.
#
# CE QUE CELA NE FAIT PAS : aucune deconnexion, aucune fermeture de session. Les programmes
# en cours — Chrome et ses sessions de publication, MetaTrader — continuent de tourner. La
# session change seulement de terminal d'affichage.
#
# POUR REVENIR : il n'y a rien a defaire. Se reconnecter en Bureau a distance ramene la
# session sur le transport RDP.
#
# Usage : powershell -ExecutionPolicy Bypass -File mains\bascule_console.ps1 [journal]

param([string]$Journal = "$env:TEMP\bascule_console.log")

function Note($texte) {
    $ligne = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $texte
    Add-Content -Path $Journal -Value $ligne -Encoding utf8
    Write-Output $ligne
}

Note "=== BASCULE VERS LA CONSOLE ==="

# La session interactive de l'Administrateur, quel que soit son etat.
$brut = (query session) -split "`r?`n"
$cible = $null
foreach ($ligne in $brut) {
    if ($ligne -match '^\s*>?\s*\S*\s+Administrator\s+(\d+)\s') { $cible = [int]$Matches[1]; break }
}
if (-not $cible) { Note "AUCUNE session Administrator trouvee — rien a basculer."; exit 1 }
Note "session ciblee : $cible"

$avantRdp = (Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 3389 | Measure-Object).Count
$avantChrome = (Get-Process chrome -ErrorAction SilentlyContinue | Measure-Object).Count
Note "avant : RDP en ecoute=$avantRdp, chrome=$avantChrome processus"

try {
    $sortie = & tscon $cible /dest:console 2>&1
    Note "tscon : $sortie"
} catch {
    Note "tscon a echoue : $_"
}

Start-Sleep -Seconds 6

Note "--- APRES ---"
foreach ($ligne in ((query session) -split "`r?`n")) { if ($ligne.Trim()) { Note $ligne } }

$apresRdp = (Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 3389 | Measure-Object).Count
$apresChrome = (Get-Process chrome -ErrorAction SilentlyContinue | Measure-Object).Count
Note "apres : RDP en ecoute=$apresRdp, chrome=$apresChrome processus"
if ($apresRdp -lt 1) { Note "🚨 ALERTE : le service RDP n'ecoute plus." }
if ($apresChrome -lt $avantChrome) { Note "🚨 ALERTE : des processus chrome ont disparu." }

Note "termine."
