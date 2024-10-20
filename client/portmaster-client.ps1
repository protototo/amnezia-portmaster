# Main script logic (parsing positional arguments)
param(
    [string[]]$Args  # Используем массив для захвата всех аргументов
)

# Переменные по умолчанию
$Action = $null
$Port = $null

# Обработка аргументов
foreach ($arg in $Args) {
    if ($arg -eq '-add') {
        $Action = 'add'
    } elseif ($arg -eq '-delete') {
        $Action = 'delete'
    } elseif ($arg -eq '-disconnect') {
        $Action = 'disconnect'
    } elseif ($arg -eq '-test') {
        $Action = 'test'
    } elseif ($Action -and -not $Port) {
        $Port = $arg  # Сохраняем порт, если он указан после действия
    }
}

# Define the configuration file path
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ConfigFile = "$ScriptDir\portmaster.conf"

# Check if the configuration file exists
if (-Not (Test-Path $ConfigFile)) {
    Write-Host "Configuration file not found."
    exit 1
}

function Read-Config {

    if (-Not (Test-Path $ConfigFile)) {
        Write-Host "Configuration file not found: $ConfigFile"
        return $null
    }

    # Создаем хэш-таблицу для хранения значений
    $config = @{}

    # Читаем файл построчно
    $lines = Get-Content $ConfigFile
    foreach ($line in $lines) {
        # Пропускаем пустые строки и комментарии
        if ([string]::IsNullOrWhiteSpace($line) -or $line -match '^\s*#') {
            continue
        }

        # Используем регулярное выражение для извлечения переменных
        if ($line -match '^(?<key>\w+)\s*=\s*"?(?<value>[^"]*)"?\s*$') {
            $key = $matches['key']
            $value = $matches['value']
            
            # Обрабатываем массив PORTS
            if ($key -eq 'PORTS') {
                # Убираем скобки, разбиваем по пробелу
                $ports = $value -replace '[()]', '' -split '\s+'
                # Удаляем пустые значения
                $ports = $ports | Where-Object { -Not [string]::IsNullOrWhiteSpace($_) }
                $config[$key] = @($ports)
            } else {
                $config[$key] = $value
            }
        }
    }

    return $config
}


$config = Read-Config

# Проверяем значения
Write-Host "SERVER_IP: $($config['SERVER_IP'])"
Write-Host "SERVER_PORT: $($config['SERVER_PORT'])"
Write-Host "PORTS: $($config['PORTS'] -join ', ')"
$PORTS = $config['PORTS']
$SERVER_IP = $config['SERVER_IP']
$SERVER_PORT = $config['SERVER_PORT']

# Function to ensure the BurntToast module is installed
function Ensure-BurntToastModule {
    if (-Not (Get-Module -ListAvailable -Name BurntToast)) {
        Write-Host "BurntToast module not found. Installing..."
        try {
            Install-Module -Name BurntToast -Force -Scope CurrentUser -ErrorAction Stop
            Write-Host "BurntToast module installed successfully."
        } catch {
            Write-Host "Error: Failed to install BurntToast module. $_"
            exit 1
        }
    } else {
        Write-Host "BurntToast module is already installed."
    }
}

# Run the check and install BurntToast if needed
Ensure-BurntToastModule

# Import the BurntToast module
Import-Module BurntToast -ErrorAction Stop

# Function to check if a string is a number
function Is-Number {
    param($Value)
    return $Value -match '^\d+$'
}

# Function to show a notification
function Show-Notification {
    param($Message, $Title = "Portmaster Client")
    New-BurntToastNotification -Text $Title, $Message
}

# Function to update the configuration file
function Update-Config {
    Write-Host "Updating configuration file..."
    $configContent = @"
SERVER_IP="$SERVER_IP"
SERVER_PORT=$SERVER_PORT
PORTS=($($PORTS -join ' '))
"@
    Set-Content -Path $ConfigFile -Value $configContent
}

# Function to add a port
function Add-Port {
    param($Port)
    Write-Host "TRYING TO ADD PORT: $Port"
    # Check if the port is valid
    if (-Not (Is-Number $Port)) {
        Show-Notification "Error: '$Port' is not a valid port number." "Portmaster Error"
        Write-Host "Error: '$Port' is not a valid port number." "Portmaster Error"
        return 1
    }

    # Check if the port already exists
    if ($global:PORTS -contains $Port) {
        Show-Notification "Error: Port $Port is already added." "Portmaster Error"
        Write-Host "Error: Port $Port is already added." "Portmaster Error"
        return 1
    }

    #$global:PORTS = @($global:PORTS; $Port)
    $global:PORTS +=  $Port.Trim()
    Write-Host "UPDATED PORTS: $($global:PORTS -join ', ')"
    # Update the configuration file
    Update-Config
    Show-Notification "Port $Port added successfully."
    return 0
}

# Function to delete a port
function Delete-Port {
    param($Port)

    # Check if the port exists
    if (-Not ($global:PORTS -contains $Port)) {
        Show-Notification "Error: Port $Port not found in configuration." "Portmaster Error"
        Write-Host "Error: Port $Port not found in configuration."
        return 1
    }

    # Remove the port from the PORTS array
    $global:PORTS = $global:PORTS | Where-Object { $_ -ne $Port }

    # Update the configuration file
    Update-Config
    Show-Notification "Port $Port deleted successfully."
    Write-Host "Port $Port deleted successfully."
    return 0
}

# Function to test port forwarding (simulate nc)
function Test-PortForwarding {
    param($Port)
    Write-Host "Testing port forwarding for port $Port..."
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([IPAddress]::Any, $Port)
        $listener.Start()
        Write-Host "Listening on port $Port..."

        # Ожидаем подключения клиента
        $client = $listener.AcceptTcpClient()
        $stream = $client.GetStream()

        # Отправляем приглашение ввести сообщение
        $writer = New-Object System.IO.StreamWriter($stream)
        $promptMessage = "Please enter a test message and press Enter:"
        $writer.WriteLine($promptMessage)
        $writer.Flush()
        Write-Host "Sent to client: $promptMessage"

        # Ожидаем ответа от клиента
        $reader = New-Object System.IO.StreamReader($stream)
        $stream.ReadTimeout = 20000  # Устанавливаем таймаут чтения в 20 секунд

        try {
            $message = $reader.ReadLine()  # Ожидаем ввода сообщения
            Write-Host "Message from client: $message"
        } catch {
            Write-Host "Error: No message received within 20 seconds. Test failed."
        }

        # Закрываем соединение
        $listener.Stop()
        Write-Host "Test completed."
    } catch {
        Write-Host "Error: Unable to test port $Port. $_"
    }
}


# Function to send data over a TCP connection and receive a response
function Send-Data {
    param(
        [string]$Message,
        [string]$ServerIP,
        [int]$ServerPort
    )
    
    try {
        # Create TCP client to connect to server
        $client = New-Object System.Net.Sockets.TcpClient($ServerIP, $ServerPort)
        $stream = $client.GetStream()
        $writer = New-Object System.IO.StreamWriter($stream)
        $reader = New-Object System.IO.StreamReader($stream)
        
        # Send the message
        $writer.WriteLine($Message)
        $writer.Flush()

        Write-Host "Message sent: $Message"
        
        # Receive the response
        $response = $reader.ReadLine()
        Write-Host "Response received: $response"
        
        # Close the connection
        $writer.Close()
        $reader.Close()
        $client.Close()

        return $response
    } catch {
        Write-Host "Error: Unable to connect to $ServerIP on port $ServerPort. $_"
        return $null
    }
}

$REQUEST = ""

switch ($Action) {
    'add' {
        if (-Not $Port) {
            Write-Host "Error: Specify exactly one port to add."
            exit 1
        }
        Add-Port $Port
        $REQUEST = "PORTS: " + "$($PORTS  -join ', ')"
    }
    'delete' {
        if (-Not $Port) {
            Write-Host "Error: Specify exactly one port to delete."
            exit 1
        }
        Delete-Port $Port
        $REQUEST = "PORTS: " + "$($PORTS  -join ', ')"
    }
    "disconnect"{
        #Running at client, so any IP will do, but IP must be present
         $REQUEST = "DISCONNECT: 10.8.1.20"
    }
    'test' {
        if (-Not $Port) {
            Write-Host "Error: Specify exactly one port to test."
            exit 1
        }
        Test-PortForwarding $Port
    }
}

if ($Action -ne "test"){
    Write-Host "REQUEST: $REQUEST"
    $response = Send-Data -Message $REQUEST -ServerIP $SERVER_IP -ServerPort $SERVER_PORT
    Write-Host $response
    Show-Notification $response
}
