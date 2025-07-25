import pathlib
import pwd
import re
import threading
from typing import Callable

import flet as ft
import paramiko
import os

# --- КОНФИГУРАЦИЯ ---
GIT_REPO_URL = "https://github.com/protototo/amnezia-portmaster.git"
REMOTE_PROJECT_DIR = "amnezia-portmaster"


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
    #page.dialog = dialog
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
    def __init__(self, client: SecureSSHClient, user_data: dict, log_callback: Callable[[str], None]
                 ):
        self.client = client
        self.data = user_data
        self.log = log_callback
        self.initial_password = user_data.get('password')
        self.confirmed_sudo_password = None

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

    # Остальные методы сервиса без изменений
    def run_installation(self):
        try:
            self.log("Этап 1: Подготовка сервера...")
            self._setup_server()
            self.log("✅ Сервер успешно подготовлен.\n")
            self.log("Этап 2: Развертывание Docker контейнеров...")
            self._deploy_docker()
            self.log("✅ Docker контейнеры успешно развернуты.\n")
            self.log("Этап 3: Применение сетевых правил...")
            self._apply_network_rules()
            self.log("✅ Сетевые правила успешно применены.\n")
            self.log("🎉 --- УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА --- 🎉")
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


# --- Главное приложение (UI) ---
class InstallerApp:
    # ... __init__ и другие методы без изменений до _installation_thread_entrypoint ...
    def __init__(self, page: ft.Page):
        self.page = page
        page.title = "Установщик Amnezia Portmaster"

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
            on_click=lambda _: self.key_picker.pick_files(dialog_title="Выберите приватный ключ",allow_multiple=False),
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(vertical=15, horizontal=15),
            )
        )
        self.log_output_column = ft.Column(spacing=5, expand=True, scroll=ft.ScrollMode.ADAPTIVE)
        self.install_btn = ft.ElevatedButton("Установить", icon=ft.Icons.ROCKET_LAUNCH, on_click=self._on_install,
                                             style=ft.ButtonStyle(padding=ft.padding.symmetric(vertical=20, horizontal=20),
            ))
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
        for ctl in (self.install_btn, self.host, self.port, self.user, self.password, self.pick_btn, self.key_path,
                    self.key_password):
            ctl.disabled = lock
        self.progress.visible = lock
        self.copy_log_btn.disabled = lock
        self.page.update()

    def _validate_inputs(self) -> bool:
        if not self.host.value or not self.port.value.isdigit() or not self.user.value:
            self._log("❌ Ошибка: Заполните поля Host/IP, Port и User.")
            return False
        if not self.key_path.value and not self.password.value:
            self._log("❌ Ошибка: Укажите пароль пользователя или выберите SSH ключ.")
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

            # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ ---
            # Передаем пароль из UI в сервис
            user_data = {
                'user': self.user.value.strip(),
                'password': self.password.value
            }

            service = InstallationService(
                client=client, user_data=user_data,
                log_callback=self._log
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
                            # Контейнер для кнопки установки в самом низу
                            ft.Container(
                                content=ft.Row([self.install_btn, self.progress]),
                                padding=ft.padding.all(10),
                                alignment=ft.alignment.bottom_right
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