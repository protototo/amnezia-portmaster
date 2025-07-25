#!/bin/bash
# installer/setup_ubuntu.sh
#
# Версия 2.0 - Минималистичная и точная.
# Устанавливает ТОЛЬКО недостающий Docker Compose Plugin, если это необходимо.

set -e # Прерывать выполнение при любой ошибке

echo "--- [worker] Начало проверки зависимостей на Ubuntu/Debian ---"

# Проверяем, существует ли команда 'docker' и 'docker compose'
DOCKER_EXISTS=false
DOCKER_COMPOSE_EXISTS=false

if command -v docker &> /dev/null; then
    DOCKER_EXISTS=true
    echo "[worker] Docker Engine найден."
else
    echo "[worker] Docker Engine не найден. Это неожиданно. Сначала нужно установить Amnezia, а потом уже portmaster!"
    # Мы могли бы добавить установку здесь, но если его нет, то, скорее всего, система не готова.
    # Лучше прерваться с ошибкой.
    exit 1
fi

if docker compose version &> /dev/null; then
    DOCKER_COMPOSE_EXISTS=true
    echo "[worker] Docker Compose Plugin уже установлен. Ничего делать не нужно."
else
    echo "[worker] Docker Compose Plugin не найден. Начинаю установку..."
fi


# Если Docker есть, а Compose Plugin - нет, то выполняем установку.
if [ "$DOCKER_EXISTS" = true ] && [ "$DOCKER_COMPOSE_EXISTS" = false ]; then

    echo "[worker] Настраиваю официальный репозиторий Docker для установки плагина..."

    # Обновляем список пакетов
    apt-get update
    # Устанавливаем зависимости для добавления репозитория
    apt-get install -y ca-certificates curl

    # Создаем директорию для ключей и добавляем GPG-ключ Docker
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    # Добавляем репозиторий Docker в apt
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null    
    # Обновляем apt и ставим ТОЛЬКО недостающий плагин
    apt-get update
    echo "[worker] Устанавливаю docker-compose-plugin..."
    apt-get install -y docker-compose-plugin
fi

echo "--- [worker] Проверка зависимостей завершена успешно! ---"