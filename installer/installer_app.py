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

# --- КОНФИГУРАЦИЯ ---
GIT_REPO_URL = "https://github.com/protototo/amnezia-portmaster.git"
REMOTE_PROJECT_DIR = "amnezia-portmaster"
UFW_RULE_COMMENT = "Added-by-Amnezia-Portmaster-Installer"
CONTAINER_NAME = "portmaster"

# --- Утилиты и диалоги (без изменений) ---
def is_path_critically_dangerous(path_str: str) -> bool:
    if not isinstance(path_str, str) or not path_str.strip(): return True
    path = path_str.strip()
    if path.startswith('/') or '..' in path.split('/') or path.startswith('./'): return True
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', path): return True
    if path in ('.', '..'): return True
    return False


def show_monkey_with_grenade_dialog(page: ft.Page, dangerous_path: str):
    def close_dialog(e):
        dialog.open = False
        page.update()
        page.window.destroy()

    dialog = ft.AlertDialog(modal=True,
                            title=ft.Row([ft.Text("🐒💣", size=40), ft.Text(" КРИТИЧЕСКАЯ ОШИБКА!", size=20)]),
                            content=ft.Text(
                                f"Конфигурация REMOTE_PROJECT_DIR = '{dangerous_path}' небезопасна.\n\nИсправьте константу и перезапустите приложение.",
                                size=14, text_align=ft.TextAlign.CENTER), actions=[
            ft.ElevatedButton("Понял", on_click=close_dialog, color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_700)],
                            actions_alignment=ft.MainAxisAlignment.END)
    page.update()
    page.open(dialog)


# --- Утилиты для умных дефолтов ---
def get_current_username() -> str | None:
    """
    Возвращает имя текущего пользователя.
    Использует самый надежный метод для Unix-систем (включая macOS).
    """
    # Проверяем, что мы не на Windows
    if os.name == 'posix':
        try:
            # Это самый надежный способ, который работает даже из IDE
            return pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            # Крайне редкий случай, если UID не найден в базе пользователей
            return None
    return None


def find_default_ssh_key() -> str | None:
    """Ищет стандартный SSH ключ (id_rsa или id_ed25519) в ~/.ssh/"""
    if os.name == 'posix':
        home_dir = pathlib.Path.home()
        ssh_dir = home_dir / ".ssh"

        # Список стандартных имен ключей для проверки
        default_keys = ["id_ed25519", "id_rsa"]

        for key_name in default_keys:
            key_path = ssh_dir / key_name
            if key_path.is_file():
                return str(key_path)  # Возвращаем путь как строку
    return None


# --- SSH-клиент (Финальная рабочая версия) ---
class SecureSSHClient:
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


# --- Сервис установки (С ИСПРАВЛЕННОЙ ЛОГИКОЙ ВЫПОЛНЕНИЯ КОМАНД) ---
class InstallationService:
    def __init__(self, client: SecureSSHClient, user_data: dict, log_callback: Callable[[str], None],
                 request_confirmation_func: Callable[[], None], confirmation_queue: queue.Queue
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

    def _check_for_existing_installation(self) -> bool:
        """Проверяет, существует ли уже контейнер Portmaster."""
        self.log("Проверка на наличие предыдущих установок...")
        # `docker ps -a` показывает все контейнеры, даже остановленные
        # `grep -q` работает в "тихом" режиме, возвращая только код завершения
        command = f"docker ps -a --format '{{{{.Names}}}}' | grep -q '^{CONTAINER_NAME}$'"
        try:
            # sudo здесь не всегда нужно, но лучше перестраховаться, если docker настроен для рута
            use_sudo = self.data['user'] != 'root'
            self._execute(command, use_sudo=use_sudo)
            # Если команда завершилась с кодом 0, значит grep нашел контейнер
            self.log(f"⚠️ Обнаружен существующий контейнер '{CONTAINER_NAME}'.")
            return True
        except ChildProcessError:
            self.log("✅ Предыдущих установок не найдено.")
            return False

    def _execute(self, command: str, use_sudo=False, working_dir: str | None = None):
        """
        Собирает и выполняет команду, ПРАВИЛЬНО обрабатывая `cd` и `sudo`.
        """
        # --- ПРОСТАЯ И НАДЕЖНАЯ ЛОГИКА СБОРКИ КОМАНДЫ ---

        command_to_run = command
        password_for_sudo = None

        if use_sudo:
            # Сначала проверяем, есть ли у нас пароль
            if not self.confirmed_sudo_password:
                # Если нет, получаем его (через пароль пользователя или диалог)
                self._obtain_sudo_password()

            password_for_sudo = self.confirmed_sudo_password
            command_to_run = f"sudo -S -p '' {command}"

        # `cd` всегда идет в самом начале, перед `sudo`
        if working_dir:
            command_to_run = f"cd {working_dir} && {command_to_run}"

        # Выполняем собранную команду
        return self.client.execute_command(command_to_run, self.log, sudo_password=password_for_sudo)

    def _cleanup_ufw_rules(self):
        """Находит все правила UFW по комментарию и удаляет их в правильном порядке."""
        self.log("Поиск и удаление старых правил UFW...")

        try:
            # 1. Получаем полный статус файрвола
            status_output = self._execute("sudo ufw status numbered", use_sudo=True)

            rules_to_delete = []
            # 2. Парсим вывод в Python
            for line in status_output.splitlines():
                if UFW_RULE_COMMENT in line:
                    # Ищем номер правила в квадратных скобках
                    match = re.search(r"\[\s*(\d+)\s*\]", line)
                    if match:
                        rule_number = int(match.group(1))
                        rules_to_delete.append(rule_number)

            if not rules_to_delete:
                self.log("✅ Правил UFW, созданных установщиком, не найдено.")
                return

            # 3. Сортируем номера в ОБРАТНОМ порядке для безопасного удаления
            rules_to_delete.sort(reverse=True)
            self.log(f"Обнаружены правила для удаления: {rules_to_delete}")

            # 4. Удаляем каждое правило по очереди
            for num in rules_to_delete:
                self.log(f"Удаление правила UFW номер {num}...")
                # --force используется, чтобы избежать интерактивного запроса "y/n"
                self._execute(f"sudo ufw --force delete {num}", use_sudo=True)

            self.log("✅ Старые правила UFW успешно удалены.")

        except ChildProcessError:
            self.log("⚠️ Команда `ufw` не найдена или неактивна. Пропускаем очистку правил.")
        except Exception as e:
            self.log(f"❌ Произошла ошибка при очистке правил UFW: {e}")

    def _cleanup_previous_installation(self):
        """Останавливает и удаляет старый контейнер и его правила UFW."""
        self.log("Начало очистки предыдущей установки...")
        use_sudo = self.data['user'] != 'root'

        # 1. Удаляем контейнер
        self.log(f"Остановка и удаление контейнера '{CONTAINER_NAME}'...")
        cleanup_command = f"docker stop {CONTAINER_NAME} || true && docker rm {CONTAINER_NAME}"
        try:
            self._execute(cleanup_command, use_sudo=use_sudo)
        except ChildProcessError:
            # Не страшно, если упало, возможно контейнера и не было
            self.log(f"Не удалось удалить контейнер '{CONTAINER_NAME}' (возможно, его не было).")

        # 2. Вызываем новый надежный метод для очистки UFW
        self._cleanup_ufw_rules()


    def _ensure_port_is_open(self):
        """Проверяет доступность порта с клиента и открывает его в UFW при необходимости."""
        self.log(f"\nЭтап 5: Проверка доступности порта {self.pm_port}...")

        # Шаг 1: Первая попытка подключения с клиента
        self.log(f"Попытка подключения к {self.amn0_ip}:{self.pm_port}...")
        try:
            with socket.create_connection((self.amn0_ip, self.pm_port), timeout=5):
                self.log(f"✅ Порт {self.pm_port} уже открыт и доступен!")
                return  # Все хорошо, выходим
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            self.log(f"⚠️ Порт недоступен: {e}. Приступаем к диагностике файрвола...")

        # Шаг 2: Диагностика UFW на сервере
        try:
            ufw_status_output = self._execute("sudo ufw status", use_sudo=True)
            if "Status: inactive" in ufw_status_output:
                # Если UFW неактивен, а порт недоступен - проблема в другом
                raise RuntimeError(
                    f"Порт {self.pm_port} недоступен, но UFW неактивен. "
                    "Возможные проблемы: ошибка в Docker-контейнере, другая сетевая проблема."
                )
        except ChildProcessError:
            # Если команда ufw не найдена, считаем, что файрвола нет
            raise RuntimeError(f"Порт {self.pm_port} недоступен и команда `ufw` не найдена на сервере.")

        # Шаг 3: Исправление - открываем порт
        self.log("UFW активен. Добавляем правило, чтобы разрешить трафик...")
        self._execute(f"sudo ufw allow {self.pm_port}/tcp comment '{UFW_RULE_COMMENT}'", use_sudo=True)
        self.log(f"✅ Правило для порта {self.pm_port} добавлено в UFW.")

        # Шаг 4: Верификация - вторая попытка подключения
        self.log(f"Повторная проверка доступности порта {self.amn0_ip}:{self.pm_port}...")
        try:
            with socket.create_connection((self.amn0_ip, self.pm_port), timeout=5):
                self.log(f"✅ Отлично! Порт {self.pm_port} теперь открыт и доступен.")
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            # Если и после открытия порта он недоступен - что-то не так
            raise RuntimeError(
                f"Порт {self.pm_port} был открыт в UFW, но по-прежнему недоступен: {e}. "
                "Проверьте настройки сети и Docker на сервере."
            )


    def _obtain_sudo_password(self):
        """
        Вспомогательный метод, который инкапсулирует логику получения пароля.
        Вызывается только когда пароль действительно нужен.
        """
        # 1. Пробуем пароль из UI
        if self.initial_password:
            self.log("Требуются права суперпользователя. Проверяем пароль пользователя для sudo...")
            # Для проверки мы выполняем простую команду, которая требует sudo
            test_command = "sudo -S -p '' ls /root"
            try:
                self.client.execute_command(test_command, self.log, sudo_password=self.initial_password)
                self.log("✅ Пароль пользователя подходит для sudo. Запоминаем его.")
                self.confirmed_sudo_password = self.initial_password
                self.initial_password = None  # Больше не будем его проверять
                return
            except (PermissionError, ChildProcessError):
                self.log("❌ Пароль пользователя не подходит для sudo.")
                self.initial_password = None  # Отмечаем, что он не подошел
                raise PermissionError("Пароль пользователя не подходит для sudo. Установка прервана.")

    def _get_amn0_ip(self) -> str:
        """Определяет и возвращает IP-адрес интерфейса amn0."""
        self.log("Определение IP-адреса интерфейса amn0...")
        # Команда для извлечения IPv4 адреса из вывода `ip addr`
        command = "ip -4 addr show amn0 | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}'"
        try:
            ip_address = self._execute(command).strip()
            if not ip_address:
                raise RuntimeError("Интерфейс amn0 найден, но IP-адрес не назначен.")
            self.log(f"✅ IP-адрес amn0: {ip_address}")
            self.amn0_ip = ip_address
            return ip_address
        except ChildProcessError:
             raise RuntimeError("Не удалось найти интерфейс amn0. Убедитесь, что AmneziaVPN установлена и запущена.")

    def _configure_docker_compose(self):
        """Заменяет плейсхолдеры в docker-compose.yml на значения из UI."""
        ip = self._get_amn0_ip()

        compose_path = f"~/{REMOTE_PROJECT_DIR}/docker-compose.yaml"
        self.log(f"Настройка файла {compose_path}...")

        # Мы используем sed с опцией -e для выполнения нескольких замен за один вызов.
        # Это эффективнее, чем вызывать sed три раза.
        sed_command = (
            f"sed -i "
            f"-e 's/^      - PORTMASTER_IP=.*/      - PORTMASTER_IP={ip}/' "
            f"-e 's/^      - PORTMASTER_PORT=.*/      - PORTMASTER_PORT={self.pm_port}/' "
            f"-e 's/^      - EXPOSED_PORT_RANGE=.*/      - EXPOSED_PORT_RANGE={self.pm_range}/' "
            f"{compose_path}"
        )

        # Модификация файла, который мы только что склонировали, не требует sudo
        self._execute(sed_command)
        self.log("✅ docker-compose.yml успешно настроен.")

    def run_uninstallation(self):
        """Запускает процесс полного удаления Portmaster с сервера."""
        self.log("\n--- Начало процесса удаления ---")
        try:
            if not self._check_for_existing_installation():
                self.log("Нечего удалять. Установка Portmaster не найдена.")
                self.log("✅ --- Процесс удаления завершен --- ✅")
                return

            self._cleanup_previous_installation()
            self.log("✅ --- Процесс удаления успешно завершен --- ✅")

        except Exception as e:
            self.log(f"\n--- ❌ ОШИБКА ПРИ УДАЛЕНИИ ---\n{type(e).__name__}: {e}")

    def run_installation(self):
        try:

            if self._check_for_existing_installation():
                self.request_confirmation()
                if not self.confirmation_queue.get():
                    self.log("Установка отменена пользователем.")
                    return
                self._cleanup_previous_installation()


            self.log("Этап 1: Подготовка сервера...")
            self._setup_server()
            self.log("✅ Сервер успешно подготовлен.\n")

            # --- ИЗМЕНЕНИЕ: Добавляем шаг конфигурации перед деплоем ---
            self.log("Этап 2: Конфигурация Portmaster...")
            self._configure_docker_compose()
            self.log("✅ Конфигурация успешно завершена.\n")

            self.log("Этап 3: Развертывание Docker контейнеров...")
            self._deploy_docker()
            self.log("✅ Docker контейнеры успешно развернуты.\n")

            self.log("Этап 4: Применение сетевых правил...")
            self._apply_network_rules()
            self.log("✅ Сетевые правила успешно применены.\n")

            self._ensure_port_is_open()
            self.log("✅ Сетевая доступность к сервису подтверждена.\n")

            # --- ИЗМЕНЕНИЕ: Добавляем финальное саммари ---
            self.log("🎉 --- УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА --- 🎉")
            self.log("\n--- Итоги установки ---")
            self.log(f"Portmaster доступен по адресу: {self.amn0_ip}:{self.pm_port}")
            self.log(f"Диапазон портов для проброса: {self.pm_range}")
            self.log("-------------------------\n")

        except Exception as e:
            self.log(f"\n--- ❌ КРИТИЧЕСКАЯ ОШИБКА ---\n{type(e).__name__}: {e}")

    def _setup_server(self):
        os_id = self.client.get_os_release_id()
        self.log(f"Обнаружена ОС: {os_id}")
        if os_id not in ("ubuntu", "debian"): raise NotImplementedError(f"Установка на {os_id} не поддерживается.")
        remote_path = f"~/{REMOTE_PROJECT_DIR}"
        self.log("Клонирование репозитория...")
        self._execute(f"rm -rf {remote_path} && git clone {GIT_REPO_URL} {remote_path}")
        self.log("Запуск скрипта настройки (setup_ubuntu.sh)...")
        setup_script_path = f"{remote_path}/installer/setup_ubuntu.sh"
        self._execute(f"chmod +x {setup_script_path}")
        # Здесь working_dir не нужен, т.к. путь абсолютный
        self._execute(setup_script_path, use_sudo=True)

    def _deploy_docker(self):
        remote_path = f"~/{REMOTE_PROJECT_DIR}"
        use_sudo = self.data['user'] != 'root'
        self.log(f"Запуск docker compose... (Sudo: {'Да' if use_sudo else 'Нет'})")
        self._execute("docker compose up --build -d", use_sudo=use_sudo, working_dir=remote_path)

    def _apply_network_rules(self):
        remote_path = f"~/{REMOTE_PROJECT_DIR}"
        use_sudo = self.data['user'] != 'root'
        script_path = "./apply_portmaster_net_rules.sh"  # Используем относительный путь
        self.log(f"Применение сетевых правил... (Sudo: {'Да' if use_sudo else 'Нет'})")
        self._execute(f"chmod +x {script_path}", working_dir=remote_path)
        self._execute(script_path, use_sudo=use_sudo, working_dir=remote_path)

    def run_fix_routes(self):
        """Запускает процесс повторного применения сетевых правил."""
        self.log("\n--- Начало процесса восстановления маршрутов ---")
        try:
            # Просто вызываем существующий, отлаженный метод
            self._apply_network_rules()
            self.log("✅ --- Маршруты успешно восстановлены --- ✅")
        except Exception as e:
            self.log(f"\n--- ❌ ОШИБКА ПРИ ВОССТАНОВЛЕНИИ ---\n{type(e).__name__}: {e}")


# --- Главное приложение (UI) ---
class InstallerApp:
    # ... __init__ и другие методы без изменений до _installation_thread_entrypoint ...
    def __init__(self, page: ft.Page):
        self.page = page
        page.title = "Установщик Amnezia Portmaster"
        self.confirmation_queue = queue.Queue(maxsize=1)

        default_user = get_current_username()
        default_key_path = find_default_ssh_key()

        self.host = ft.TextField(label="Host/IP", expand=True)
        self.port = ft.TextField(label="SSH Port", value="22", width=120)
        self.user = ft.TextField(label="User", value=default_user if default_user else "root", expand=True)
        self.password = ft.TextField(label="Пароль пользователя", password=True, can_reveal_password=True, expand=True)
        self.key_path = ft.TextField(label="Путь к приватному SSH ключу", value=default_key_path, read_only=True, expand=True)
        self.key_password = ft.TextField(label="Пароль от SSH ключа (если есть)", password=True,
                                         can_reveal_password=True)
        self.key_picker = ft.FilePicker(on_result=self._on_key_picked)
        page.overlay.append(self.key_picker)
        self.pick_btn = ft.ElevatedButton(
            "Выбрать ключ",
            icon=ft.Icons.FOLDER_OPEN,
            width=180,
            on_click=lambda _: self.key_picker.pick_files(dialog_title="Выберите приватный ключ",allow_multiple=False),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=4),
                padding=ft.padding.symmetric(vertical=15, horizontal=15),
            )
        )
        self.log_output_column = ft.Column(spacing=5, expand=True, scroll=ft.ScrollMode.ADAPTIVE)
        self.install_btn = ft.ElevatedButton(
            "Установить",
            icon=ft.Icons.ROCKET_LAUNCH,
            on_click=self._on_install,
            width=130,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=4),  # Оставляем скругление
                bgcolor=ft.Colors.GREEN_700,
                color=ft.Colors.WHITE,
            )
        )

        self.fix_btn = ft.ElevatedButton(
            "Исправить",
            icon=ft.Icons.HEALING,
            on_click=self._on_fix_routes,
            tooltip="Применить сетевые правила повторно, если они сбросились после рестарта контейнеров или хоста",
            width=130,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=4),  # Оставляем скругление
                bgcolor=ft.Colors.BLUE_700,
                color=ft.Colors.WHITE,
            )
        )

        self.delete_btn = ft.ElevatedButton(
            "Удалить",
            icon=ft.Icons.DELETE_FOREVER,
            width=130,
            on_click=self._on_delete,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=4),  # Оставляем скругление
                bgcolor=ft.Colors.RED_700,
                color=ft.Colors.WHITE,
            )
        )

        self.progress = ft.ProgressRing(visible=False)
        self.copy_log_btn = ft.IconButton(
            icon=ft.Icons.COPY,
            tooltip="Скопировать лог",
            on_click=self._copy_log_to_clipboard,
            # Задаем стиль, чтобы убрать лишние отступы
            style=ft.ButtonStyle(
                padding=0  # Нулевые внутренние отступы для максимальной компактности
            ),
            icon_size=16 # Опционально можно чуть уменьшить и саму иконку
        )
        self.pm_service_port = ft.TextField(
            label="Порт сервиса Portmaster",
            value="5000",
            width=180
        )
        self.pm_pool_start = ft.TextField(label="Начало пула", value="20000", expand=True)
        self.pm_pool_end = ft.TextField(label="Конец пула", value="21000", expand=True)
        self.log_output_column = ft.Column(spacing=5, expand=True, scroll=ft.ScrollMode.ADAPTIVE)

        self._build_ui()

    def _request_cleanup_confirmation(self):
        """Показывает диалог и ждет подтверждения от пользователя."""
        def close_dialog(e, confirmed: bool):
            self.confirmation_queue.put(confirmed)
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("⚠️ Обнаружена предыдущая установка"),
            content=ft.Text(
                f"На сервере уже запущен контейнер с именем '{CONTAINER_NAME}'.\n\n"
                "Продолжить? Это приведет к остановке и удалению существующего контейнера и связанных с ним правил файрвола."
            ),
            actions=[
                ft.TextButton("Отмена", on_click=lambda e: close_dialog(e, False)),
                ft.ElevatedButton("Да, удалить и продолжить", on_click=lambda e: close_dialog(e, True), color=ft.Colors.WHITE, bgcolor=ft.Colors.RED),
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
        self.page.snack_bar = ft.SnackBar(content=ft.Text("Лог скопирован в буфер обмена!"), duration=2000)
        self.page.snack_bar.open = True
        self.page.update()

    def _log(self, msg: str):
        text = msg.strip()
        if text:
            self.log_output_column.controls.append(ft.Text(text, font_family="Consolas", size=12, selectable=True))
            self.page.update()

    def _lock_ui(self, lock: bool):
        # --- ДОБАВЛЯЕМ НОВУЮ КНОПКУ В БЛОКИРОВКУ ---
        for ctl in (self.install_btn, self.delete_btn, self.fix_btn, self.host, self.port,
                    self.user, self.password, self.pick_btn, self.key_path,
                    self.key_password, self.pm_service_port, self.pm_pool_start, self.pm_pool_end):
            ctl.disabled = lock
        self.progress.visible = lock
        self.copy_log_btn.disabled = lock
        self.page.update()

    def _on_fix_routes(self, e):
        self.log_output_column.controls.clear()
        self.page.update()
        # Для этой операции нам нужны только данные для подключения
        if not self.host.value or not self.port.value.isdigit() or not self.user.value:
            self._log("❌ Ошибка: Для восстановления маршрутов необходимо заполнить поля Host/IP, Port и User.")
            return
        if not self.key_path.value and not self.password.value:
            self._log("❌ Ошибка: Укажите пароль или ключ для подключения к серверу.")
            return

        self._lock_ui(True)
        threading.Thread(target=self._fix_routes_thread_entrypoint, daemon=True).start()

    def _fix_routes_thread_entrypoint(self):
        client = SecureSSHClient()
        try:
            self._log(f"Подключение к {self.user.value}@{self.host.value}:{self.port.value}...")
            client.connect(
                hostname=self.host.value.strip(), port=int(self.port.value.strip()),
                username=self.user.value.strip(),
                password=self.password.value if not self.key_path.value else None,
                key_filename=self.key_path.value or None,
                key_password=self.key_password.value or None
            )
            self._log("✅ Подключение успешно установлено!")

            user_data = {'user': self.user.value.strip(), 'password': self.password.value}

            # Создаем сервис и вызываем НОВЫЙ метод
            service = InstallationService(client=client, user_data=user_data, log_callback=self._log,confirmation_queue=None, request_confirmation_func=None)
            service.run_fix_routes()

        except Exception as ex:
            self._log(f"\n--- ❌ КРИТИЧЕСКАЯ ОШИБКА ---\n{type(ex).__name__}: {ex}")
        finally:
            client.close()
            self._lock_ui(False)

    def _on_delete(self, e):
        self.log_output_column.controls.clear()
        self.page.update()
        # Для удаления нам нужны только данные для подключения
        if not self.host.value or not self.port.value.isdigit() or not self.user.value:
            self._log("❌ Ошибка: Для удаления необходимо заполнить поля Host/IP, Port и User.")
            return
        if not self.key_path.value and not self.password.value:
            self._log("❌ Ошибка: Укажите пароль или ключ для подключения к серверу.")
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
            title=ft.Text("⚠️ Подтвердите удаление"),
            content=ft.Text(
                f"Вы уверены, что хотите НАВСЕГДА удалить контейнер '{CONTAINER_NAME}' и все связанные с ним правила файрвола с сервера?\n\nЭто действие необратимо."
            ),
            actions=[
                ft.TextButton("Отмена", on_click=lambda e: close_dialog(e, False)),
                ft.ElevatedButton("Да, я уверен, удалить", on_click=lambda e: close_dialog(e, True), color=ft.Colors.WHITE, bgcolor=ft.Colors.RED_900),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.dialog = dialog
        self.page.update()
        self.page.open(dialog)

    def _uninstallation_thread_entrypoint(self):
        client = SecureSSHClient()
        try:
            # 1. Запрашиваем подтверждение ПЕРЕД подключением
            self._log("Требуется подтверждение на удаление...")
            self._request_delete_confirmation()
            if not self.confirmation_queue.get():
                self._log("Операция удаления отменена пользователем.")
                return

            # 2. Теперь подключаемся
            self._log(f"Подключение к {self.user.value}@{self.host.value}:{self.port.value}...")
            client.connect(
                hostname=self.host.value.strip(), port=int(self.port.value.strip()),
                username=self.user.value.strip(),
                password=self.password.value if not self.key_path.value else None,
                key_filename=self.key_path.value or None,
                key_password=self.key_password.value or None
            )
            self._log("✅ Подключение успешно установлено!")

            user_data = {'user': self.user.value.strip(), 'password': self.password.value}

            # 3. Создаем сервис и вызываем НОВЫЙ метод
            service = InstallationService(
                client=client, user_data=user_data, log_callback=self._log,
                request_confirmation_func=None, confirmation_queue=None
            )
            service.run_uninstallation()

        except Exception as ex:
            self._log(f"\n--- ❌ КРИТИЧЕСКАЯ ОШИБКА ---\n{type(ex).__name__}: {ex}")
        finally:
            client.close()
            self._lock_ui(False)

    def _validate_inputs(self) -> bool:
        if not self.host.value or not self.port.value.isdigit() or not self.user.value:
            self._log("❌ Ошибка: Заполните поля Host/IP, Port и User.")
            return False
        if not self.key_path.value and not self.password.value:
            self._log("❌ Ошибка: Укажите пароль пользователя или выберите SSH ключ.")
            return False
        if not all(p.value.isdigit() for p in [self.pm_service_port, self.pm_pool_start, self.pm_pool_end]):
            self._log("❌ Ошибка: Порты и диапазон пула Portmaster должны быть числами.")
            return False
        if  int(self.pm_service_port.value) < 1081:
            self._log("❌ Ошибка: Порт Portmaster должен быть больше 1080.")
            return False
        if  int(self.pm_pool_start.value) >= int(self.pm_pool_end.value):
            self._log("❌ Ошибка: Введите корректный диапазон портов")
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
            self._log(f"Подключение к {self.user.value}@{self.host.value}:{self.port.value}...")
            client.connect(
                hostname=self.host.value.strip(), port=int(self.port.value.strip()),
                username=self.user.value.strip(),
                password=self.password.value if not self.key_path.value else None,
                key_filename=self.key_path.value or None,
                key_password=self.key_password.value or None
            )
            self._log("✅ Подключение успешно установлено!")

            user_data = {
                'user': self.user.value.strip(),
                'password': self.password.value,
                'pm_port': self.pm_service_port.value,
                'pm_range': f"{self.pm_pool_start.value}-{self.pm_pool_end.value}"
            }

            service = InstallationService(
                client=client, user_data=user_data,
                log_callback=self._log,
                request_confirmation_func=self._request_cleanup_confirmation,
                confirmation_queue=self.confirmation_queue
            )
            service.run_installation()
        except Exception as ex:
            self._log(f"\n--- ❌ КРИТИЧЕСКАЯ ОШИБКА ---\n{type(ex).__name__}: {ex}")
        finally:
            client.close()
            self._lock_ui(False)

    def _build_ui(self):
        self.page.clean()
        self.page.add(
            ft.Row(
                controls=[
                    # --- КОЛОНКА 1: Подсказки и кнопка установки (слева) ---
                    ft.Column(
                        width=250,
                        controls=[
                            # Контейнер для подсказок, чтобы они занимали доступное пространство
                            ft.Container(
                                content=ft.Column(
                                    controls=[
                                        ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, color=ft.Colors.BLUE_400, size=20),
                                                ft.Text("Подключение", weight=ft.FontWeight.BOLD)]),
                                        ft.Text("Введите данные для подключения к вашему серверу по SSH.", size=13,
                                                color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Divider(height=40),

                                        ft.Row([ft.Icon(ft.Icons.KEY, color=ft.Colors.AMBER_400, size=20),
                                                ft.Text("Авторизация", weight=ft.FontWeight.BOLD)]),
                                        ft.Text(
                                            "Рекомендуется использовать SSH-ключ. Если ключ не выбран, будет использован пароль.",
                                            size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Divider(height=40),
                                        ft.Text(
                                            "Убедитесь что у пользователя есть права root",
                                            size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Divider(height=40),

                                        ft.Text(
                                            "Для работы через sudo нужно ввести пароль даже при авторизации по ключу",
                                            size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Divider(height=40),

                                        ft.Row([ft.Icon(ft.Icons.SETTINGS_APPLICATIONS, color=ft.Colors.GREEN_400,
                                                        size=20), ft.Text("Portmaster", weight=ft.FontWeight.BOLD)]),
                                        ft.Text("Настройте порты для работы сервиса и проброса портов.", size=13,
                                                color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Text(
                                            "Порты пробрасываются позже клиентом из указанного диапазона."
                                            ,
                                            size=13,
                                            color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Text(
                                            "Клиенты подключающиеся к разным VPN протоколам используют один и тот же пул портов. 1000 портов обычно хвататет. ",
                                            size=13,
                                            color=ft.Colors.ON_SURFACE_VARIANT),
                                        ft.Text(
                                            "Portmaster будет жить в отдельном контейнере. Установка не разорвет существующие VPN подключения.",
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
                                        ft.Text("Параметры подключения", weight=ft.FontWeight.BOLD, size=16),
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
                                        ft.Text("Авторизация по ключу", weight=ft.FontWeight.BOLD, size=16),
                                        self.key_path,
                                        ft.Row([self.key_password, self.pick_btn]),
                                    ]),
                                    padding=20
                                )
                            ),
                            ft.Card(
                                ft.Container(
                                    content=ft.Column(spacing=15,controls=[
                                        ft.Text("Настройки Portmaster", weight=ft.FontWeight.BOLD, size=16),
                                        self.pm_service_port,
                                        ft.Text("Диапазон портов для проброса:"),
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
                            # Контейнер для кнопок в самом низу
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        # Контейнер для кнопки "Установить"
                                        ft.Container(
                                            content=self.install_btn,
                                            expand=True,  # Занимает все доступное место (пропорционально)
                                            height=60,  # Задаем фиксированную высоту для всех кнопок
                                            alignment=ft.alignment.center,
                                        ),
                                        # Контейнер для кнопки "Удалить"
                                        ft.Container(
                                            content=self.delete_btn,
                                            expand=True,  # Занимает все доступное место (пропорционально)
                                            height=60,  # Задаем фиксированную высоту для всех кнопок
                                            alignment=ft.alignment.center,
                                        ),
                                        ft.Container(
                                            content=self.fix_btn,
                                            expand=True,  # Занимает все доступное место (пропорционально)
                                            height=60,  # Задаем фиксированную высоту для всех кнопок
                                            alignment=ft.alignment.center,
                                        ),
                                        # Контейнер для индикатора прогресса
                                        ft.Container(
                                            content=self.progress,
                                            width=60,  # Фиксированная ширина, чтобы не влиять на кнопки
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
                        expand=True,  # Эта колонка займет все оставшееся место
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        controls=[
                            ft.Row(controls=[
                                ft.Text("Лог выполнения", size=18, weight=ft.FontWeight.BOLD),
                                self.copy_log_btn
                            ],vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.Container(
                                content=self.log_output_column,
                                border=None,
                                padding=10,
                                width=300,
                                expand=True,  # Контейнер лога растягивается на всю высоту колонки
                            )
                        ]
                    )
                ],
                expand=True,  # Главный Row растягивается на всю высоту и ширину окна
                vertical_alignment=ft.CrossAxisAlignment.START
            )
        )
        self.page.update()


def main(page: ft.Page):
    page.window.width = 1200
    page.window.height = 850
    page.window.min_width = 1200
    page.window.min_height = 850
    if is_path_critically_dangerous(REMOTE_PROJECT_DIR):
        show_monkey_with_grenade_dialog(page, REMOTE_PROJECT_DIR)
    else:
        InstallerApp(page)


if __name__ == "__main__":
    ft.app(target=main)