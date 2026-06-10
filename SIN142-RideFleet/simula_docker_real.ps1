# =============================================================================
#  simula_docker_real.ps1
#  Simulacao REAL dos 3 fluxos de leilao RideFleet com Docker/PostgreSQL/Redis/Core
#
#  Uso:
#    cd "C:\Users\Luan\Desktop\Faculdade\Sistemas Distribuidos\SIN142-RideFleet"
#    .\simula_docker_real.ps1
#
#  Flags opcionais:
#    -SomenteFluxoA   executa apenas o Fluxo A
#    -SomenteFluxoB   executa apenas o Fluxo B
#    -SomenteFluxoC   executa apenas o Fluxo C
#    -Diagnostico     mostra logs dos containers e para
#    -LimparTudo      docker compose down -v em ambos os projetos e sai
# =============================================================================
param(
    [switch]$SomenteFluxoA,
    [switch]$SomenteFluxoB,
    [switch]$SomenteFluxoC,
    [switch]$Diagnostico,
    [switch]$LimparTudo
)

Set-StrictMode -Off
$ErrorActionPreference = "Stop"

# --- Configuracoes ------------------------------------------------------------
$RF_DIR   = $PSScriptRoot
$CORE_DIR = Join-Path (Split-Path $PSScriptRoot -Parent) "ridefleet-core-sin142"
$CORE_BASE      = "http://localhost:8080/api/v1"
$RF_BASE        = "http://localhost:8000"
$GRUPO_RF       = "ridefleet-grupo-a"
$GRUPO_FAKE     = "origem-teste"
$GRUPO_FAKE_URL = "http://host.docker.internal:9999"

# --- Helpers -----------------------------------------------------------------
function Write-Step  { param($n, $msg) Write-Host "`n  [$n] $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "      OK  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "      !   $msg" -ForegroundColor Yellow }
function Write-Info  { param($msg) Write-Host "          $msg" -ForegroundColor DarkGray }
function Write-Fail  { param($msg) Write-Host "      X   $msg" -ForegroundColor Red }
function Write-Hdr   {
    param($titulo)
    $line = "=" * 64
    Write-Host ""
    Write-Host $line -ForegroundColor Magenta
    Write-Host "  $titulo" -ForegroundColor Magenta
    Write-Host $line -ForegroundColor Magenta
}

# --- Execucao segura de comandos nativos (captura stdout/stderr/exit) -------
function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    $exit = $null
    try {
        $p = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
                -NoNewWindow -PassThru -Wait `
                -RedirectStandardOutput $outFile `
                -RedirectStandardError  $errFile
        $exit = $p.ExitCode
        $out = (Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue)
        $err = (Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue)
    } catch {
        # Start-Process so falha se o executavel nao existe no PATH
        $exit = 9001
        $out = ""
        $err = $_.Exception.Message
    } finally {
        Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $out) { $out = "" }
    if ($null -eq $err) { $err = "" }
    return [pscustomobject]@{
        Command  = "$FilePath $($Arguments -join ' ')"
        ExitCode = $exit
        StdOut   = $out.Trim()
        StdErr   = $err.Trim()
        Success  = ($exit -eq 0)
    }
}

# --- Verifica se o daemon Docker esta realmente respondendo ----------------
function Test-DockerReady {
    $v = Invoke-NativeCommand -FilePath "docker" -Arguments @("version","--format","{{.Server.Version}}")
    if ((-not $v.Success) -or [string]::IsNullOrWhiteSpace($v.StdOut)) {
        Write-Fail "Docker Engine NAO esta respondendo."
        Write-Info  "Comando : $($v.Command)"
        Write-Info  "ExitCode: $($v.ExitCode)"
        if ($v.StdErr) { Write-Info "Erro    : $($v.StdErr)" }
        $ctx = Invoke-NativeCommand -FilePath "docker" -Arguments @("context","ls")
        if ($ctx.StdOut) { Write-Info "Contextos:`n$($ctx.StdOut)" }
        Write-Warn "Acoes: abrir o Docker Desktop, aguardar 'Engine running',"
        Write-Warn "       e conferir 'docker context use desktop-linux'."
        throw "Docker Engine indisponivel."
    }
    Write-Info "Docker Engine OK (server $($v.StdOut))"
    return $true
}

# --- Garante a rede sem mascarar erro real como 'ja existe' ----------------
function Ensure-DockerNetwork {
    param([string]$Name = "ridefleet-net")

    Test-DockerReady | Out-Null

    # 1) A rede ja existe? inspect retorna exit 0 se sim
    $insp = Invoke-NativeCommand -FilePath "docker" -Arguments @("network","inspect",$Name)
    if ($insp.Success) {
        Write-Ok "Rede '$Name' ja existe (inspect OK) - nao sera recriada"
        return $true
    }

    # 2) Nao existe -> tentar criar
    Write-Info "Rede '$Name' nao encontrada. Criando..."
    $create = Invoke-NativeCommand -FilePath "docker" -Arguments @("network","create",$Name)
    if ($create.Success) {
        Write-Ok "Rede '$Name' criada"
        return $true
    }

    # 3) Falhou de verdade (ex.: 500 Internal Server Error) -> diagnosticar
    Write-Fail "Falha REAL ao criar a rede '$Name' (exit $($create.ExitCode))"
    if ($create.StdErr) { Write-Info "stderr: $($create.StdErr)" }

    $ls = Invoke-NativeCommand -FilePath "docker" -Arguments @("network","ls")
    if ($ls.StdOut) { Write-Info "docker network ls:`n$($ls.StdOut)" }

    $info = Invoke-NativeCommand -FilePath "docker" -Arguments @("info","--format","{{.ServerErrors}}")
    if ($info.StdOut -and $info.StdOut -ne "[]" -and $info.StdOut -ne "<no value>") {
        Write-Warn "ServerErrors do daemon: $($info.StdOut)"
    }

    Write-Warn "Causa provavel: Docker Desktop/engine Linux instavel ou reiniciando."
    Write-Warn "Acao recomendada:"
    Write-Warn "  1) Docker Desktop -> Troubleshoot -> Restart"
    Write-Warn "  2) Aguardar 'Engine running'"
    Write-Warn "  3) Rodar manualmente: docker network create $Name"
    Write-Warn "  4) Reexecutar este script"
    throw "Nao foi possivel garantir a rede '$Name'. Veja o diagnostico acima."
}

# --- 'docker compose up' com streaming, sem quebrar StrictMode/Stop --------
function Invoke-ComposeUp {
    param(
        [string]$WorkDir,
        [string[]]$ComposeArgs
    )
    Push-Location $WorkDir
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"   # stderr nativo nao deve abortar o script
    try {
        & docker @ComposeArgs
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
        Pop-Location
    }
    return $code
}


function Invoke-Api {
    param(
        [string]$Method = "GET",
        [string]$Uri,
        [hashtable]$Body = @{},
        [string]$ApiKey = "",
        [switch]$IgnoreErrors
    )
    $headers = @{ "Content-Type" = "application/json" }
    if ($ApiKey -ne "") { $headers["X-API-Key"] = $ApiKey }
    try {
        if ($Method -eq "GET") {
            return Invoke-RestMethod -Uri $Uri -Method GET -Headers $headers
        } else {
            $json = $Body | ConvertTo-Json -Depth 10
            return Invoke-RestMethod -Uri $Uri -Method $Method -Headers $headers -Body $json
        }
    } catch {
        if ($IgnoreErrors) { return $null }
        Write-Fail "Erro $Method $Uri : $($_.Exception.Message)"
        throw
    }
}

function Wait-HttpHealthy {
    param([string]$Uri, [string]$Nome, [int]$MaxTentativas = 40, [int]$IntervaloSec = 3)
    Write-Info "Aguardando $Nome ($Uri)..."
    for ($i = 1; $i -le $MaxTentativas; $i++) {
        try {
            $r = Invoke-RestMethod -Uri $Uri -Method GET -TimeoutSec 5 -ErrorAction Stop
            if ($null -ne $r) { Write-Ok "$Nome esta UP"; return $true }
        } catch {
            # silencioso: servico ainda subindo
        }
        Write-Info "  tentativa $i/$MaxTentativas..."
        Start-Sleep -Seconds $IntervaloSec
    }
    Write-Fail "$Nome nao respondeu apos $MaxTentativas tentativas"
    return $false
}

# --- Limpar tudo -------------------------------------------------------------
if ($LimparTudo) {
    Write-Hdr "LIMPAR TUDO"
    Write-Warn "Vai derrubar containers E apagar volumes (banco, redis, rabbitmq)"
    $confirm = Read-Host "Confirma? (s/N)"
    if ($confirm -ne "s") {
        Write-Info "Cancelado."
        exit 0
    }
    Write-Step 1 "Derrubando RideFleet"
    Set-Location $RF_DIR
    docker compose down -v --remove-orphans 2>&1 | Out-Null
    Write-Step 2 "Derrubando Core"
    Set-Location $CORE_DIR
    docker compose -f infra/docker-compose.core.yml down -v --remove-orphans 2>&1 | Out-Null
    Write-Ok "Tudo limpo. API Keys perdidas - sera necessario registrar novamente."
    exit 0
}

# --- Diagnostico -------------------------------------------------------------
if ($Diagnostico) {
    Write-Hdr "DIAGNOSTICO"
    Write-Step 1 "docker ps"
    docker ps --format "table {{.Names}}`t{{.Status}}`t{{.Ports}}"
    Write-Step 2 "Core health"
    try { Invoke-RestMethod "$CORE_BASE/health" | ConvertTo-Json } catch { Write-Warn "Core nao responde" }
    Write-Step 3 "RideFleet health"
    try { Invoke-RestMethod "$RF_BASE/health" | ConvertTo-Json } catch { Write-Warn "RideFleet nao responde" }
    Write-Step 4 "Logs Core (ultimas 60 linhas)"
    docker logs ridefleet-core --tail 60 2>&1
    Write-Step 5 "Logs RabbitMQ (ultimas 30 linhas)"
    docker logs ridefleet-rabbitmq --tail 30 2>&1
    Write-Step 6 "Logs RideFleet-1 (ultimas 60 linhas)"
    docker logs ridefleet-grupo-a-1 --tail 60 2>&1
    Write-Step 7 "Logs RideFleet-2 (ultimas 60 linhas)"
    docker logs ridefleet-grupo-a-2 --tail 60 2>&1
    exit 0
}

# =============================================================================
#  SETUP - Sobe Core e RideFleet
# =============================================================================
Write-Hdr "SETUP - Subindo infraestrutura Docker"

Write-Step 1 "Garantir rede compartilhada ridefleet-net (robusto)"
Ensure-DockerNetwork -Name "ridefleet-net" | Out-Null

Write-Step 2 "Subindo Core (PostgreSQL + RabbitMQ + Core + Prometheus + Grafana)"
$coreCode = Invoke-ComposeUp -WorkDir $CORE_DIR `
    -ComposeArgs @("compose","-f","infra/docker-compose.core.yml","up","-d","--build")
if ($coreCode -ne 0) {
    Write-Warn "docker compose (Core) retornou exit $coreCode (pode ser apenas warning de build)"
} else {
    Write-Ok "Core compose iniciado"
}

Write-Step 3 "Subindo RideFleet (PostgreSQL + Redis + Nginx + 2 instancias)"
$rfCode = Invoke-ComposeUp -WorkDir $RF_DIR `
    -ComposeArgs @("compose","up","-d","--build")
if ($rfCode -ne 0) {
    Write-Warn "docker compose (RideFleet) retornou exit $rfCode"
    Write-Warn "Causa comum: a rede external 'ridefleet-net' nao existe -> ver Passo 1."
} else {
    Write-Ok "RideFleet compose iniciado"
}

Write-Step 4 "Aguardando Core health ($CORE_BASE/health)"
$coreOk = Wait-HttpHealthy -Uri "$CORE_BASE/health" -Nome "Core" -MaxTentativas 40 -IntervaloSec 3
if (-not $coreOk) {
    Write-Fail "Core nao subiu. Rode: .\simula_docker_real.ps1 -Diagnostico"
    exit 1
}

Write-Step 5 "Aguardando RideFleet health ($RF_BASE/health)"
$rfOk = Wait-HttpHealthy -Uri "$RF_BASE/health" -Nome "RideFleet" -MaxTentativas 40 -IntervaloSec 3
if (-not $rfOk) {
    Write-Fail "RideFleet nao subiu. Rode: .\simula_docker_real.ps1 -Diagnostico"
    exit 1
}

Write-Step 6 "Registrando grupo RideFleet no Core"
$regRF = Invoke-Api -Method POST -Uri "$CORE_BASE/groups/register" -Body @{
    groupId      = $GRUPO_RF
    groupName    = "RideFleet Grupo A - SIN 142"
    serviceUrl   = "http://host.docker.internal:8000"
    contactEmail = "ridefleet@sin142.example.com"
}
$API_KEY_RF = $regRF.apiKey
Write-Ok "API Key RideFleet: $($API_KEY_RF.Substring(0,12))..."

Write-Step 7 "Registrando grupo fake origem-teste no Core"
$regFake = Invoke-Api -Method POST -Uri "$CORE_BASE/groups/register" -Body @{
    groupId      = $GRUPO_FAKE
    groupName    = "Grupo Fake de Testes"
    serviceUrl   = $GRUPO_FAKE_URL
    contactEmail = "fake@sin142.example.com"
}
$API_KEY_FAKE = $regFake.apiKey
Write-Ok "API Key Fake: $($API_KEY_FAKE.Substring(0,12))..."

Write-Ok "Setup concluido."
Write-Info "API_KEY_RF   = $API_KEY_RF"
Write-Info "API_KEY_FAKE = $API_KEY_FAKE"

# =============================================================================
#  FLUXO A - Corrida LOCAL (PostgreSQL + Redis reais, sem Core)
# =============================================================================
if ((-not $SomenteFluxoB) -and (-not $SomenteFluxoC)) {
    Write-Hdr "FLUXO A - Corrida LOCAL (sem delegacao)"

    Write-Step 1 "Cadastrar motorista disponivel"
    $driverA = Invoke-Api -Method POST -Uri "$RF_BASE/drivers/" -Body @{
        name          = "Ana Silva"
        license_plate = "MG-0042"
        phone         = "31999110042"
    }
    Write-Ok "Motorista: $($driverA.name) | id=$($driverA.id.Substring(0,8))..."

    Write-Step 2 "Cadastrar passageiro"
    $passengerA = Invoke-Api -Method POST -Uri "$RF_BASE/passengers/" -Body @{
        name  = "Carlos Souza"
        phone = "31988220001"
    }
    Write-Ok "Passageiro: $($passengerA.name) | id=$($passengerA.id.Substring(0,8))..."

    Write-Step 3 "Verificar overflow (deve ser false)"
    $ovA = Invoke-Api -Uri "$RF_BASE/rides/overflow/check"
    Write-Info "Motoristas disponiveis: $($ovA.available_drivers)"
    Write-Info "Deve delegar: $($ovA.should_delegate)"
    if ($ovA.should_delegate -eq $false) {
        Write-Ok "Sem overflow - corrida sera atendida localmente"
    } else {
        Write-Warn "Overflow detectado - verifique MIN_AVAILABLE_DRIVERS"
    }

    Write-Step 4 "Criar corrida"
    $rideA = Invoke-Api -Method POST -Uri "$RF_BASE/rides/" -Body @{
        passenger_id = $passengerA.id
        origin       = "Campus UFV - Vicosa MG"
        destination  = "Rodoviaria de Vicosa MG"
    }
    Write-Ok "Corrida criada: $($rideA.id.Substring(0,8))... | status=$($rideA.status)"

    Write-Step 5 "Progressao de estados: request > match > confirm > in_transit > complete"
    $bodyMatch = @{ new_status = "match"; driver_id = $driverA.id }
    $r1 = Invoke-Api -Method PATCH -Uri "$RF_BASE/rides/$($rideA.id)/status" -Body $bodyMatch
    Write-Ok "  $($r1.status)"
    Start-Sleep -Milliseconds 300

    $bodyConfirm = @{ new_status = "confirm" }
    $r2 = Invoke-Api -Method PATCH -Uri "$RF_BASE/rides/$($rideA.id)/status" -Body $bodyConfirm
    Write-Ok "  $($r2.status)"
    Start-Sleep -Milliseconds 300

    $bodyTransit = @{ new_status = "in_transit" }
    $r3 = Invoke-Api -Method PATCH -Uri "$RF_BASE/rides/$($rideA.id)/status" -Body $bodyTransit
    Write-Ok "  $($r3.status)"
    Start-Sleep -Milliseconds 300

    $bodyComplete = @{ new_status = "complete" }
    $r4 = Invoke-Api -Method PATCH -Uri "$RF_BASE/rides/$($rideA.id)/status" -Body $bodyComplete
    Write-Ok "  $($r4.status)"

    Write-Step 6 "Estado final"
    $finalA = Invoke-Api -Uri "$RF_BASE/rides/$($rideA.id)"
    Write-Info "id:             $($finalA.id.Substring(0,8))..."
    Write-Info "status:         $($finalA.status)"
    Write-Info "driver_id:      $($finalA.driver_id)"
    Write-Info "delegated_from: $($finalA.delegated_from)"

    if ($finalA.status -eq "complete") {
        Write-Ok "FLUXO A - SUCESSO: status=complete, driver atribuido, sem delegacao"
    } else {
        Write-Warn "FLUXO A - status inesperado: $($finalA.status)"
    }
}

# =============================================================================
#  FLUXO B - Delegacao de SAIDA (overflow > outbox > Core)
# =============================================================================
if ((-not $SomenteFluxoA) -and (-not $SomenteFluxoC)) {
    Write-Hdr "FLUXO B - Delegacao de SAIDA (overflow)"

    Write-Step 1 "Colocar todos os motoristas OFFLINE para forcar overflow"
    $allDrivers = Invoke-Api -Uri "$RF_BASE/drivers/" -IgnoreErrors
    if ($null -ne $allDrivers) {
        foreach ($d in $allDrivers) {
            Invoke-Api -Method PATCH -Uri "$RF_BASE/drivers/$($d.id)" `
                -Body @{ status = "offline" } -IgnoreErrors | Out-Null
        }
    }
    Start-Sleep -Seconds 1

    $ovB = Invoke-Api -Uri "$RF_BASE/rides/overflow/check"
    Write-Info "Motoristas disponiveis: $($ovB.available_drivers)"
    Write-Info "Deve delegar: $($ovB.should_delegate)"
    if ($ovB.should_delegate -eq $true) {
        Write-Ok "Overflow ativo - proxima corrida vai para a outbox"
    } else {
        Write-Warn "Overflow nao detectado (MIN_AVAILABLE_DRIVERS pode ser 0)"
    }

    Write-Step 2 "Cadastrar passageiro para teste de overflow"
    $passengerB = Invoke-Api -Method POST -Uri "$RF_BASE/passengers/" -Body @{
        name  = "Beatriz Lima"
        phone = "31977330002"
    }
    Write-Ok "Passageiro: $($passengerB.name)"

    Write-Step 3 "Criar corrida (deve ir para outbox)"
    $rideB = Invoke-Api -Method POST -Uri "$RF_BASE/rides/" -Body @{
        passenger_id = $passengerB.id
        origin       = "Praca da Estacao - BH MG"
        destination  = "Aeroporto de Confins MG"
    }
    Write-Ok "Corrida criada: $($rideB.id.Substring(0,8))... | status=$($rideB.status)"

    Start-Sleep -Seconds 3

    Write-Step 4 "Verificar fila outbox"
    $ovB2 = Invoke-Api -Uri "$RF_BASE/rides/overflow/check"
    Write-Info "inbox:  $($ovB2.queue.inbox)"
    Write-Info "outbox: $($ovB2.queue.outbox)"
    if ($ovB2.queue.outbox -gt 0) {
        Write-Ok "Corrida na outbox - delegation_worker enviara ao Core"
    } else {
        Write-Warn "Outbox vazio - delegation_worker pode ja ter processado"
        Write-Info "Verifique: docker logs ridefleet-grupo-a-1 --tail 30"
    }

    Write-Step 5 "Logs recentes do delegation_worker"
    Write-Info "Execute para ver a delegacao:"
    Write-Info "  docker logs ridefleet-grupo-a-1 --tail 50"
    Write-Info "Procure por: corrida_delegada_core | corrida_enviada_core"

    Write-Ok "FLUXO B - SUCESSO se outbox > 0 OU log mostra corrida_delegada_core"
    Write-Info "Criterio: corrida na outbox E delegation_worker chama POST /api/v1/rides no Core"
}

# =============================================================================
#  FLUXO C - Delegacao de ENTRADA (Core cria corrida, RideFleet vence leilao)
# =============================================================================
if ((-not $SomenteFluxoA) -and (-not $SomenteFluxoB)) {
    Write-Hdr "FLUXO C - Delegacao de ENTRADA (Core > leilao > RideFleet vence)"

    Write-Step 1 "Garantir motoristas disponiveis no RideFleet"
    $motoristas = @(
        @{ name = "Diego Ferreira"; license_plate = "SP-9988"; phone = "11988001122" },
        @{ name = "Luisa Martins";  license_plate = "RJ-4455"; phone = "21977003344" }
    )
    foreach ($m in $motoristas) {
        $d = Invoke-Api -Method POST -Uri "$RF_BASE/drivers/" -Body $m -IgnoreErrors
        if ($null -ne $d) { Write-Ok "Motorista: $($d.name) ($($d.license_plate))" }
    }
    # Garante AVAILABLE
    $allD = Invoke-Api -Uri "$RF_BASE/drivers/" -IgnoreErrors
    if ($null -ne $allD) {
        foreach ($d in $allD) {
            if ($d.status -ne "available") {
                Invoke-Api -Method PATCH -Uri "$RF_BASE/drivers/$($d.id)" `
                    -Body @{ status = "available" } -IgnoreErrors | Out-Null
            }
        }
    }
    $ovC = Invoke-Api -Uri "$RF_BASE/rides/overflow/check"
    Write-Info "Motoristas disponiveis: $($ovC.available_drivers)"
    if ($ovC.available_drivers -gt 0) {
        Write-Ok "RideFleet com capacidade - participara do leilao"
    } else {
        Write-Warn "Sem motoristas! Verifique os logs."
    }

    Write-Step 2 "Criar corrida no Core usando grupo-fake (inicia leilao)"
    $origin = @{
        lat = -20.7546; lng = -42.8825
        street = "Rua das Flores"; number = "100"
        city = "Vicosa"; state = "MG"
    }
    $dest = @{
        lat = -20.7600; lng = -42.8900
        street = "Av. P.H. Rolfs"; number = "500"
        city = "Vicosa"; state = "MG"
    }
    $rideCore = Invoke-Api -Method POST -Uri "$CORE_BASE/rides" `
        -ApiKey $API_KEY_FAKE -Body @{
            originServiceId       = $GRUPO_FAKE
            passengerId           = "passageiro-fluxo-c-001"
            origin                = $origin
            destination           = $dest
            logicalTimestamp      = 10
            auctionTimeoutSeconds = 15
        }
    $rideUuid = $rideCore.rideUuid
    Write-Ok "Corrida criada no Core: $($rideUuid.Substring(0,8))..."
    Write-Info "logicalTimestamp: $($rideCore.logicalTimestamp)"
    Write-Info "message:          $($rideCore.message)"

    Write-Step 3 "Aguardar leilao (Core chama /rides/incoming no RideFleet)"
    Write-Info "O Core esta executando scatter-gather. Aguardando 20s..."
    Start-Sleep -Seconds 20

    Write-Step 4 "Consultar propostas no Core"
    $proposals = Invoke-Api -Uri "$CORE_BASE/rides/$rideUuid/proposals" `
        -ApiKey $API_KEY_RF -IgnoreErrors
    if (($null -ne $proposals) -and ($null -ne $proposals.proposals) -and ($proposals.proposals.Count -gt 0)) {
        Write-Ok "Propostas recebidas ($($proposals.proposals.Count)):"
        foreach ($p in $proposals.proposals) {
            if ($p.isWinner -eq 1) {
                Write-Ok "  VENCEDOR: grupo=$($p.groupId) | preco=$($p.estimatedPrice) | ETA=$($p.estimatedEta)s"
            } else {
                Write-Info "  grupo=$($p.groupId) | status=$($p.status)"
            }
        }
    } else {
        Write-Warn "Sem propostas ainda. Possiveis causas:"
        Write-Warn "  1) Core nao alcancou o RideFleet (verificar serviceUrl=http://host.docker.internal:8000)"
        Write-Warn "  2) RideFleet estava sem motoristas"
        Write-Warn "  3) Leilao ainda em andamento"
        Write-Info "Logs Core:      docker logs ridefleet-core --tail 50"
        Write-Info "Logs RideFleet: docker logs ridefleet-grupo-a-1 --tail 50"
    }

    Write-Step 5 "Consultar status da corrida no Core"
    $statusCore = Invoke-Api -Uri "$CORE_BASE/rides/$rideUuid/status" `
        -ApiKey $API_KEY_RF -IgnoreErrors
    if ($null -ne $statusCore) {
        Write-Info "status:          $($statusCore.status)"
        Write-Info "auctionStatus:   $($statusCore.auctionStatus)"
        Write-Info "assignedService: $($statusCore.assignedServiceId)"
        Write-Info "lockHeldBy:      $($statusCore.lockHeldBy)"
    }

    Write-Step 6 "Verificar fila inbox do RideFleet"
    $healthC = Invoke-Api -Uri "$RF_BASE/health" -IgnoreErrors
    if ($null -ne $healthC) {
        Write-Info "status: $($healthC.status)"
        Write-Info "inbox:  $($healthC.queue.inbox)"
        Write-Info "outbox: $($healthC.queue.outbox)"
    }

    Write-Step 7 "Aguardar inbox_worker processar corrida (15s)"
    Start-Sleep -Seconds 15

    Write-Step 8 "Consultar audit log da corrida no Core"
    $audit = Invoke-Api -Uri "$CORE_BASE/rides/$rideUuid/audit" `
        -ApiKey $API_KEY_RF -IgnoreErrors
    if (($null -ne $audit) -and ($null -ne $audit.events)) {
        Write-Ok "Audit log ($($audit.events.Count) eventos):"
        foreach ($ev in $audit.events) {
            Write-Info "  [ts=$($ev.logicalTimestamp)] $($ev.eventType) | service=$($ev.serviceId)"
        }
    }

    Write-Step 9 "Status final da corrida no Core"
    $finalStatus = Invoke-Api -Uri "$CORE_BASE/rides/$rideUuid/status" `
        -ApiKey $API_KEY_RF -IgnoreErrors
    if ($null -ne $finalStatus) {
        Write-Info "status final: $($finalStatus.status)"
        if ($finalStatus.status -eq "complete") {
            Write-Ok "FLUXO C - SUCESSO COMPLETO: saga encerrada em complete"
        } elseif ($finalStatus.status -eq "match") {
            Write-Ok "FLUXO C - Leilao vencido, inbox_worker processando (aguarde +30s)"
        } else {
            Write-Warn "Status: $($finalStatus.status) - verifique logs"
        }
    }

    Write-Info ""
    Write-Info "Criterios de sucesso Fluxo C:"
    Write-Info "  OK proposals retorna groupId=ridefleet-grupo-a, status=accepted"
    Write-Info "  OK status da corrida = match (leilao vencido) ou complete (saga concluida)"
    Write-Info "  OK audit log contem: auction_closed, lock_acquired, confirm, in_transit, complete"
    Write-Info "  OK inbox_worker logou: corrida_delegada_recebida, transicao_saga"
}

# =============================================================================
#  RESUMO FINAL
# =============================================================================
Write-Hdr "RESUMO"
Write-Info "URLs:"
Write-Info "  RideFleet API  : $RF_BASE"
Write-Info "  RideFleet Docs : $RF_BASE/docs"
Write-Info "  Core API       : $CORE_BASE"
Write-Info "  Core Docs      : http://localhost:8080/docs"
Write-Info "  RabbitMQ Mgmt  : http://localhost:15672  (ridefleet/ridefleet)"
Write-Info "  Grafana        : http://localhost:3000   (admin/ridefleet)"
Write-Info ""
Write-Info "Comandos uteis:"
Write-Info "  docker logs -f ridefleet-grupo-a-1"
Write-Info "  docker logs -f ridefleet-core"
Write-Info "  .\simula_docker_real.ps1 -Diagnostico"
Write-Info "  .\simula_docker_real.ps1 -LimparTudo"
Write-Host ""