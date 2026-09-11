# Installe ou repare la tache planifiee Windows qui maintient @Agentmk06bot en vie.
#
# 🚨 POURQUOI CE FICHIER EXISTE. La tache d'origine avait un declencheur AtStartup et
# `RestartCount = 999 / RestartInterval = 1 min`. Mesure du 2026-09-11 : le bot tue a la
# main etait TOUJOURS MORT trois minutes apres, la tache en etat "Ready", LastTaskResult
# 4294967295. La relance sur echec de Windows n'a rien fait. Sans declencheur repete, le
# service ne repartait qu'au prochain redemarrage de la MACHINE.
#
# 🚨 LA PARADE : une REPETITION du declencheur, toutes les 2 minutes, indefiniment.
# Elle n'est sans danger que grace a `MultipleInstances = IgnoreNew` : si le bot tourne
# deja, la nouvelle instance est ignoree. Sans ce reglage on lancerait un second poller
# Telegram, et deux pollers sur le meme jeton se battent (conflit 409 getUpdates) — le bot
# deviendrait muet par intermittence, ce qui est PIRE qu'un bot arrete.
# Verifie apres correction : bot tue -> relance automatique en moins de 30 s.

param(
    [string]$Nom     = 'AgentMk06-Bot',
    [string]$Dossier = 'C:\Users\Administrator\agentmk06-bot'
)

$ErrorActionPreference = 'Stop'

$python  = Join-Path $Dossier 'venv\Scripts\python.exe'
$journal = Join-Path $Dossier 'data\service.log'
if (-not (Test-Path $python)) { throw "Interpreteur introuvable : $python" }

$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c `"`"$python`" -m hermes run >> `"$journal`" 2>&1`"" `
    -WorkingDirectory $Dossier

# Deux declencheurs : au demarrage de la machine, ET toutes les 2 minutes en filet.
$auDemarrage = New-ScheduledTaskTrigger -AtStartup
$repetition  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
                 -RepetitionInterval (New-TimeSpan -Minutes 2)

$reglages = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $Nom -Action $action `
    -Trigger @($auDemarrage, $repetition) -Settings $reglages `
    -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

$t = Get-ScheduledTask -TaskName $Nom
Write-Host "Tache '$Nom' installee."
Write-Host ("  declencheurs      : " + (($t.Triggers | ForEach-Object {
    $_.CimClass.CimClassName + $(if ($_.Repetition.Interval) { " (repete $($_.Repetition.Interval))" })
}) -join ' + '))
Write-Host ("  instances         : " + $t.Settings.MultipleInstances + "  <- garde-fou anti double poller")
Write-Host ("  etat              : " + $t.State)
Write-Host ''
Write-Host "Verifier ensuite : `"$python`" -m hermes health   (code 0 = il ecoute)"
