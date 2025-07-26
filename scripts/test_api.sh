#!/usr/bin/env bash

# Строгий режим для надежности скрипта
set -e  # Выход при ошибке
set -u  # Ошибка при использовании необъявленной переменной
set -o pipefail # Пайплайн завершается с кодом первой упавшей команды

# --- КОНФИГУРАЦИЯ ---
HOST="172.29.172.1"
ADMIN_API_KEY="12345"

if [ -z "$ADMIN_API_KEY" ]; then
    echo "Ошибка: Админский API ключ не предоставлен."
    echo "Использование: $0 <host_ip> <admin_api_key>"
    echo "Пример: $0 127.0.0.1 your-super-secret-admin-key"
    exit 1
fi

PORT="5000"
BASE_URL="http://${HOST}:${PORT}"
TEST_CLIENT_ID="test-client-$(date +%s)"
TEST_PORT_RANGE="20000-21000"
TEST_VALID_PORT="20101"
TEST_INVALID_PORT="29999"
USER_API_KEY=""

# --- ОПЦИИ CURL В ВИДЕ МАССИВОВ ---
CURL_ADMIN_OPTS_ARRAY=("-s" "-H" "X-Admin-API-Key: ${ADMIN_API_KEY}")

# --- ЦВЕТА ---
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

# --- УЛУЧШЕННАЯ ФУНКЦИЯ ТЕСТИРОВАНИЯ (БЕЗ EVAL) ---
function run_test() {
    local description="$1"; local expected_code="$2"; shift 2
    printf "${YELLOW}RUNNING TEST: ${description}${NC}\n"
    local response_file; response_file=$(mktemp)
    local http_code; http_code=$(curl -s -w '%{http_code}' -o "$response_file" "$@")
    if [ "$http_code" -eq "$expected_code" ]; then
        printf "${GREEN}PASS${NC} (Got HTTP ${http_code})\n"
        echo "Response body:"; cat "$response_file" | (jq . || cat); echo ""
    else
        printf "${RED}FAIL${NC} (Expected HTTP ${expected_code}, but got ${http_code})\n"
        echo "Response body:"; cat "$response_file" | (jq . || cat); echo ""
        exit 1
    fi
    rm -f "$response_file"
}

# --- ТЕСТОВЫЙ СЦЕНАРИЙ ---
echo "--- Запуск полного цикла тестирования PortMaster API v2.0 ---"

# --- ЭТАП 0: ОЧИСТКА ОКРУЖЕНИЯ ---
# (Рекомендуется перезапускать контейнер перед запуском)
echo "Рекомендация: Для чистого теста перезапустите контейнер: docker restart <container_name>"

# --- ЭТАП 1: АДМИНИСТРИРОВАНИЕ ---

# ... (все тесты до очистки остаются без изменений) ...

printf "${YELLOW}RUNNING TEST: Создание нового клиента '${TEST_CLIENT_ID}'...${NC}\n"
response_file=$(mktemp)
http_code=$(curl -s -w '%{http_code}' -o "$response_file" "${CURL_ADMIN_OPTS_ARRAY[@]}" -H "Content-Type: application/json" -d "{\"client_id\": \"${TEST_CLIENT_ID}\", \"port_range\": \"${TEST_PORT_RANGE}\"}" "${BASE_URL}/admin/clients")
if [ "$http_code" -ne "201" ]; then
    printf "${RED}FAIL: Не удалось создать клиента (HTTP ${http_code})${NC}\n"; cat "$response_file"; exit 1
fi
USER_API_KEY=$(cat "$response_file" | jq -r .api_key)
if [ -z "$USER_API_KEY" ] || [ "$USER_API_KEY" == "null" ]; then
    printf "${RED}FAIL: Не удалось извлечь API ключ пользователя!${NC}\n"; cat "$response_file"; exit 1
fi
printf "${GREEN}PASS: Клиент создан. Получен ключ: ${USER_API_KEY}${NC}\n"; echo "Response body:"; cat "$response_file" | jq .; echo ""; rm -f "$response_file"

run_test "Проверка списка клиентов (должен содержать '${TEST_CLIENT_ID}')" "200" "${CURL_ADMIN_OPTS_ARRAY[@]}" "${BASE_URL}/admin/clients"

CURL_USER_OPTS_ARRAY=("-s" "-H" "X-API-Key: ${USER_API_KEY}")
run_test "Пользователь: Проверка начального статуса" "200" "${CURL_USER_OPTS_ARRAY[@]}" "${BASE_URL}/ports"
run_test "Пользователь: Проброс валидного порта (${TEST_VALID_PORT})" "200" "${CURL_USER_OPTS_ARRAY[@]}" -H "Content-Type: application/json" -d "{\"ports\": [${TEST_VALID_PORT}]}" "${BASE_URL}/ports"
run_test "Пользователь: Проверка статуса после проброса" "200" "${CURL_USER_OPTS_ARRAY[@]}" "${BASE_URL}/ports"
run_test "Пользователь: Попытка проброса невалидного порта (${TEST_INVALID_PORT})" "200" "${CURL_USER_OPTS_ARRAY[@]}" -H "Content-Type: application/json" -d "{\"ports\": [${TEST_VALID_PORT}, ${TEST_INVALID_PORT}]}" "${BASE_URL}/ports"
run_test "Проверка безопасности: Доступ к админке с ключом юзера" "403" "${CURL_USER_OPTS_ARRAY[@]}" "${BASE_URL}/admin/status"

# --- ЭТАП 4: ОЧИСТКА И НАСТОЯЩАЯ ПРОВЕРКА ---

run_test "Пользователь: Удаление всех правил для своего IP" "204" "${CURL_USER_OPTS_ARRAY[@]}" -X "DELETE" "${BASE_URL}/ports"

run_test "Администратор: Удаление созданного клиента '${TEST_CLIENT_ID}'" "204" "${CURL_ADMIN_OPTS_ARRAY[@]}" -X "DELETE" "${BASE_URL}/admin/clients/${TEST_CLIENT_ID}"

# --- НОВЫЙ, УМНЫЙ ТЕСТ ---
printf "${YELLOW}RUNNING TEST: Проверка, что клиент '${TEST_CLIENT_ID}' ДЕЙСТВИТЕЛЬНО удален из списка${NC}\n"
final_list_file=$(mktemp)
final_http_code=$(curl -s -w '%{http_code}' -o "$final_list_file" "${CURL_ADMIN_OPTS_ARRAY[@]}" "${BASE_URL}/admin/clients")

if [ "$final_http_code" -ne "200" ]; then
    printf "${RED}FAIL: Не удалось получить финальный список клиентов (HTTP ${final_http_code})${NC}\n"; exit 1
fi

# Ищем ID нашего клиента в ответе. `grep -q` вернет 0, если найдет (это будет провал).
if grep -q "$TEST_CLIENT_ID" "$final_list_file"; then
    printf "${RED}FAIL: Клиент '${TEST_CLIENT_ID}' все еще присутствует в списке после удаления!${NC}\n"
    echo "Response body:"; cat "$final_list_file" | jq .
    exit 1
else
    printf "${GREEN}PASS: Клиент '${TEST_CLIENT_ID}' успешно удален из списка.${NC}\n"
    echo "Response body:"; cat "$final_list_file" | jq .
fi

printf "\n${YELLOW}--- FINAL SECURITY CHECK ---${NC}\n"
run_test "Проверка, что ключ удаленного пользователя НЕВАЛИДЕН" "403" \
    "${CURL_USER_OPTS_ARRAY[@]}" \
    "${BASE_URL}/ports"

rm -f "$final_list_file"

echo -e "\n${GREEN}--- ВСЕ ТЕСТЫ УСПЕШНО ПРОЙДЕНЫ! ---${NC}"