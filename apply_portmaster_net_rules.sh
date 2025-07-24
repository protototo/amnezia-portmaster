#!/bin/bash
# /opt/portmaster/apply_net_rules.sh
# Версия 5.2 - Точный фильтр контейнеров. Безопасный и надежный.

set -e # Прерывать выполнение при любой ошибке
# set -x # Раскомментируйте для детальной отладки

echo "--- Запуск скрипта настройки сети для Portmaster---"
sleep 1

# --- Фиксированные допущения ---
HOST_BRIDGE_INTERFACE="amn0"

# --- Получаем сеть моста с хоста ---
echo "INFO: Получаю сеть моста с интерфейса $HOST_BRIDGE_INTERFACE..."
HOST_BRIDGE_SUBNET=$(ip -o -4 addr show dev "$HOST_BRIDGE_INTERFACE" | awk '{print $4}')
if [ -z "$HOST_BRIDGE_SUBNET" ]; then
    echo "CRITICAL: Не удалось найти интерфейс $HOST_BRIDGE_INTERFACE на хосте. Выход."
    exit 1
fi
echo "OK: Сеть моста: $HOST_BRIDGE_SUBNET"

# --- Находим ТОЛЬКО VPN-контейнеры ---
echo "INFO: Ищу запущенные контейнеры amnezia-openvpn и amnezia-awg..."
# ИЗМЕНЕНИЕ ЗДЕСЬ: Мы используем регулярное выражение, чтобы выбрать только нужные контейнеры
VPN_CONTAINERS=$(docker ps --filter "name=amnezia-(openvpn|awg)" --filter "status=running" --format "{{.Names}}")

if [ -z "$VPN_CONTAINERS" ]; then
    echo "WARN: Не найдено запущенных контейнеров amnezia-openvpn или amnezia-awg. Пропускаю."
    exit 0
fi
echo "OK: Обнаружены целевые контейнеры: $VPN_CONTAINERS"

# --- Итерация по каждому контейнеру ---
for container_name in $VPN_CONTAINERS; do
    echo ""
    echo "--- Настраиваю контейнер: $container_name ---"

    # 1. Извлекаем внутреннюю VPN-сеть
    echo "INFO: Ищу VPN-сеть внутри $container_name..."
    VPN_SUBNET=$(docker exec "$container_name" ip -o -4 addr show | grep -E ' (wg[0-9]+|tun[0-9]+)' | awk '{print $4}' | head -n 1)
    if [ -z "$VPN_SUBNET" ]; then
        echo "WARN: Не найден интерфейс tun* или wg* внутри $container_name. Пропускаю."
        continue
    fi
    echo "OK: Найдена VPN подсеть: $VPN_SUBNET"

    # --- Применяем настройки ---
    echo "INFO: Применяю настройки для $container_name..."

    # 2. Включаем IP Forwarding и Proxy ARP
    echo "CMD: Включаю sysctl в $container_name..."
    docker exec "$container_name" sysctl -w net.ipv4.ip_forward=1 >/dev/null
    docker exec "$container_name" sysctl -w net.ipv4.conf.all.proxy_arp=1 >/dev/null

    # 3. Настраиваем правило ACCEPT в iptables контейнера.
    echo "CMD: Настраиваю iptables в $container_name..."
    docker exec "$container_name" iptables -t nat -D POSTROUTING -s "$VPN_SUBNET" -d "$HOST_BRIDGE_SUBNET" -j ACCEPT 2>/dev/null || true
    docker exec "$container_name" iptables -t nat -I POSTROUTING 1 -s "$VPN_SUBNET" -d "$HOST_BRIDGE_SUBNET" -j ACCEPT

    # 4. Настраиваем маршрут на ХОСТЕ
    echo "CMD: Настраиваю маршрут на ХОСТЕ (требует sudo)..."
    ip route replace "$VPN_SUBNET" dev "$HOST_BRIDGE_INTERFACE"

    echo "OK: Настройка для $container_name завершена."
done

echo ""
echo "--- Все сетевые правила для Portmaster успешно применены. ---"