import flet as ft
import os
import sys
import shutil
import time
import paramiko
import abc
import threading
from getpass import getpass
from typing import Optional, Dict, Type


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (заглушки для примера) ---
# В реальном проекте здесь будут ваши импорты из portmaster_installer_func
def get_user_input(prompt, **kwargs): pass


def validate_ip_address(ip): return True


def validate_container_index(idx, conts): return True


def validate_port_range(pr, ports): return True


def check_docker_access(ssh): return True


def get_os_info(ssh): return "Linux", "Ubuntu"


def print_docker_access_help(os, dist, user): pass


def get_exposed_ports(ssh, cont): return [], []


def prepare_payload_openvpn(ssh, c, p, pd, ccp, pp): pass


def prepare_payload_wg(ssh, c, p, pd, ccp, pp): pass


def print_directory_structure(path, expl): pass


def upload_payload(ssh, ct, pd, user): return "/tmp/deploy/deploy.sh"


def execute_remote_script(ssh, path): return "backup_container_123"


def check_container_exists(ssh, name): return True


# --- 1. КОНФИГУРАЦИЯ И КОНСТАНТЫ ---
WORK_DIR = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__)
PAYLOAD_DIR = os.path.join(WORK_DIR, "payload")
CLIENT_CONFIG_PATH = os.path.join(WORK_DIR, "client", "portmaster.conf")
DEFAULT_SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_rsa")
PORTMASTER_PORT = 50000


# --- 2. ПАТТЕРН "СТРАТЕГИЯ" (остается без изменений) ---
class VpnStrategy(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str: pass

    @abc.abstractmethod
    def prepare_payload(self, ssh: paramiko.SSHClient, container_name: str, port_range: str) -> None: pass


class OpenVpnStrategy(VpnStrategy):
    @property
    def name(self) -> str: return "openvpn"

    def prepare_payload(self, ssh, container_name, port_range) -> None:
        prepare_payload_openvpn(ssh, container_name, port_range, PAYLOAD_DIR, CLIENT_CONFIG_PATH, PORTMASTER_PORT)


class WireguardStrategy(VpnStrategy):
    @property
    def name(self) -> str: return "wireguard"

    def prepare_payload(self, ssh, container_name, port_range) -> None:
        prepare_payload_wg(ssh, container_name, port_range, PAYLOAD_DIR, CLIENT_CONFIG_PATH, PORTMASTER_PORT)


class CustomStrategy(VpnStrategy):
    @property
    def name(self) -> str: return "custom"

    def prepare_payload(self, ssh, container_name, port_range) -> None:
        if not os.path.exists(PAYLOAD_DIR): os.makedirs(PAYLOAD_DIR)


STRATEGIES: Dict[str, Type[VpnStrategy]] = {"openvpn": OpenVpnStrategy, "wireguard": WireguardStrategy,
                                            "custom": CustomStrategy}
DEFAULT_CONTAINER_STRATEGIES = {"amnezia-openvpn": "openvpn", "amnezia-wireguard": "wireguard"}


# --- 3. КЛАСС УСТАНОВЩИКА, АДАПТИРОВАННЫЙ ДЛЯ FLET ---
class PortMasterInstaller:
    """Класс-состояние, который теперь взаимодействует с Flet Page."""

    def __init__(self, page: ft.Page):
        self.page = page
        self.ssh_client: Optional[paramiko.SSHClient] = None
        self.target_container: Optional[str] = None
        self.strategy: Optional[VpnStrategy] = None
        self.backup_container_name: Optional[str] = None
        self.remote_script_path: Optional[str] = None

    def log(self, message: str, color: str = ft.Colors.WHITE):
        """Выводит сообщение в лог на UI."""
        # `controls` - это список виджетов на странице
        log_view = self.page.controls[0].controls[-1]
        log_view.controls.append(ft.Text(message, color=color))
        self.page.update()

    def connect(self, host, user, password, key_path):
        """Выполняет SSH-подключение."""
        try:
            self.log(f"Подключение к {host}...")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            pkey = paramiko.RSAKey.from_private_key_file(key_path, password=password or None) if key_path else None
            client.connect(host, username=user, pkey=pkey, password=password if not pkey else None, timeout=10)
            self.ssh_client = client
            self.log("Соединение установлено!", ft.Colors.GREEN)
            return True
        except Exception as e:
            self.log(f"Ошибка подключения: {e}", ft.Colors.RED)
            return False

    def get_containers(self) -> list:
        """Получает список контейнеров с сервера."""
        if not self.ssh_client: return []
        _, stdout, _ = self.ssh_client.exec_command("docker ps -a --format '{{.Names}}'")
        containers = stdout.read().decode().strip().split('\n')
        return containers if containers != [''] else []

    def select_strategy(self, container_name):
        """Определяет стратегию по имени контейнера."""
        self.target_container = container_name
        strategy_name = DEFAULT_CONTAINER_STRATEGIES.get(self.target_container, "custom")
        self.strategy = STRATEGIES[strategy_name]()
        self.log(f"Выбран контейнер: {container_name}. Тип установки: {self.strategy.name}")

    def deploy(self, port_range: str, username: str):
        """Запускает процесс подготовки и развертывания."""
        try:
            self.log(f"Подготовка payload для {self.strategy.name}...")
            self.strategy.prepare_payload(self.ssh_client, self.target_container, port_range)
            self.log("Payload готов.")

            self.log("Загрузка файлов на сервер...")
            self.remote_script_path = upload_payload(self.ssh_client, self.strategy.name, PAYLOAD_DIR, username)
            if not self.remote_script_path: raise RuntimeError("Ошибка при загрузке файлов.")
            self.log("Файлы загружены.")

            self.log("Запуск удаленного установочного скрипта...")
            self.backup_container_name = execute_remote_script(self.ssh_client, self.remote_script_path)
            if not self.backup_container_name: raise RuntimeError("Скрипт не вернул имя бэкапа.")
            self.log(f"Резервная копия создана: {self.backup_container_name}")
            self.log("Установка на сервере завершена!", ft.Colors.GREEN)
            return True
        except Exception as e:
            self.log(f"Ошибка развертывания: {e}", ft.Colors.RED)
            return False

    def cleanup_on_success(self):
        """Очистка после успешной установки."""
        self.log("Удаление резервной копии и временных файлов...")
        if self.backup_container_name:
            self.ssh_client.exec_command(f"docker rm -f {self.backup_container_name}")
        if self.remote_script_path:
            self.ssh_client.exec_command(f"rm -rf {os.path.dirname(self.remote_script_path)}")
        self.log("Очистка завершена.", ft.Colors.GREEN)

    def rollback_changes(self):
        """Откат изменений в случае неудачи."""
        self.log("Откат изменений...", ft.Colors.YELLOW)
        # Логика отката
        self.log("Изменения отменены.", ft.Colors.YELLOW)

    def close(self):
        if self.ssh_client: self.ssh_client.close()
        if os.path.exists(PAYLOAD_DIR): shutil.rmtree(PAYLOAD_DIR)


# --- 4. FLET UI ---
def main(page: ft.Page):
    page.title = "PortMaster Installer"
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.window_width = 800
    page.window_height = 600

    installer = PortMasterInstaller(page)

    # --- UI Компоненты ---
    host_input = ft.TextField(label="IP-адрес сервера", width=300)
    user_input = ft.TextField(label="Имя пользователя SSH", value=os.getlogin(), width=300)
    pass_input = ft.TextField(label="Пароль SSH / Пароль от ключа", password=True, can_reveal_password=True, width=300)
    key_path_input = ft.TextField(label="Путь к SSH ключу (оставьте пустым для пароля)", value=DEFAULT_SSH_KEY_PATH,
                                  width=400)
    connect_button = ft.ElevatedButton("Подключиться", icon=ft.Icons.POWER_SETTINGS_NEW)

    container_dropdown = ft.Dropdown(label="Выберите контейнер", width=400, disabled=True)
    port_range_input = ft.TextField(label="Диапазон портов", value="20000-20099", width=400, disabled=True)
    deploy_button = ft.ElevatedButton("Развернуть", icon=ft.Icons.CLOUD_UPLOAD, disabled=True)

    log_view = ft.ListView(expand=True, spacing=5, auto_scroll=True)
    progress_bar = ft.ProgressBar(width=400, visible=False)

    # --- Функции-обработчики событий ---
    def run_in_thread(target_func, *args):
        """Запускает функцию в отдельном потоке, чтобы не блокировать UI."""
        thread = threading.Thread(target=target_func, args=args)
        thread.start()

    def connect_clicked(e):
        def task():
            connect_button.disabled = True
            progress_bar.visible = True
            page.update()

            success = installer.connect(host_input.value, user_input.value, pass_input.value, key_path_input.value)

            if success:
                containers = installer.get_containers()
                container_dropdown.options = [ft.dropdown.Option(c) for c in containers]
                container_dropdown.disabled = False
                port_range_input.disabled = False
                deploy_button.disabled = False
                tabs.selected_index = 1  # Переключаемся на вторую вкладку

            connect_button.disabled = False
            progress_bar.visible = False
            page.update()

        run_in_thread(task)

    def deploy_clicked(e):
        def task():
            deploy_button.disabled = True
            progress_bar.visible = True
            page.update()

            installer.select_strategy(container_dropdown.value)
            success = installer.deploy(port_range_input.value, user_input.value)

            if success:
                # Показываем диалог для подтверждения
                dlg = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Проверка"),
                    content=ft.Text("Удалось ли вам подключиться к VPN после установки?"),
                    actions=[
                        ft.TextButton("Да, всё работает!", on_click=lambda _: finalize_install(dlg, True)),
                        ft.TextButton("Нет, что-то пошло не так", on_click=lambda _: finalize_install(dlg, False)),
                    ],
                )
                page.dialog = dlg
                dlg.open = True
                page.update()
            else:
                deploy_button.disabled = False
                progress_bar.visible = False
                page.update()

        run_in_thread(task)

    def finalize_install(dlg, success):
        dlg.open = False
        if success:
            installer.cleanup_on_success()
        else:
            installer.rollback_changes()

        deploy_button.disabled = False
        progress_bar.visible = False
        page.update()

    connect_button.on_click = connect_clicked
    deploy_button.on_click = deploy_clicked

    # --- Создание вкладок и компоновка ---
    tab_connect = ft.Container(
        content=ft.Column([
            ft.Text("1. Данные для подключения", style="headlineSmall"),
            host_input, user_input, pass_input, key_path_input,
            ft.Row([connect_button, progress_bar])
        ]),
        padding=20
    )

    tab_config = ft.Container(
        content=ft.Column([
            ft.Text("2. Настройка и развертывание", style="headlineSmall"),
            container_dropdown,
            port_range_input,
            deploy_button
        ]),
        padding=20,
    )

    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        tabs=[
            ft.Tab(text="Подключение", content=tab_connect),
            ft.Tab(text="Настройка", content=tab_config),
        ],
        expand=1,
    )

    # Собираем главный экран
    page.add(
        ft.Column(
            [
                tabs,
                ft.Divider(),
                ft.Text("Лог выполнения:", style="titleMedium"),
                log_view
            ],
            expand=True
        )
    )

    page.on_disconnect = lambda _: installer.close()


# --- 5. ЗАПУСК ПРИЛОЖЕНИЯ ---
if __name__ == "__main__":
    ft.app(target=main)