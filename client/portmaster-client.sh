#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
CONFIG_FILE="$SCRIPT_DIR/portmaster.conf"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Configuration file not found."
    exit 1
fi
source "$CONFIG_FILE"

# Преобразуем IP-адрес в вид "10.8.1." (отбрасываем последний октет)
#network_addr=$(echo "$SERVER_IP" | sed 's/\.[0-9]*$//')

# Поиск сети по адресу "10.8.1." в выводе ifconfig
#if ifconfig | grep -q "$network_addr"; then
#    echo "VPN interface is up"
#else
#    echo "Couldn't find VPN interface"
#    exit
#fi

# Flag indicating that we are sending a request to clear all forwarded ports
# Set in the disconnect function
DISCONNECT_REQUEST=0

# Function to check if a string is a number
is_number() {
    [[ "$1" =~ ^[0-9]+$ ]]
}

# Функция для замены переменных в шаблоне plist
function update_plist {
    local template_file="$1"   # Путь к шаблону
    local output_file="$2"      # Путь к выходному файлу
    local portmaster_client="$3" # Значение для PORTMASTER_CLIENT
    local port_master_conf="$4"  # Значение для PORT_MASTER_CONF

    # Копируем шаблон в выходной файл
    cp "$template_file" "$output_file"

    # Заменяем переменные на переданные значения
    sed -i.bak "s|\$PORTMASTER_CLIENT|$portmaster_client|g" "$output_file"
    sed -i.bak "s|\$PORT_MASTER_CONF|$port_master_conf|g" "$output_file"

    # Удаляем резервную копию, созданную sed
    rm "$output_file.bak"

    echo "Файл $output_file успешно обновлен."
}

# Function to add a port
add_port() {
    local port="$1"

    # Check if the port is valid
    if ! is_number "$port"; then
        echo "Error: '$port' is not a valid port number."
        return 1
    fi

    # Проверка, есть ли уже порт в массиве PORTS
    if [[ " ${PORTS[*]} " =~ " $port " ]]; then 
        echo "Error: Port $port is already added."
        return 1
    fi

    # Add the port to the PORTS array
    PORTS+=("$port")

   # Update the configuration file

    update_config
    echo "Port $port added."
    return 0
}

update_config() {
   echo "Updating configuration file..."
   {
        echo "SERVER_IP=\"$SERVER_IP\""
        echo "SERVER_PORT=$SERVER_PORT"
        echo "PORTS=(${PORTS[@]})"
   } > "$CONFIG_FILE"
}

# Delete port
delete_port() {
    local port="$1"
    local index=-1
    local i=0

    # Находим индекс порта в массиве PORTS
    for p in "${PORTS[@]}"; do
        if [[ "$p" == "$port" ]]; then
            index=$i
            break  # Выходим из цикла после нахождения порта
        fi
        ((i++))
    done

    # Проверяем, найден ли порт
    if [[ "$index" == "-1" ]]; then
        echo "Error: Port $port not found in configuration."
        return 1
    fi

    # Удаляем порт из массива с помощью unset
    unset PORTS["$index"]

    # Обновляем конфигурационный файл
    update_config
    echo "Port $port deleted."
 
}


# Function to ask to remove all port forwarding rules for the current client
disconnect() {
    DISCONNECT_REQUEST=1
    return 0  # Return 0 to indicate success
}

# Function to display a notification on macOS
notify_mac() {
    osascript -e "display notification \"$1\" with title \"Portmaster Client\""
}

# Function to test port forwarding
test_port_forwarding() {
    local port="$1"
    echo "Testing port forwarding for port $port..."
    # Start a server that will listen on the specified port and send a prompt, then receive a message
    {
        # Accept a connection, send a prompt, wait for a message, and respond
        echo -e "Server is ready for connection. Enter a message:\n" | nc -l -w 20 -p "$port" | while read client_message; do
            echo "Message from client: $client_message"
            echo "Message received. Test successful."
            pkill -f "nc -l -w 20 -p $port"
            break
        done
    } &
    
    local nc_pid=$!
    echo "Waiting for connection for 20 seconds..."
    # Wait for the server to finish
    wait $nc_pid
    echo "Test completed."
}

uninstall_agent(){
   echo "Portmaster agent will be uninstalled"
    read -p "Do you want to continue? (y/N):" answer
    answer=${answer:-N}
    case "$answer" in
        [Yy]* )            
            OUTPUT_FILE="$SCRIPT_DIR/mac/portmaster_agent.plist"                        
            launchctl unload -w $OUTPUT_FILE
            exit
            ;;
        [Nn]* )
            echo "Uninstalation aborted."
            exit
            ;;
        * )
            echo "Please, Y or N"
            install_agent  # Рекурсивный вызов функции для повторного запроса
            ;;
    esac

}

install_agent() {
    echo "Client will install MacOS portmaster agent so you don't need to run portmaster-client every time you connect to VPN server."
    echo "The agent will only run when you connect to VPN or edit and save portmaster.conf"
    echo "You can easily remove agent by running portmaster-client.sh --uninstall"
    read -p "Do you want to continue? (y/N):" answer
    answer=${answer:-N}
    case "$answer" in
        [Yy]* )
            echo "Installing..."
            TEMPLATE_FILE="$SCRIPT_DIR/mac/agent_template.plist"
            OUTPUT_FILE="$SCRIPT_DIR/mac/portmaster_agent.plist"
            PORTMASTER_CLIENT="$SCRIPT_DIR/portmaster-client.sh"
            update_plist $TEMPLATE_FILE $OUTPUT_FILE $PORTMASTER_CLIENT $CONFIG_FILE
            launchctl load -w $OUTPUT_FILE
            echo "Installed!"
            echo "You can easyly remove agent by running portmaster-client.sh --uninstall"            
            ;;
        [Nn]* )
            echo "Instalation aborted."
            exit
            ;;
        * )
            echo "Please, Y or N"
            install_agent  # Рекурсивный вызов функции для повторного запроса
            ;;
    esac
}

# Check arguments
if [[ "$#" > 0 ]]; then
  # Handle arguments
  case "$1" in
    --add|-add)
        if [ "$#" -ne 2 ]; then
            echo "Error: Specify exactly one port to add."
            exit 1
        fi
        if ! is_number "$2"; then
            echo "Error: '$2' is not a valid port number."
            exit 1
        fi
        add_port "$2"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            notify_mac "Port $2 deleted. Configuration updated."
        fi
        ;;

    --delete|-delete)
        if [ "$#" -ne 2 ]; then
            echo "Error: Specify exactly one port to delete."
            exit 1
        fi
        if ! is_number "$2"; then
            echo "Error: '$2' is not a valid port number."
            exit 1
        fi
        if delete_port "$2"; then
            # Send notification on macOS
            if [[ "$OSTYPE" == "darwin"* ]]; then
                notify_mac "Port $2 deleted. Configuration updated."
            fi
        fi
        ;;

    --disconnect|-disconnect)
        disconnect
        ;;

    --test)
        if [ "$#" -ne 2 ]; then
            echo "Error: Specify exactly one port to test."
            exit 1
        fi
        if ! is_number "$2"; then
            echo "Error: '$2' is not a valid port number."
            exit 1
        fi
        test_port_forwarding "$2"
        ;;
    --install)
        install_agent
        ;;

    *)
        echo "Error: Invalid argument '$1'. Valid arguments: --add, --delete, --disconnect, --test."
        exit 1
        ;;
  esac
fi

if [[ $DISCONNECT_REQUEST -eq 0 ]]; then
    REQUEST="PORTS: $(echo "${PORTS[*]}" | tr ' ' ',')"
    response=$(echo "$REQUEST" | nc -w1  "$SERVER_IP" "$SERVER_PORT")
else
    # Client can send any IP as IP will be ignored, because disconnect requests to different IPs are accepted only from server
    response=$(echo "$REQUEST" | nc "$SERVER_IP" "$SERVER_PORT")
    echo "Disconnect request sent."
fi

# Send notification on macOS with the results
if [[ "$OSTYPE" == "darwin"* ]]; then
    notify_mac "$response"
else
    echo "$response"
fi
