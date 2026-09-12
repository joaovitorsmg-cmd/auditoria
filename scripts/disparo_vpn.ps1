# disparo_vpn.ps1
# Monitora a conexao WatchGuard SSL VPN e dispara atualizar_compras.py automaticamente.
# Configure no Agendador de Tarefas para rodar ao iniciar o Windows (como usuario atual).
#
# COMO INSTALAR:
#   1. Abra o PowerShell como Administrador
#   2. Execute: powershell -ExecutionPolicy Bypass -File "C:\Caminho\Para\disparo_vpn.ps1" -Instalar
#   OU execute manualmente: powershell -ExecutionPolicy Bypass -File "C:\Caminho\Para\disparo_vpn.ps1"

param([switch]$Instalar)

# ── CONFIGURACAO ──────────────────────────────────────────────────────────────
$SCRIPT_DIR   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PYTHON_SCRIPT = Join-Path $SCRIPT_DIR "atualizar_compras.py"
$LOG_FILE      = Join-Path $SCRIPT_DIR "disparo_vpn.log"

# IP virtual da VPN WatchGuard (prefixo da Virtual IP — ajuste se necessario)
$VPN_IP_PREFIX = "10.212"

# Intervalo de verificacao em segundos
$INTERVALO_SEGUNDOS = 30

# ── INSTALAR NO AGENDADOR DE TAREFAS ─────────────────────────────────────────
if ($Instalar) {
    $acao    = New-ScheduledTaskAction -Execute "powershell.exe" `
                 -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    $gatilho = New-ScheduledTaskTrigger -AtLogOn
    $config  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask `
        -TaskName "Agroquima_Disparo_VPN" `
        -Action   $acao `
        -Trigger  $gatilho `
        -Settings $config `
        -RunLevel Highest `
        -Force | Out-Null
    Write-Host "Tarefa 'Agroquima_Disparo_VPN' registrada. Sera executada automaticamente ao fazer login." -ForegroundColor Green
    exit 0
}

# ── FUNCOES ───────────────────────────────────────────────────────────────────
function Escrever-Log($msg) {
    $linha = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $msg"
    Write-Host $linha
    Add-Content -Path $LOG_FILE -Value $linha -Encoding UTF8
}

function VPN-Conectada {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -like "$VPN_IP_PREFIX.*" }
    return ($null -ne $ips -and $ips.Count -gt 0)
}

function Executar-Atualizacao {
    Escrever-Log "VPN detectada! Iniciando atualizacao..."
    try {
        $resultado = & python "$PYTHON_SCRIPT" 2>&1
        $resultado | ForEach-Object { Escrever-Log "  $_" }
        Escrever-Log "Atualizacao concluida."
    } catch {
        Escrever-Log "ERRO ao executar Python: $_"
    }
}

# ── LOOP PRINCIPAL ────────────────────────────────────────────────────────────
Escrever-Log "Monitor VPN iniciado. Aguardando conexao WatchGuard ($VPN_IP_PREFIX.x.x)..."

$vpnEstabaConectada = $false
$ultimaExecucao     = [DateTime]::MinValue

while ($true) {
    $conectada = VPN-Conectada

    if ($conectada -and -not $vpnEstabaConectada) {
        # VPN acabou de conectar — aguarda 5s para estabilizar
        Start-Sleep -Seconds 5
        Executar-Atualizacao
        $ultimaExecucao = Get-Date
    }

    # Re-executa se VPN ainda conectada e passou mais de 23h desde a ultima execucao
    if ($conectada -and $vpnEstabaConectada) {
        $horas = (Get-Date) - $ultimaExecucao
        if ($horas.TotalHours -ge 23) {
            Escrever-Log "Re-execucao diaria..."
            Executar-Atualizacao
            $ultimaExecucao = Get-Date
        }
    }

    $vpnEstabaConectada = $conectada
    Start-Sleep -Seconds $INTERVALO_SEGUNDOS
}
