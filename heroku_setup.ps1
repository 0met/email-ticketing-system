param(
    [string]$AppName,
    [string]$EmailUser,
    [string]$EmailPass,
    [string]$AdminToken
)

if (-not $AppName) { Write-Host 'Usage: .\heroku_setup.ps1 -AppName <app> -EmailUser <email> -EmailPass <pass> -AdminToken <token>'; exit 1 }

Write-Host "Setting Heroku config vars for app $AppName"
heroku config:set EMAIL_USER=$EmailUser EMAIL_PASS=$EmailPass ADMIN_TOKEN=$AdminToken --app $AppName

Write-Host 'Scaling worker dyno to 1'
heroku ps:scale worker=1 --app $AppName

Write-Host 'Done. Use `heroku logs --tail --app <app>` to watch logs while testing.'
