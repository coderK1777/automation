param(
    [ValidateSet('up', 'check', 'stop', 'status')]
    [string]$Action = 'up',
    [ValidatePattern('^[a-z0-9][a-z0-9_-]*$')]
    [string]$ProjectName = 'tradebot-pro',
    [switch]$ReuseImages
)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDirectory

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE)." }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Install Docker Desktop and start it, then run this launcher again.'
}
$composeVersion = & docker compose version --short
if ($LASTEXITCODE -ne 0 -or [version]($composeVersion.TrimStart('v').Split('-')[0]) -lt [version]'2.24.4') {
    throw 'Update Docker Desktop: Compose 2.24.4 or newer is required.'
}
$dockerSystem = & docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $dockerSystem.Trim() -ne 'linux') {
    throw 'Start Docker Desktop and switch it to Linux containers.'
}
$composeArguments = @('compose', '-p', $ProjectName, '-f', 'docker-compose.yml',
    '-f', 'infrastructure/docker-local-windows.yml')
$proxyBuildArguments = @()
if ($env:HTTPS_PROXY) {
    if (-not $env:SSL_CERT_FILE -or -not (Test-Path -LiteralPath $env:SSL_CERT_FILE)) {
        throw 'This HTTPS proxy needs SSL_CERT_FILE pointing to its trusted public CA bundle.'
    }
    $env:TRADEBOT_PROXY_HOST = ([uri]$env:HTTPS_PROXY).DnsSafeHost
    $address = [System.Net.Dns]::GetHostAddresses($env:TRADEBOT_PROXY_HOST) |
        Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } | Select-Object -First 1
    if (-not $address) { throw 'Could not resolve the HTTPS proxy host.' }
    $env:TRADEBOT_PROXY_IP = $address.IPAddressToString
    $null = New-Item -ItemType Directory -Force -Path '.runtime'
    Copy-Item -LiteralPath $env:SSL_CERT_FILE -Destination '.runtime/ca-bundle.pem'
    $composeArguments += @('-f', 'infrastructure/docker-runtime-proxy.yml')
    $proxyBuildArguments = @('build', '--secret', 'id=platform_ca,src=.runtime/ca-bundle.pem',
        '--add-host', "$($env:TRADEBOT_PROXY_HOST):$($env:TRADEBOT_PROXY_IP)")
    foreach ($variable in @('HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY')) {
        if ([Environment]::GetEnvironmentVariable($variable)) { $proxyBuildArguments += @('--build-arg', $variable) }
    }
}
$composeArguments += @('--profile', 'app')
if ($Action -eq 'stop') {
    Invoke-Docker ($composeArguments + @('stop', 'web', 'auxiliary', 'demo', 'worker', 'api'))
    exit
}
if ($Action -eq 'status') {
    Invoke-Docker ($composeArguments + @('ps'))
    exit
}

if ($Action -eq 'up') {
    if (Test-Path -LiteralPath '.runtime/vault.key') {
        throw 'This folder has a host vault. Preserve it and use its existing workflow; do not replace it with a new Windows vault.'
    }
    $volumeNames = @(& docker volume ls --format '{{.Name}}')
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect existing Docker storage.' }
    $databaseExists = $volumeNames -contains "${ProjectName}_postgres_data"
    $vaultExists = $volumeNames -contains "${ProjectName}_local_vault"
    if ($databaseExists -and -not $vaultExists) {
        throw 'An existing database belongs to another vault workflow. Restore its original key or choose a separate project name.'
    }
    if ($databaseExists -and -not (Test-Path -LiteralPath '.env')) {
        throw 'Restore the original private .env before starting the existing database; do not generate different database credentials.'
    }
    if (-not (Test-Path -LiteralPath '.env')) {
        $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        function New-LocalSecret {
            $bytes = New-Object byte[] 24
            $generator.GetBytes($bytes)
            return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
        }
        try {
            $postgresPassword = New-LocalSecret
            $redisPassword = New-LocalSecret
            $operatorPassword = New-LocalSecret
            $webPort = if ($env:TRADEBOT_LOCAL_WEB_PORT) { $env:TRADEBOT_LOCAL_WEB_PORT } else { '3000' }
            $databasePort = if ($env:TRADEBOT_LOCAL_DATABASE_PORT) { $env:TRADEBOT_LOCAL_DATABASE_PORT } else { '5432' }
            $redisPort = if ($env:TRADEBOT_LOCAL_REDIS_PORT) { $env:TRADEBOT_LOCAL_REDIS_PORT } else { '6379' }
            $settings = @(
                'APP_ENV=development', 'TRADING_MODE=PAPER', 'LIVE_EXECUTION_ENABLED=false',
                'DEMO_EXECUTION_AUTHORIZED=false', 'TELEGRAM_DELIVERY_ENABLED=false',
                'SESSION_COOKIE_SECURE=false', "APP_ORIGIN=http://127.0.0.1:$webPort",
                'POSTGRES_USER=tradebot', 'POSTGRES_DB=tradebot', "POSTGRES_PASSWORD=$postgresPassword",
                "REDIS_PASSWORD=$redisPassword",
                "DATABASE_URL=postgresql+psycopg://tradebot:${postgresPassword}@127.0.0.1:$databasePort/tradebot",
                "REDIS_URL=redis://:${redisPassword}@127.0.0.1:$redisPort/0",
                'BOOTSTRAP_USERNAME=operator', "BOOTSTRAP_PASSWORD=$operatorPassword",
                'BACKEND_URL=http://127.0.0.1:8000'
            ) -join "`n"
            $environmentPath = Join-Path $projectDirectory '.env'
            $file = [System.IO.File]::Open($environmentPath, [System.IO.FileMode]::CreateNew)
            try { $file.Dispose() } finally { $file = $null }
            try {
                if ($env:OS -eq 'Windows_NT') {
                    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
                    $acl = New-Object System.Security.AccessControl.FileSecurity
                    $acl.SetOwner($identity)
                    $acl.SetAccessRuleProtection($true, $false)
                    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')
                    $acl.AddAccessRule($rule)
                    Set-Acl -LiteralPath $environmentPath -AclObject $acl
                } else {
                    & chmod 600 $environmentPath
                    if ($LASTEXITCODE -ne 0) { throw 'Could not protect .env permissions.' }
                }
                [System.IO.File]::WriteAllText($environmentPath, $settings + "`n", (New-Object System.Text.UTF8Encoding($false)))
            } finally { $settings = $null }
        } finally { $generator.Dispose() }
        Write-Host 'Generated private local credentials in .env; no passwords printed.'
    } else {
        Write-Host 'Preserving existing .env and operator credentials.'
    }
    if (-not $ReuseImages) {
        if ($proxyBuildArguments.Count) {
            Invoke-Docker ($proxyBuildArguments + @('-f', 'infrastructure/Dockerfile.api', '-t', 'tradebot-api:local', '.'))
            Invoke-Docker ($proxyBuildArguments + @('--build-arg', 'BACKEND_URL=http://api:8000',
                '-f', 'infrastructure/Dockerfile.web', '-t', 'tradebot-web:local', '.'))
        } else {
            Invoke-Docker ($composeArguments + @('build', 'api', 'web'))
        }
    }
    Invoke-Docker ($composeArguments + @('run', '--rm', '--no-deps', 'vault-init'))
    Invoke-Docker ($composeArguments + @('up', '-d', '--wait', 'postgres', 'redis'))
    Invoke-Docker ($composeArguments + @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'tradebot.cli', 'init-db'))
    Invoke-Docker ($composeArguments + @('run', '--rm', '--no-deps', 'api', 'python', '-m', 'tradebot.cli', 'bootstrap'))
    Invoke-Docker ($composeArguments + @('up', '--no-build', '-d', '--wait'))
}

# Use the actual connected web proxy and keep authentication values inside Docker.
$probe = @'
import os, time
import httpx
with httpx.Client(base_url='http://web:3000/api/v1', timeout=15, trust_env=False,
                  headers={'Origin': os.environ['APP_ORIGIN']}) as client:
    response = client.get('/health/ready')
    assert response.status_code == 200 and response.json()['status'] == 'ready', 'API/database/cache readiness failed'
    response = client.post('/auth/login', json={'username': os.environ['BOOTSTRAP_USERNAME'], 'password': os.environ['BOOTSTRAP_PASSWORD']})
    if response.status_code != 200:
        raise SystemExit('Operator login needs attention; enrolled MFA requires the documented MFA readiness workflow.')
    csrf = response.json()['csrf_token']
    try:
        response = client.get('/dashboard')
        assert response.status_code == 200 and response.json()['mode'] == 'PAPER', 'PAPER dashboard check failed'
        deadline = time.monotonic() + 180
        while True:
            response = client.get('/worker/health')
            if response.status_code == 200 and response.json()['status'] == 'healthy':
                break
            if time.monotonic() >= deadline:
                raise SystemExit('Web/API ready; public-data worker is still warming up. Inspect worker logs and rerun with -Action check.')
            time.sleep(3)
    finally:
        client.post('/auth/logout', json={}, headers={'X-CSRF-Token': csrf}).raise_for_status()
print('Authenticated dashboard, database, Redis and public-data worker passed. Real-money execution is disabled.')
'@
Invoke-Docker ($composeArguments + @('run', '--rm', '--no-deps', '-T', 'api', 'python', '-c', $probe))
$webPort = if ($env:TRADEBOT_LOCAL_WEB_PORT) { $env:TRADEBOT_LOCAL_WEB_PORT } else { '3000' }
Write-Host "On this Windows computer, open http://127.0.0.1:$webPort in your browser."
Write-Host 'Read BOOTSTRAP_USERNAME and BOOTSTRAP_PASSWORD from .env in a local editor.'
