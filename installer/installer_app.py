import pathlib
import pwd
import queue
import re
import threading
from typing import Callable, Container
import socket
import flet as ft
import paramiko
import os
from fluent.runtime import FluentLocalization, FluentResourceLoader

# --- КОНФИГУРАЦИЯ ---
GIT_REPO_URL = "https://github.com/protototo/amnezia-portmaster.git"
REMOTE_PROJECT_DIR = "amnezia-portmaster"
UFW_RULE_COMMENT = "Added-by-Amnezia-Portmaster-Installer"
CONTAINER_NAME = "portmaster"

# --- Утилиты и диалоги ---
def is_path_critically_dangerous(path_str: str) -> bool:
    """
    Проверяет, является ли путь потенциально опасным.
    """
    if not isinstance(path_str, str) or not path_str.strip(): return True
    path = path_str.strip()
    # Проверяем на абсолютные пути, относительные пути "вверх" и "текущую директорию"
    if path.startswith('/') or '..' in path.split('/') or path.startswith('./'): return True
    # Проверяем, что путь состоит только из безопасных символов (буквы, цифры, _, ., -)
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', path): return True
    # Запрещаем просто "." или ".."
    if path in ('.', '..'): return True
    return False


# Обновлено: теперь принимает объект локализации L10nManager
def show_monkey_with_grenade_dialog(page: ft.Page, dangerous_path: str, l10n: 'L10nManager'):
    """
    Показывает диалог критической ошибки для опасного пути.
    """
    def close_dialog(e):
        dialog.open = False
        page.update()
        page.window.destroy() # Закрываем окно при критической ошибке

    dialog = ft.AlertDialog(modal=True,
                            title=ft.Row([ft.Text("🐒💣", size=40), ft.Text(l10n.get("critical-error-title-text"), size=20)]), # Локализовано
                            content=ft.Text(
                                l10n.get("critical-error-content", dangerous_path=dangerous_path), # Локализовано с переменной
                                size=14, text_align=ft.TextAlign.CENTER), actions=[
            ft.ElevatedButton(l10n.get("button-understood"), on_click=close_dialog, color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_700)], # Локализовано
                            actions_alignment=ft.MainAxisAlignment.END)
    page.update()
    page.open(dialog)


# --- Утилиты для умных дефолтов ---
def get_current_username() -> str | None:
    """
    Возвращает имя текущего пользователя.
    Использует самый надежный метод для Unix-систем (включая macOS).
    """
    if os.name == 'posix':
        try:
            return pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            return None
    return None


def find_default_ssh_key() -> str | None:
    """Ищет стандартный SSH ключ (id_rsa или id_ed25519) в ~/.ssh/"""
    if os.name == 'posix':
        home_dir = pathlib.Path.home()
        ssh_dir = home_dir / ".ssh"

        default_keys = ["id_ed25519", "id_rsa"]

        for key_name in default_keys:
            key_path = ssh_dir / key_name
            if key_path.is_file():
                return str(key_path)
    return None


class L10nManager:
    """
    Менеджер локализации, реализующий паттерн Singleton.
    Загружает FTL-ресурсы и предоставляет метод для получения переведенных строк.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(L10nManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, fallback_locale="en"):
        if hasattr(self, 'initialized'):
            return
        self.initialized = True

        self.loader = FluentResourceLoader("locales/{locale}")
        self.fallback_locale = fallback_locale
        self.locales = {}
        self._discover_locales()

        # Устанавливаем текущую локаль: сначала русский, если доступен, иначе английский, иначе fallback
        self.current_locale = fallback_locale
        if "ru" in self.locales:
            self.current_locale = "ru"
        elif "en" in self.locales:
            self.current_locale = "en"
        else:
            print(f"Warning: No 'ru' or 'en' locales found. Falling back to '{self.fallback_locale}'.")


    def _discover_locales(self):
        """
        Обнаруживает доступные локали в директории 'locales'.
        """
        locales_path = pathlib.Path("locales")
        if not locales_path.is_dir():
            print("Warning: 'locales' directory not found. No translations will be loaded.")
            return
        for locale_dir in locales_path.iterdir():
            if locale_dir.is_dir():
                # FluentLocalization принимает список локалей для фоллбэка
                self.locales[locale_dir.name] = FluentLocalization([locale_dir.name, self.fallback_locale],
                                                                   ["main.ftl"], self.loader)
        if not self.locales:
            print("Warning: No locale directories found inside 'locales'. Localization will not work.")

    def set_locale(self, locale: str):
        """
        Устанавливает текущую локаль.
        """
        if locale in self.locales:
            self.current_locale = locale
        else:
            print(f"Warning: Locale '{locale}' not found among available locales: {self.get_available_locales()}. Keeping current locale '{self.current_locale}'.")

    def get(self, key: str, **kwargs) -> str:
        """
        Получает переведенную строку по ключу.
        Если перевод не найден, возвращает ключ.
        """
        try:
            # Сначала пытаемся получить из текущей локали
            if self.current_locale in self.locales:
                bundle = self.locales[self.current_locale]
                message = bundle.format_value(key, args=kwargs)
                if message:
                    return message

            # Если в текущей локали не нашлось, пытаемся получить из фоллбэка
            if self.fallback_locale in self.locales and self.fallback_locale != self.current_locale:
                bundle = self.locales[self.fallback_locale]
                message = bundle.format_value(key, args=kwargs)
                if message:
                    return message

            # Если нигде не нашлось, возвращаем ключ
            print(f"Warning: Translation key '{key}' not found in locale '{self.current_locale}' or fallback '{self.fallback_locale}'.")
            return key
        except Exception as e:
            # Логируем ошибку, но возвращаем ключ, чтобы приложение не падало
            print(f"Error getting translation for key '{key}': {e}")
            return key

    def get_available_locales(self) -> list[str]:
        """
        Возвращает список доступных локалей.
        """
        return sorted(list(self.locales.keys()))


# --- SSH-клиент ---
class SecureSSHClient:
    """
    Класс для безопасного SSH-подключения и выполнения команд.
    """
    def __init__(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self, hostname, port, username, password=None, key_filename=None, key_password=None):
        try:
            if key_filename:
                key_path = os.path.expanduser(key_filename)
                if not os.path.exists(key_path): raise FileNotFoundError(f"SSH-ключ не найден: {key_path}")
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(key_path, password=key_password)
                except paramiko.ssh_exception.SSHException:
                    pkey = paramiko.RSAKey.from_private_key_file(key_path, password=key_password)
                self.client.connect(hostname, port, username, pkey=pkey, timeout=10)
            elif password:
                self.client.connect(hostname, port, username, password=password, timeout=10)
            else:
                raise ValueError("Необходимо указать пароль или путь к SSH-ключу для подключения.")
        except Exception as e:
            raise ConnectionError(f"Не удалось подключиться к {username}@{hostname}:{port}: {e}")

    def get_os_release_id(self) -> str:
        stdin, stdout, stderr = self.client.exec_command("grep '^ID=' /etc/os-release | cut -d'=' -f2")
        os_id = stdout.read().decode().strip().strip('"')
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0 or not os_id:
            err = stderr.read().decode().strip()
            raise RuntimeError(f"Не удалось определить OS: {err or 'нет данных'}")
        return os_id

    def execute_command(self, command: str, log_callback: Callable[[str], None],
                        sudo_password: str | None = None) -> str:
        log_callback(f"$ {command.replace(sudo_password or 'DUMMY_PASSWORD_REPLACE', '********')}")
        stdin, stdout, stderr = self.client.exec_command(command, get_pty=True)
        if sudo_password:
            stdin.write(sudo_password + '\n')
            stdin.flush()
        output_lines = []
        for line in iter(stdout.readline, ""): output_lines.append(line)
        exit_status = stdout.channel.recv_exit_status()
        full_stdout = "".join(output_lines)
        if exit_status != 0:
            full_stderr = "".join(stderr.readlines())
            error_details = f"Команда завершилась с кодом {exit_status}.\n--- КОМАНДА ---\n{command}\n\n--- STDOUT ---\n{full_stdout}\n\n--- STDERR ---\n{full_stderr}"
            if 'incorrect password attempt' in full_stderr.lower(): raise PermissionError("Неверный пароль для sudo!")
            raise ChildProcessError(error_details)
        return full_stdout

    def close(self):
        if self.client: self.client.close()


# --- Сервис установки ---
class InstallationService:
    """
    Сервис, отвечающий за логику установки, удаления и исправления Portmaster.
    """
    def __init__(self, client: SecureSSHClient, user_data: dict, log_callback: Callable[[str], None],
                 request_confirmation_func: Callable[[], None], confirmation_queue: queue.Queue, l10n: 'L10nManager'
                 ):
        self.client = client
        self.data = user_data
        self.log = log_callback
        self.request_confirmation = request_confirmation_func
        self.confirmation_queue = confirmation_queue
        self.initial_password = user_data.get('password')
        self.confirmed_sudo_password = None
        self.pm_port = user_data.get('pm_port')
        self.pm_range = user_data.get('pm_range')
        self.amn0_ip = None # Будет определен во время установки
        self.l10n = l10n # Инжектируем L10nManager

    def _check_for_existing_installation(self) -> bool:
        """Проверяет, существует ли уже контейнер Portmaster."""
        self.log(self.l10n.get("log-check-previous-installation")) # Локализовано
        command = f"docker ps -a --format '{{{{.Names}}}}' | grep -q '^{CONTAINER_NAME}$'"
        try:
            use_sudo = self.data['user'] != 'root'
            self._execute(command, use_sudo=use_sudo)
            self.log(self.l10n.get("log-existing-container-found", container_name=CONTAINER_NAME)) # Локализовано
            return True
        except ChildProcessError:
            self.log(self.l10n.get("log-no-previous-installation")) # Локализовано
            return False

    def _execute(self, command: str, use_sudo=False, working_dir: str | None = None):
        """
        Собирает и выполняет команду, ПРАВИЛЬНО обрабатывая `cd` и `sudo`.
        """
        command_to_run = command
        password_for_sudo = None

        if use_sudo:
            if not self.confirmed_sudo_password:
                self._obtain_sudo_password()

            password_for_sudo = self.confirmed_sudo_password
            command_to_run = f"sudo -S -p '' {command}"

        if working_dir:
            command_to_run = f"cd {working_dir} && {command_to_run}"

        return self.client.execute_command(command_to_run, self.log, sudo_password=password_for_sudo)

    def _cleanup_ufw_rules(self):
        """Находит все правила UFW по комментарию и удаляет их в правильном порядке."""
        self.log(self.l10n.get("log-cleanup-ufw-rules")) # Локализовано

        try:
            status_output = self._execute("sudo ufw status numbered", use_sudo=True)

            rules_to_delete = []
            for line in status_output.splitlines():
                if UFW_RULE_COMMENT in line:
                    match = re.search(r"\[\s*(\d+)\s*\]", line)
                    if match:
                        rule_number = int(match.group(1))
                        rules_to_delete.append(rule_number)

            if not rules_to_delete:
                self.log(self.l10n.get("log-no-ufw-rules-found")) # Локализовано
                return

            rules_to_delete.sort(reverse=True) # Удаляем с конца, чтобы номера не сдвигались
            self.log(self.l10n.get("log-rules-to-delete", rules=", ".join(map(str, rules_to_delete)))) # Локализовано

            for num in rules_to_delete:
                self.log(self.l10n.get("log-deleting-ufw-rule", rule_number=num)) # Локализовано
                self._execute(f"sudo ufw --force delete {num}", use_sudo=True)

            self.log(self.l10n.get("log-ufw-rules-cleaned")) # Локализовано

        except ChildProcessError:
            self.log(self.l10n.get("log-ufw-command-not-found")) # Локализовано
        except Exception as e:
            self.log(self.l10n.get("log-error-cleaning-ufw-rules", error=str(e))) # Локализовано

    def _cleanup_previous_installation(self):
        """Останавливает и удаляет старый контейнер и его правила UFW."""
        self.log(self.l10n.get("log-start-cleanup")) # Локализовано
        use_sudo = self.data['user'] != 'root'

        self.log(self.l10n.get("log-stopping-removing-container", container_name=CONTAINER_NAME)) # Локализовано
        cleanup_command = f"docker stop {CONTAINER_NAME} || true && docker rm {CONTAINER_NAME}"
        try:
            self._execute(cleanup_command, use_sudo=use_sudo)
        except ChildProcessError:
            self.log(self.l10n.get("log-failed-to-remove-container", container_name=CONTAINER_NAME)) # Локализовано

        self._cleanup_ufw_rules()


    def _ensure_port_is_open(self):
        """Проверяет доступность порта с клиента и открывает его в UFW при необходимости."""
        self.log(self.l10n.get("log-check-port-accessibility", port=self.pm_port)) # Локализовано

        self.log(self.l10n.get("log-attempt-connect", ip=self.amn0_ip, port=self.pm_port)) # Локализовано
        try:
            with socket.create_connection((self.amn0_ip, self.pm_port), timeout=5):
                self.log(self.l10n.get("log-port-already-open", port=self.pm_port)) # Локализовано
                return
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            self.log(self.l10n.get("log-port-unavailable", port=self.pm_port, error=str(e))) # Локализовано

        try:
            ufw_status_output = self._execute("sudo ufw status", use_sudo=True)
            if "Status: inactive" in ufw_status_output:
                raise RuntimeError(self.l10n.get("error-port-unavailable-ufw-inactive", port=self.pm_port)) # Локализовано
        except ChildProcessError:
            raise RuntimeError(self.l10n.get("error-port-unavailable-ufw-not-found", port=self.pm_port)) # Локализовано

        self.log(self.l10n.get("log-ufw-active-adding-rule")) # Локализовано
        self._execute(f"sudo ufw allow {self.pm_port}/tcp comment '{UFW_RULE_COMMENT}'", use_sudo=True)
        self.log(self.l10n.get("log-rule-added-to-ufw", port=self.pm_port)) # Локализовано

        self.log(self.l10n.get("log-recheck-port-accessibility", ip=self.amn0_ip, port=self.pm_port)) # Локализовано
        try:
            with socket.create_connection((self.amn0_ip, self.pm_port), timeout=5):
                self.log(self.l10n.get("log-port-now-open", port=self.pm_port)) # Локализовано
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            raise RuntimeError(
                self.l10n.get("error-port-still-unavailable", port=self.pm_port, error=str(e)) # Локализовано
            )


    def _obtain_sudo_password(self):
        """
        Вспомогательный метод, который инкапсулирует логику получения пароля.
        Вызывается только когда пароль действительно нужен.
        """
        if self.initial_password:
            self.log(self.l10n.get("log-checking-sudo-password")) # Локализовано
            test_command = "sudo -S -p '' ls /root"
            try:
                self.client.execute_command(test_command, self.log, sudo_password=self.initial_password)
                self.log(self.l10n.get("log-sudo-password-ok")) # Локализовано
                self.confirmed_sudo_password = self.initial_password
                self.initial_password = None
                return
            except (PermissionError, ChildProcessError):
                self.log(self.l10n.get("log-sudo-password-failed")) # Локализовано
                self.initial_password = None
                raise PermissionError(self.l10n.get("error-sudo-password-invalid")) # Локализовано

    def _get_amn0_ip(self) -> str:
        """Определяет и возвращает IP-адрес интерфейса amn0."""
        self.log(self.l10n.get("log-get-amn0-ip")) # Локализовано
        command = "ip -4 addr show amn0 | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}'"
        try:
            ip_address = self._execute(command).strip()
            if not ip_address:
                raise RuntimeError(self.l10n.get("error-amn0-ip-not-assigned")) # Локализовано
            self.log(self.l10n.get("log-amn0-ip-found", ip=ip_address)) # Локализовано
            self.amn0_ip = ip_address
            return ip_address
        except ChildProcessError:
             raise RuntimeError(self.l10n.get("error-amn0-interface-not-found")) # Локализовано

    def _configure_docker_compose(self):
        """Заменяет плейсхолдеры в docker-compose.yml на значения из UI."""
        ip = self._get_amn0_ip()

        compose_path = f"~/{REMOTE_PROJECT_DIR}/docker-compose.yaml"
        self.log(self.l10n.get("log-configure-docker-compose", path=compose_path)) # Локализовано

        sed_command = (
            f"sed -i "
            f"-e 's/^      - PORTMASTER_IP=.*/      - PORTMASTER_IP={ip}/' "
            f"-e 's/^      - PORTMASTER_PORT=.*/      - PORTMASTER_PORT={self.pm_port}/' "
            f"-e 's/^      - EXPOSED_PORT_RANGE=.*/      - EXPOSED_PORT_RANGE={self.pm_range}/' "
            f"{compose_path}"
        )

        self._execute(sed_command)
        self.log(self.l10n.get("log-docker-compose-configured")) # Локализовано

    def run_uninstallation(self):
        """Запускает процесс полного удаления Portmaster с сервера."""
        self.log(self.l10n.get("log-start-uninstallation")) # Локализовано
        try:
            if not self._check_for_existing_installation():
                self.log(self.l10n.get("log-nothing-to-uninstall")) # Локализовано
                self.log(self.l10n.get("log-uninstallation-completed")) # Локализовано
                return

            self._cleanup_previous_installation()
            self.log(self.l10n.get("log-uninstallation-successfully-completed")) # Локализовано

        except Exception as e:
            self.log(self.l10n.get("log-error-during-uninstallation", error_type=type(e).__name__, error=str(e))) # Локализовано

    def run_installation(self):
        try:
            if self._check_for_existing_installation():
                self.request_confirmation()
                if not self.confirmation_queue.get():
                    self.log(self.l10n.get("log-installation-canceled-by-user")) # Локализовано
                    return
                self._cleanup_previous_installation()


            self.log(self.l10n.get("log-stage-1-server-prep")) # Локализовано
            self._setup_server()
            self.log(self.l10n.get("log-server-prep-complete")) # Локализовано

            self.log(self.l10n.get("log-stage-2-pm-config")) # Локализовано
            self._configure_docker_compose()
            self.log(self.l10n.get("log-config-complete")) # Локализовано

            self.log(self.l10n.get("log-stage-3-docker-deploy")) # Локализовано
            self._deploy_docker()
            self.log(self.l10n.get("log-docker-deploy-complete")) # Локализовано

            self.log(self.l10n.get("log-stage-4-apply-net-rules")) # Локализовано
            self._apply_network_rules()
            self.log(self.l10n.get("log-net-rules-applied")) # Локализовано

            self._ensure_port_is_open()
            self.log(self.l10n.get("log-network-accessibility-confirmed")) # Локализовано

            self.log(self.l10n.get("log-installation-success")) # Локализовано
            self.log(self.l10n.get("log-installation-summary")) # Локализовано
            self.log(self.l10n.get("log-pm-available-at", ip=self.amn0_ip, port=self.pm_port)) # Локализовано
            self.log(self.l10n.get("log-port-range", pm_range=self.pm_range)) # Локализовано
            self.log(self.l10n.get("log-separator")) # Локализовано

        except Exception as e:
            self.log(self.l10n.get("log-critical-error", error_type=type(e).__name__, error=str(e))) # Локализовано

    def _setup_server(self):
        os_id = self.client.get_os_release_id()
        self.log(self.l10n.get("log-os-detected", os_id=os_id)) # Локализовано
        if os_id not in ("ubuntu", "debian"): raise NotImplementedError(self.l10n.get("error-os-not-supported", os_id=os_id)) # Локализовано
        remote_path = f"~/{REMOTE_PROJECT_DIR}"
        self.log(self.l10n.get("log-clone-repo")) # Локализовано
        self._execute(f"rm -rf {remote_path} && git clone {GIT_REPO_URL} {remote_path}")
        self.log(self.l10n.get("log-run-setup-script")) # Локализовано
        setup_script_path = f"{remote_path}/installer/setup_ubuntu.sh"
        self._execute(f"chmod +x {setup_script_path}")
        self._execute(setup_script_path, use_sudo=True)

    def _deploy_docker(self):
        remote_path = f"~/{REMOTE_PROJECT_DIR}"
        use_sudo = self.data['user'] != 'root'
        self.log(self.l10n.get("log-run-docker-compose", use_sudo=self.l10n.get("yes") if use_sudo else self.l10n.get("no"))) # Локализовано
        self._execute("docker compose up --build -d", use_sudo=use_sudo, working_dir=remote_path)

    def _apply_network_rules(self):
        remote_path = f"~/{REMOTE_PROJECT_DIR}"
        use_sudo = self.data['user'] != 'root'
        script_path = "./apply_portmaster_net_rules.sh"
        self.log(self.l10n.get("log-apply-net-rules", use_sudo=self.l10n.get("yes") if use_sudo else self.l10n.get("no"))) # Локализовано
        self._execute(f"chmod +x {script_path}", working_dir=remote_path)
        self._execute(script_path, use_sudo=use_sudo, working_dir=remote_path)

    def run_fix_routes(self):
        """Запускает процесс повторного применения сетевых правил."""
        self.log(self.l10n.get("log-start-fix-routes")) # Локализовано
        try:
            self._apply_network_rules()
            self.log(self.l10n.get("log-routes-fixed")) # Локализовано
        except Exception as e:
            self.log(self.l10n.get("log-error-during-fix-routes", error_type=type(e).__name__, error=str(e))) # Локализовано


# --- Главное приложение (UI) ---
class InstallerApp:
    """
    Основной класс Flet-приложения для установщика.
    """
    def __init__(self, page: ft.Page, l10n_manager: L10nManager):
        self.page = page
        self.l10n = l10n_manager # Сохраняем экземпляр L10nManager
        page.title = self.l10n.get("installer-title") # Локализовано
        self.confirmation_queue = queue.Queue(maxsize=1)

        default_user = get_current_username()
        default_key_path = find_default_ssh_key()

        # Инициализация всех UI-элементов с локализованными метками
        self.host = ft.TextField(label=self.l10n.get("label-host-ip"), expand=True)
        self.port = ft.TextField(label=self.l10n.get("label-ssh-port"), value="22", width=120)
        self.user = ft.TextField(label=self.l10n.get("label-user"), value=default_user if default_user else "root", expand=True)
        self.password = ft.TextField(label=self.l10n.get("label-user-password"), password=True, can_reveal_password=True, expand=True)
        self.key_path = ft.TextField(label=self.l10n.get("label-private-key-path"), value=default_key_path, read_only=True, expand=True)
        self.key_password = ft.TextField(label=self.l10n.get("label-key-password"), password=True,
                                         can_reveal_password=True)
        self.key_picker = ft.FilePicker(on_result=self._on_key_picked)
        page.overlay.append(self.key_picker)
        self.pick_btn = ft.ElevatedButton(
            self.l10n.get("button-pick-key"),
            icon=ft.Icons.FOLDER_OPEN,
            width=180,
            on_click=lambda _: self.key_picker.pick_files(dialog_title=self.l10n.get("dialog-pick-key-title"), allow_multiple=False),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=4),
                padding=ft.padding.symmetric(vertical=15, horizontal=15),
            )
        )
        self.log_output_column = ft.Column(spacing=5, expand=True, scroll=ft.ScrollMode.ADAPTIVE)
        self.install_btn = ft.ElevatedButton(
            self.l10n.get("button-install"),
            icon=ft.Icons.ROCKET_LAUNCH,
            on_click=self._on_install,
            width=130,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=4),
                bgcolor=ft.Colors.GREEN_700,
                color=ft.Colors.WHITE,
            )
        )

        self.fix_btn = ft.ElevatedButton(
            self.l10n.get("button-fix"),
            icon=ft.Icons.HEALING,
            on_click=self._on_fix_routes,
            tooltip=self.l10n.get("tooltip-fix-routes"),
            width=130,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=4),
                bgcolor=ft.Colors.BLUE_700,
                color=ft.Colors.WHITE,
            )
        )

        self.delete_btn = ft.ElevatedButton(
            self.l10n.get("button-delete"),
            icon=ft.Icons.DELETE_FOREVER,
            width=130,
            on_click=self._on_delete,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=4),
                bgcolor=ft.Colors.RED_700,
                color=ft.Colors.WHITE,
            )
        )

        self.progress = ft.ProgressRing(visible=False)
        self.copy_log_btn = ft.IconButton(
            icon=ft.Icons.COPY,
            tooltip=self.l10n.get("tooltip-copy-log"),
            on_click=self._copy_log_to_clipboard,
            style=ft.ButtonStyle(
                padding=0
            ),
            icon_size=16
        )
        self.pm_service_port = ft.TextField(
            label=self.l10n.get("label-portmaster-service-port"),
            value="5000",
            width=180
        )
        self.pm_pool_start = ft.TextField(label=self.l10n.get("label-pool-start"), value="20000", expand=True)
        self.pm_pool_end = ft.TextField(label=self.l10n.get("label-pool-end"), value="21000", expand=True)

        # Выпадающий список для выбора языка
        self.locale_dropdown = ft.Dropdown(
            options=[ft.dropdown.Option(locale) for locale in self.l10n.get_available_locales()],
            value=self.l10n.current_locale,
            on_change=self._on_locale_change,
            label="Language", # Метка самого выпадающего списка языка не локализуется
            width=150
        )

        # Первичная отрисовка UI
        self._build_ui()

    def _on_locale_change(self, e):
        """Обработчик изменения языка через выпадающий список."""
        new_locale = e.control.value
        if new_locale:
            self.l10n.set_locale(new_locale)
            self._rebuild_all_ui_elements() # Пересобираем UI с новым языком
            self.page.update()

    def _rebuild_all_ui_elements(self):
        """
        Обновляет текст всех UI-элементов в соответствии с текущей локалью.
        Вызывается после смены языка.
        """
        # Обновляем заголовок страницы
        self.page.title = self.l10n.get("installer-title")

        # Обновляем метки TextField
        self.host.label = self.l10n.get("label-host-ip")
        self.port.label = self.l10n.get("label-ssh-port")
        self.user.label = self.l10n.get("label-user")
        self.password.label = self.l10n.get("label-user-password")
        self.key_path.label = self.l10n.get("label-private-key-path")
        self.key_password.label = self.l10n.get("label-key-password")
        self.pm_service_port.label = self.l10n.get("label-portmaster-service-port")
        self.pm_pool_start.label = self.l10n.get("label-pool-start")
        self.pm_pool_end.label = self.l10n.get("label-pool-end")

        # Обновляем текст кнопок и подсказки
        self.pick_btn.text = self.l10n.get("button-pick-key")
        self.install_btn.text = self.l10n.get("button-install")
        self.fix_btn.text = self.l10n.get("button-fix")
        self.fix_btn.tooltip = self.l10n.get("tooltip-fix-routes")
        self.delete_btn.text = self.l10n.get("button-delete")
        self.copy_log_btn.tooltip = self.l10n.get("tooltip-copy-log")

        # Перестраиваем весь UI, чтобы применились новые локализованные строки
        self._build_ui()


    def _request_cleanup_confirmation(self):
        """Показывает диалог и ждет подтверждения от пользователя перед очисткой."""
        def close_dialog(e, confirmed: bool):
            self.confirmation_queue.put(confirmed)
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(self.l10n.get("dialog-existing-installation-title")), # Локализовано
            content=ft.Text(
                self.l10n.get("dialog-existing-installation-content", container_name=CONTAINER_NAME) # Локализовано
            ),
            actions=[
                ft.TextButton(self.l10n.get("button-cancel"), on_click=lambda e: close_dialog(e, False)), # Локализовано
                ft.ElevatedButton(self.l10n.get("button-yes-delete-continue"), on_click=lambda e: close_dialog(e, True), color=ft.Colors.WHITE, bgcolor=ft.Colors.RED), # Локализовано
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.update()
        self.page.open(dialog)

    def _on_key_picked(self, e: ft.FilePickerResultEvent):
        if e.files:
            self.key_path.value = e.files[0].path
            self.page.update()

    def _copy_log_to_clipboard(self, e):
        full_log = "\n".join([txt.value for txt in self.log_output_column.controls if isinstance(txt, ft.Text)])
        self.page.set_clipboard(full_log)
        self.page.snack_bar = ft.SnackBar(content=ft.Text(self.l10n.get("snackbar-log-copied")), duration=2000) # Локализовано
        self.page.snack_bar.open = True
        self.page.update()

    def _log(self, msg: str):
        """
        Добавляет сообщение в лог UI.
        Пытается локализовать стандартные префиксы сообщений.
        """
        text = msg.strip()
        if text:
            # Попытка локализовать известные префиксы сообщений лога
            if text.startswith("✅ "):
                text = self.l10n.get("log-prefix-success") + text[2:]
            elif text.startswith("⚠️ "):
                text = self.l10n.get("log-prefix-warning") + text[2:]
            elif text.startswith("❌ "):
                text = self.l10n.get("log-prefix-error") + text[2:]
            # Сообщения, приходящие из InstallationService, уже локализованы
            # или являются выводом команд, которые лучше оставить как есть.
            self.log_output_column.controls.append(ft.Text(text, font_family="Consolas", size=12, selectable=True))
            self.page.update()

    def _lock_ui(self, lock: bool):
        """Блокирует или разблокирует элементы UI во время выполнения операций."""
        for ctl in (self.install_btn, self.delete_btn, self.fix_btn, self.host, self.port,
                    self.user, self.password, self.pick_btn, self.key_path,
                    self.key_password, self.pm_service_port, self.pm_pool_start, self.pm_pool_end, self.locale_dropdown): # Добавлен locale_dropdown
            ctl.disabled = lock
        self.progress.visible = lock
        self.copy_log_btn.disabled = lock
        self.page.update()

    def _on_fix_routes(self, e):
        self.log_output_column.controls.clear()
        self.page.update()
        # Валидация входных данных для этой операции
        if not self.host.value or not self.port.value.isdigit() or not self.user.value:
            self._log(self.l10n.get("validation-error-host-port-user")) # Локализовано
            return
        if not self.key_path.value and not self.password.value:
            self._log(self.l10n.get("validation-error-password-key")) # Локализовано
            return

        self._lock_ui(True)
        threading.Thread(target=self._fix_routes_thread_entrypoint, daemon=True).start()

    def _fix_routes_thread_entrypoint(self):
        client = SecureSSHClient()
        try:
            self._log(self.l10n.get("log-connecting-to", user=self.user.value, host=self.host.value, port=self.port.value)) # Локализовано
            client.connect(
                hostname=self.host.value.strip(), port=int(self.port.value.strip()),
                username=self.user.value.strip(),
                password=self.password.value if not self.key_path.value else None,
                key_filename=self.key_path.value or None,
                key_password=self.key_password.value or None
            )
            self._log(self.l10n.get("log-connection-successful")) # Локализовано

            user_data = {'user': self.user.value.strip(), 'password': self.password.value}

            # Передаем self.l10n в InstallationService
            service = InstallationService(client=client, user_data=user_data, log_callback=self._log,
                                          confirmation_queue=None, request_confirmation_func=None, l10n=self.l10n)
            service.run_fix_routes()

        except Exception as ex:
            self._log(self.l10n.get("log-critical-error", error_type=type(ex).__name__, error=str(ex))) # Локализовано
        finally:
            client.close()
            self._lock_ui(False)

    def _on_delete(self, e):
        self.log_output_column.controls.clear()
        self.page.update()
        # Валидация входных данных для этой операции
        if not self.host.value or not self.port.value.isdigit() or not self.user.value:
            self._log(self.l10n.get("validation-error-host-port-user")) # Локализовано
            return
        if not self.key_path.value and not self.password.value:
            self._log(self.l10n.get("validation-error-password-key")) # Локализовано
            return

        self._lock_ui(True)
        threading.Thread(target=self._uninstallation_thread_entrypoint, daemon=True).start()

    def _request_delete_confirmation(self):
        """Показывает строгий диалог подтверждения удаления."""
        def close_dialog(e, confirmed: bool):
            self.confirmation_queue.put(confirmed)
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(self.l10n.get("dialog-confirm-delete-title")), # Локализовано
            content=ft.Text(
                self.l10n.get("dialog-confirm-delete-content", container_name=CONTAINER_NAME) # Локализовано
            ),
            actions=[
                ft.TextButton(self.l10n.get("button-cancel"), on_click=lambda e: close_dialog(e, False)), # Локализовано
                ft.ElevatedButton(self.l10n.get("button-yes-i-am-sure"), on_click=lambda e: close_dialog(e, True), color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_900), # Локализовано
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.dialog = dialog
        self.page.update()
        self.page.open(dialog)

    def _uninstallation_thread_entrypoint(self):
        client = SecureSSHClient()
        try:
            self._log(self.l10n.get("log-requesting-delete-confirmation")) # Локализовано
            self._request_delete_confirmation()
            if not self.confirmation_queue.get():
                self._log(self.l10n.get("log-delete-operation-canceled")) # Локализовано
                return

            self._log(self.l10n.get("log-connecting-to", user=self.user.value, host=self.host.value, port=self.port.value)) # Локализовано
            client.connect(
                hostname=self.host.value.strip(), port=int(self.port.value.strip()),
                username=self.user.value.strip(),
                password=self.password.value if not self.key_path.value else None,
                key_filename=self.key_path.value or None,
                key_password=self.key_password.value or None
            )
            self._log(self.l10n.get("log-connection-successful")) # Локализовано

            user_data = {'user': self.user.value.strip(), 'password': self.password.value}

            # Передаем self.l10n в InstallationService
            service = InstallationService(
                client=client, user_data=user_data, log_callback=self._log,
                request_confirmation_func=None, confirmation_queue=None, l10n=self.l10n
            )
            service.run_uninstallation()

        except Exception as ex:
            self._log(self.l10n.get("log-critical-error", error_type=type(ex).__name__, error=str(ex))) # Локализовано
        finally:
            client.close()
            self._lock_ui(False)

    def _validate_inputs(self) -> bool:
        """
        Валидация всех полей ввода перед началом установки.
        Сообщения об ошибках теперь локализованы.
        """
        if not self.host.value or not self.port.value.isdigit() or not self.user.value:
            self._log(self.l10n.get("validation-error-host-port-user"))
            return False
        if not self.key_path.value and not self.password.value:
            self._log(self.l10n.get("validation-error-password-key"))
            return False
        if not all(p.value.isdigit() for p in [self.pm_service_port, self.pm_pool_start, self.pm_pool_end]):
            self._log(self.l10n.get("validation-error-ports-numeric"))
            return False
        if  int(self.pm_service_port.value) < 1081:
            self._log(self.l10n.get("validation-error-port-too-low"))
            return False
        if  int(self.pm_pool_start.value) >= int(self.pm_pool_end.value):
            self._log(self.l10n.get("validation-error-invalid-range"))
            return False
        return True

    def _on_install(self, e):
        self.log_output_column.controls.clear()
        self.page.update()
        if not self._validate_inputs(): return
        self._lock_ui(True)
        threading.Thread(target=self._installation_thread_entrypoint, daemon=True).start()


    def _installation_thread_entrypoint(self):
        client = SecureSSHClient()
        try:
            self._log(self.l10n.get("log-connecting-to", user=self.user.value, host=self.host.value, port=self.port.value)) # Локализовано
            client.connect(
                hostname=self.host.value.strip(), port=int(self.port.value.strip()),
                username=self.user.value.strip(),
                password=self.password.value if not self.key_path.value else None,
                key_filename=self.key_path.value or None,
                key_password=self.key_password.value or None
            )
            self._log(self.l10n.get("log-connection-successful")) # Локализовано

            user_data = {
                'user': self.user.value.strip(),
                'password': self.password.value,
                'pm_port': self.pm_service_port.value,
                'pm_range': f"{self.pm_pool_start.value}-{self.pm_pool_end.value}"
            }

            # Передаем self.l10n в InstallationService
            service = InstallationService(
                client=client, user_data=user_data,
                log_callback=self._log,
                request_confirmation_func=self._request_cleanup_confirmation,
                confirmation_queue=self.confirmation_queue,
                l10n=self.l10n
            )
            service.run_installation()
        except Exception as ex:
            self._log(self.l10n.get("log-critical-error", error_type=type(ex).__name__, error=str(ex))) # Локализовано
        finally:
            client.close()
            self._lock_ui(False)

    def _build_ui(self):
        """
        Метод для построения (и перестроения) всего пользовательского интерфейса.
        """
        # Очищаем все существующие элементы и добавляем новые
        self.page.clean()
        self.page.add(
            ft.Row(
                controls=[
                    # --- КОЛОНКА 1: Подсказки и выбор языка ---
                    ft.Column(
                        width=250,
                        controls=[
                            # Выпадающий список для выбора языка
                            ft.Container(
                                content=self.locale_dropdown,
                                padding=ft.padding.only(bottom=20)
                            ),
                            ft.Container(
                                content=ft.Column(
                                    controls=[
                                        ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, color=ft.Colors.BLUE_400, size=20),
                                                ft.Text(self.l10n.get("section-connection-title"), weight=ft.FontWeight.BOLD)]),
                                        ft.Text(self.l10n.get("section-connection-text"), size=13,
                                                color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Divider(height=40),

                                        ft.Row([ft.Icon(ft.Icons.KEY, color=ft.Colors.AMBER_400, size=20),
                                                ft.Text(self.l10n.get("section-auth-title"), weight=ft.FontWeight.BOLD)]),
                                        ft.Text(
                                            self.l10n.get("section-auth-text-1"),
                                            size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Divider(height=40),
                                        ft.Text(
                                            self.l10n.get("section-auth-text-2"),
                                            size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Divider(height=40),

                                        ft.Text(
                                            self.l10n.get("section-auth-text-3"),
                                            size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Divider(height=40),

                                        ft.Row([ft.Icon(ft.Icons.SETTINGS_APPLICATIONS, color=ft.Colors.GREEN_400,
                                                        size=20), ft.Text(self.l10n.get("section-portmaster-title"), weight=ft.FontWeight.BOLD)]),
                                        ft.Text(self.l10n.get("section-portmaster-text-1"), size=13,
                                                color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Text(
                                            self.l10n.get("section-portmaster-text-2"),
                                            size=13,
                                            color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Text(
                                            self.l10n.get("section-portmaster-text-3"),
                                            size=13,
                                            color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Text(
                                            self.l10n.get("section-portmaster-text-4"),
                                            size=13,
                                            color=ft.Colors.ON_SURFACE_VARIANT),
                                    ],
                                    spacing=10
                                ),
                                expand=True,
                                padding=ft.padding.only(top=10, right=10)
                            )
                        ]
                    ),

                    # --- КОЛОНКА 2: Настройки (центр) ---
                    ft.Column(
                        width=550,
                        spacing=10,
                        scroll=ft.ScrollMode.ADAPTIVE,
                        controls=[
                            ft.Card(
                                ft.Container(
                                    content=ft.Column( spacing=15,controls=[
                                        ft.Text(self.l10n.get("card-connection-params"), weight=ft.FontWeight.BOLD, size=16),
                                        ft.Row([self.host, self.port]),
                                        self.user,
                                        self.password,
                                    ]),
                                    padding=20
                                )
                            ),
                            ft.Card(
                                ft.Container(
                                    content=ft.Column(spacing=15,controls=[
                                        ft.Text(self.l10n.get("card-key-auth"), weight=ft.FontWeight.BOLD, size=16),
                                        self.key_path,
                                        ft.Row([self.key_password, self.pick_btn]),
                                    ]),
                                    padding=20
                                )
                            ),
                            ft.Card(
                                ft.Container(
                                    content=ft.Column(spacing=15,controls=[
                                        ft.Text(self.l10n.get("card-portmaster-settings"), weight=ft.FontWeight.BOLD, size=16),
                                        self.pm_service_port,
                                        ft.Text(self.l10n.get("label-port-range")),
                                        ft.Row(
                                            controls=[
                                                self.pm_pool_start,
                                                ft.Text("-", size=20),
                                                self.pm_pool_end
                                            ],
                                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                                        )
                                    ]),
                                    padding=20
                                )
                            ),
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        ft.Container(
                                            content=self.install_btn,
                                            expand=True,
                                            height=60,
                                            alignment=ft.alignment.center,
                                        ),
                                        ft.Container(
                                            content=self.delete_btn,
                                            expand=True,
                                            height=60,
                                            alignment=ft.alignment.center,
                                        ),
                                        ft.Container(
                                            content=self.fix_btn,
                                            expand=True,
                                            height=60,
                                            alignment=ft.alignment.center,
                                        ),
                                        ft.Container(
                                            content=self.progress,
                                            width=60,
                                            height=60,
                                            alignment=ft.alignment.center,
                                        )
                                    ],
                                    spacing=10,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER
                                ),
                                alignment=ft.alignment.center,
                                padding=ft.padding.only(top=10, left=10, right=10, bottom=10),
                            )
                        ]
                    ),

                    ft.VerticalDivider(width=10),

                    # --- КОЛОНКА 3: Лог (справа) ---
                    ft.Column(
                        expand=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        controls=[
                            ft.Row(controls=[
                                ft.Text(self.l10n.get("log-output-title"), size=18, weight=ft.FontWeight.BOLD),
                                self.copy_log_btn
                            ],vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.Container(
                                content=self.log_output_column,
                                border=None,
                                padding=10,
                                width=300,
                                expand=True,
                            )
                        ]
                    )
                ],
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START
            )
        )
        self.page.update()


def main(page: ft.Page):
    """
    Основная функция Flet-приложения.
    """
    page.window.width = 1200
    page.window.height = 850
    page.window.min_width = 1200
    page.window.min_height = 850

    # Инициализация менеджера локализации. Он использует Singleton, так что будет только один экземпляр.
    l10n_manager = L10nManager()

    if is_path_critically_dangerous(REMOTE_PROJECT_DIR):
        # Передаем l10n_manager в диалог критической ошибки
        show_monkey_with_grenade_dialog(page, REMOTE_PROJECT_DIR, l10n_manager)
    else:
        # Передаем l10n_manager в InstallerApp
        InstallerApp(page, l10n_manager)


if __name__ == "__main__":
    ft.app(target=main)