#!/usr/bin/env python3

import logging
import os
import re
import signal
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Set

# Настраиваем логирование для вывода в stdout, как принято в Docker.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)


@dataclass
class Config:
    """Централизованная и валидируемая конфигурация из переменных окружения."""

    vpn_ip: str
    daemon_port: int
    exposed_ports: range

    @classmethod
    def from_env(cls) -> "Config":
        """Фабричный метод для создания конфигурации из переменных окружения."""
        # Теперь эта переменная будет содержать конкретный IP, а не '0.0.0.0'
        vpn_ip = os.environ.get("PORTMASTER_IP")
        if not vpn_ip:
            logging.critical(
                "Критическая ошибка: Переменная окружения PORTMASTER_IP не задана! Демон не может быть запущен.")
            sys.exit(1)

        try:
            daemon_port = int(os.environ.get("PORTMASTER_PORT", "5000"))
        except (ValueError, TypeError):
            logging.warning("PORTMASTER_PORT некорректен или не задан. Используется порт 5000.")
            daemon_port = 5000

        port_range_str = os.environ.get("EXPOSED_PORT_RANGE", "20000-25000")
        try:
            start_str, end_str = port_range_str.split("-")
            start, end = int(start_str), int(end_str)
            if start >= end:
                raise ValueError("Начало диапазона должно быть меньше конца.")
            exposed_ports = range(start, end + 1)
        except ValueError as e:
            logging.error(f"Ошибка в EXPOSED_PORT_RANGE ('{port_range_str}'): {e}. Используется диапазон по умолчанию.")
            exposed_ports = range(20000, 25001)

        logging.info(
            f"Конфигурация загружена: Слушаем на IP={vpn_ip}, Port={daemon_port}, Range={exposed_ports.start}-{exposed_ports.stop - 1}")
        return cls(vpn_ip, daemon_port, exposed_ports)


class IPTablesError(Exception):
    """Кастомное исключение для ошибок при работе с iptables."""
    pass


class HostPortScanner:
    """
    Сканирует порты, прослушиваемые на хост-машине.
    Использует `ss`, так как контейнер запущен в network_mode: host.
    """

    def _parse_ss_output(self, output: str) -> Set[int]:
        """Парсит вывод команды ss для извлечения номеров портов."""
        ports = set()
        lines = output.strip().split('\n')[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 5:
                try:
                    address_port = parts[4]
                    port_str = address_port.split(':')[-1]
                    if port_str.isdigit():
                        ports.add(int(port_str))
                except (ValueError, IndexError):
                    continue
        return ports

    def get_listening_ports(self) -> Set[int]:
        """
        Возвращает множество всех прослушиваемых TCP и UDP портов на хосте.
        """
        listening_ports = set()
        try:
            process = subprocess.run(
                ["ss", "-ltun"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            listening_ports = self._parse_ss_output(process.stdout)
            logging.info(f"На хосте обнаружено {len(listening_ports)} прослушиваемых портов.")

        except FileNotFoundError:
            logging.error("Команда 'ss' не найдена. Убедитесь, что пакет 'iproute2' установлен в контейнере.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Ошибка выполнения 'ss': {e.stderr}")

        return listening_ports


class IPTablesManager:
    """
    Класс, инкапсулирующий всю логику работы с iptables.
    """

    def _run_command(self, command: List[str]):
        """Приватный хелпер для выполнения команд."""
        try:
            process = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            logging.info(f"Команда выполнена успешно: {' '.join(command)}")
            return process.stdout
        except subprocess.CalledProcessError as e:
            error_message = f"Ошибка выполнения команды '{" ".join(command)}'. stderr: {e.stderr.strip()}"
            logging.error(error_message)
            raise IPTablesError(error_message) from e

    def add_port_forward(self, client_ip: str, port: int):
        """Добавляет правила проброса порта для TCP и UDP."""
        for proto in ["tcp", "udp"]:
            self._run_command(
                [
                    "iptables", "-t", "nat", "-A", "PREROUTING",
                    "-p", proto, "--dport", str(port),
                    "-j", "DNAT", "--to-destination", f"{client_ip}:{port}",
                ]
            )
            self._run_command(
                [
                    "iptables", "-A", "FORWARD",
                    "-p", proto, "-d", client_ip, "--dport", str(port),
                    "-j", "ACCEPT",
                ]
            )

            self._run_command(
                [
                    "iptables", "-t", "nat", "-A", "POSTROUTING",
                    "-p", proto, "--dport", str(port),
                    "-j", "MASQUERADE", "-d", f"{client_ip}",
                ]
            )



        logging.info(f"Порт {port} (TCP/UDP) проброшен на {client_ip}")

    def remove_port_forward(self, client_ip: str, port: int):
        """Удаляет правила проброса порта для TCP и UDP."""
        for proto in ["tcp", "udp"]:
            self._run_command(
                [
                    "iptables", "-t", "nat", "-D", "PREROUTING",
                    "-p", proto, "--dport", str(port),
                    "-j", "DNAT", "--to-destination", f"{client_ip}:{port}",
                ]
            )
            self._run_command(
                [
                    "iptables", "-D", "FORWARD",
                    "-p", proto, "-d", client_ip, "--dport", str(port),
                    "-j", "ACCEPT",
                ]
            )
        logging.info(f"Проброс порта {port} (TCP/UDP) для {client_ip} удален")

    def parse_existing_rules(self) -> Dict[str, Set[int]]:
        """
        Парсит существующие правила iptables и возвращает словарь {ip: {порты}}.
        """
        forwarded_ports: Dict[str, Set[int]] = {}
        output = self._run_command(["iptables", "-t", "nat", "-L", "PREROUTING", "-n"])

        dnat_regex = re.compile(r"DNAT\s+\w+\s+--\s+[\d\.\/]+\s+[\d\.\/]+\s+\w+\s+dpt:(\d+)\s+to:([\d\.]+):(\d+)")

        for line in output.splitlines():
            match = dnat_regex.search(line)
            if match:
                port, client_ip, dest_port = match.groups()
                if port == dest_port:
                    port_num = int(port)
                    if client_ip not in forwarded_ports:
                        forwarded_ports[client_ip] = set()
                    forwarded_ports[client_ip].add(port_num)

        if forwarded_ports:
            logging.info(f"Обнаружены существующие правила: {forwarded_ports}")
        else:
            logging.info("Существующих правил проброса не обнаружено.")

        return forwarded_ports


class PortMasterDaemon:
    """
    Основной класс демона. Управляет жизненным циклом, состоянием и обработкой запросов.
    """

    def __init__(self, config: Config, iptables_manager: IPTablesManager, host_port_scanner: HostPortScanner):
        self.config = config
        self.iptables_manager = iptables_manager
        self.host_port_scanner = host_port_scanner
        self.running = False
        self.forwarded_ports: Dict[str, Set[int]] = {}
        self.unavailable_ports: Set[int] = set()

    def signal_handler(self, signum, frame):
        """Обработчик сигналов для корректной остановки."""
        logging.info(f"Получен сигнал {signum}. Завершаю работу...")
        self.running = False

    def handle_client_connection(self, client_socket: socket.socket, client_ip: str):
        """Обрабатывает одно клиентское подключение."""
        try:
            request = client_socket.recv(1024).decode("utf-8").strip()
            logging.info(f"Получен запрос от {client_ip}: '{request}'")

            if request.upper().startswith("PORTS:"):
                self.process_ports_request(client_socket, client_ip, request)
            elif request.upper().startswith("DISCONNECT:"):
                self.process_disconnect_request(client_socket, client_ip)
            else:
                client_socket.sendall(b"Error: Unknown command\n")

        except Exception as e:
            logging.error(f"Критическая ошибка при обработке клиента {client_ip}: {e}", exc_info=True)
            try:
                client_socket.sendall(f"Error: Internal server error: {e}\n".encode("utf-8"))
            except socket.error:
                pass
        finally:
            client_socket.close()

    def process_ports_request(self, sock: socket.socket, client_ip: str, request: str):
        """Логика обработки запроса на проброс портов."""
        ports_str = request[len("PORTS:"):].strip()
        try:
            requested_ports = {int(p) for p in ports_str.split(",") if p.strip().isdigit()}
        except ValueError:
            sock.sendall(b"Error: Invalid port format. Must be comma-separated numbers.\n")
            return

        if client_ip in self.forwarded_ports:
            for port in list(self.forwarded_ports.get(client_ip, set())):
                try:
                    self.iptables_manager.remove_port_forward(client_ip, port)
                    self.forwarded_ports[client_ip].remove(port)
                except IPTablesError as e:
                    logging.warning(f"Не удалось удалить старое правило для {client_ip}:{port}: {e}")

        success_ports, failed_ports = set(), set()
        all_currently_forwarded_by_me = {p for ip_ports in self.forwarded_ports.values() for p in ip_ports}

        for port in requested_ports:
            if port not in self.config.exposed_ports:
                logging.warning(f"Порт {port} не входит в разрешенный диапазон. Запрос от {client_ip}.")
                failed_ports.add(port)
                continue
            if port in self.unavailable_ports:
                logging.warning(f"Порт {port} занят другим процессом на хосте. Запрос от {client_ip}.")
                failed_ports.add(port)
                continue
            if port in all_currently_forwarded_by_me:
                logging.warning(f"Порт {port} уже занят другим VPN-клиентом. Запрос от {client_ip}.")
                failed_ports.add(port)
                continue

            try:
                self.iptables_manager.add_port_forward(client_ip, port)
                if client_ip not in self.forwarded_ports:
                    self.forwarded_ports[client_ip] = set()
                self.forwarded_ports[client_ip].add(port)
                success_ports.add(port)
            except IPTablesError:
                failed_ports.add(port)

        response = ""
        if success_ports:
            response += f"Success: Ports {','.join(map(str, sorted(success_ports)))} forwarded.\n"
        if failed_ports:
            response += f"Error: Failed to forward ports {','.join(map(str, sorted(failed_ports)))}.\n"
        if not response:
            response = "Ok: No new ports to forward. All previous ports for your IP were removed.\n"

        sock.sendall(response.encode("utf-8"))

    def process_disconnect_request(self, sock: socket.socket, client_ip: str):
        """Логика обработки запроса на отключение."""
        if client_ip in self.forwarded_ports:
            removed_count = 0
            for port in list(self.forwarded_ports.get(client_ip, set())):
                try:
                    self.iptables_manager.remove_port_forward(client_ip, port)
                    self.forwarded_ports[client_ip].remove(port)
                    removed_count += 1
                except IPTablesError as e:
                    logging.warning(f"Не удалось удалить правило для {client_ip}:{port} при дисконнекте: {e}")

            if not self.forwarded_ports[client_ip]:
                del self.forwarded_ports[client_ip]

            sock.sendall(
                f"Success: Disconnected. {removed_count} port rules removed for {client_ip}.\n".encode("utf-8"))
        else:
            sock.sendall(f"Info: No forwarded ports found for your IP {client_ip}.\n".encode("utf-8"))

    def run(self):
        """Основной цикл работы демона."""
        self.running = True
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

        self.forwarded_ports = self.iptables_manager.parse_existing_rules()

        host_ports = self.host_port_scanner.get_listening_ports()
        config_ports = set(self.config.exposed_ports)
        self.unavailable_ports = config_ports.intersection(host_ports)

        if self.unavailable_ports:
            logging.warning(
                f"Следующие порты из заданного диапазона уже заняты на хосте и не будут использоваться: "
                f"{sorted(list(self.unavailable_ports))}"
            )

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Код привязывается к IP из конфигурации. Безопасно!
            server_socket.bind((self.config.vpn_ip, self.config.daemon_port))
            server_socket.listen(5)
            server_socket.settimeout(1.0)
            logging.info(f"Демон запущен и слушает на {self.config.vpn_ip}:{self.config.daemon_port}")

            while self.running:
                try:
                    client_socket, client_address = server_socket.accept()
                    client_ip = client_address[0]
                    logging.info(f"Принято соединение от {client_ip}")
                    self.handle_client_connection(client_socket, client_ip)
                except socket.timeout:
                    continue
                except OSError as e:
                    if not self.running:
                        break
                    logging.error(f"Сетевая ошибка: {e}")

        logging.info("Демон остановлен.")


def main():
    """Точка входа в приложение."""
    try:
        config = Config.from_env()
        iptables_manager = IPTablesManager()
        host_port_scanner = HostPortScanner()
        daemon = PortMasterDaemon(config, iptables_manager, host_port_scanner)
        daemon.run()
    except Exception as e:
        logging.critical(f"Не удалось запустить демона: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()