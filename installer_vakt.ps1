# Registrerer LTR-lagervakten i Oppgaveplanlegger: hvert 5. minutt, skjult vindu, kun når du er logget på
# (påkrevd for Windows-varsler). Fjern igjen med:  Unregister-ScheduledTask -TaskName 'LTR-lagervakt' -Confirm:$false
$dir     = $PSScriptRoot
$pythonw = Join-Path (Split-Path (Get-Command python).Source) 'pythonw.exe'

$action   = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$dir\vakt.py`"" -WorkingDirectory $dir
$every5   = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$atLogon  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName 'LTR-lagervakt' -Action $action -Trigger $every5, $atLogon -Settings $settings `
    -Principal $principal -Description 'Varsler når MTG Tales of Middle-earth Set Booster / Display kommer på lager i norske nettbutikker.' -Force |
    Select-Object TaskName, State
