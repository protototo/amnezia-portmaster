#!/bin/bash

# Specify variables
CONFIG_FILE="portmaster.conf"
SERVER_IP="10.8.0.1"  # Replace with the IP of your VPN server on the TUN interface
SERVER_PORT="50000"    # The port your portmaster listens on
#TUN_IFACE="utun3" # The name of the TUN interface of the VPN server where portmaster is running
# Flag indicating that we are sending a request to clear all forwarded ports
# Set in the disconnect function
DISCONNECT_REQUEST=0
IP="" # Determined in case of disconnect

# Function to check if a string is a number
is_number() {
    [[ "$1" =~ ^[0-9]+$ ]]
}

#add port
add_port() {
    local port="$1"

    # Check if the port is valid
    if ! is_number "$port"; then
        echo "Error: '$port' is not a valid port number."
        return 1
    fi

    # Check if the port is already in the configuration
    if grep -q "PORTS:.*\b$port\b" "$CONFIG_FILE"; then
        echo "Error: Port $port is already added."
        return 1
    fi

    # If "PORTS:" line exists, append the port with a comma and space
    if grep -q "PORTS:" "$CONFIG_FILE"; then
        # Check if the line already contains ports
        if grep -qE "PORTS:\s*[0-9]" "$CONFIG_FILE"; then
            # Append the new port with a comma and space
            sed -i.bak -E "s/(PORTS:.*[0-9])/\1, $port/" "$CONFIG_FILE"
        else
            # No ports listed yet, add the first port
            sed -i.bak -E "s/(PORTS:\s*)/\1$port/" "$CONFIG_FILE"
        fi
    else
        # Otherwise, create a new "PORTS:" line
        echo "PORTS: $port" >> "$CONFIG_FILE"
    fi

    echo "Port $port added."
    return 0
}


delete_port() {
    local port="$1"

    # Читаем строку с портами из конфигурации
    ports_line=$(grep "PORTS:" "$CONFIG_FILE")

    # Извлекаем порты, удаляя все лишние пробелы и разделители
    ports_array=($(echo "$ports_line" | sed 's/PORTS: //' | tr ',' ' '))

    # Проверяем, есть ли порт в массиве, и удаляем его
    new_ports_array=()
    port_found=0
    for p in "${ports_array[@]}"; do
        if [ "$p" == "$port" ]; then
            port_found=1
        else
            new_ports_array+=("$p")
        fi
    done

    if [ "$port_found" -eq 0 ]; then
        echo "Error: Port $port not found in configuration."
        return 1  # Return 1 to indicate an error
    fi

    # Формируем строку для конфигурации
    if [ ${#new_ports_array[@]} -eq 0 ]; then
        # Если массив пуст, просто оставляем PORTS: пустым
        new_ports_line="PORTS:"
    else
        # Иначе соединяем порты с запятыми
        new_ports_line="PORTS: $(IFS=','; echo "${new_ports_array[*]}")"
    fi

    # Обновляем конфигурационный файл
    sed -i.bak "s|$ports_line|$new_ports_line|" "$CONFIG_FILE"
    echo "Port $port deleted."
    return 0
}




# Function asks to remove all port forwarding rules for current client
disconnect() {
    #IP=$(ifconfig $TUN_IFACE | grep 'inet ' | awk '{print $2}')
    #if [ -z "$IP" ]; then
    #    echo "Error: Could not determine IP address."
    #    return 1  # Return 1 to indicate an error
    #fi
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
        if add_port "$2"; then
            cat "$CONFIG_FILE" | nc "$SERVER_IP" "$SERVER_PORT"
            # Send notification on macOS
            if [[ "$OSTYPE" == "darwin"* ]]; then
                notify_mac "Port $2 added. Configuration updated."
            fi
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
            cat "$CONFIG_FILE" | nc "$SERVER_IP" "$SERVER_PORT"
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

    *)
        echo "Error: Invalid argument '$1'. Valid arguments: --add, --delete, --disconnect, --test."
        exit 1
        ;;
  esac
fi

# Read configuration
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Configuration file not found."
    exit 1
fi

if [[ "$DISCONNECT_REQUEST" -eq 0 ]]; then
    response=$(cat "$CONFIG_FILE" | nc "$SERVER_IP" "$SERVER_PORT")
else
    #Client can send any IP as IP will be ignored, because disconnect requests to different IPs are accepted only from server
    response=$(echo "DISCONNECT: 10.8.0.2" | nc "$SERVER_IP" "$SERVER_PORT")
    echo "Disconnect request sent."
fi

# Send notification on macOS with the results
if [[ "$OSTYPE" == "darwin"* ]]; then
    notify_mac "$response"
else
    echo "$response"
fi