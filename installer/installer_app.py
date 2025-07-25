import re
import threading
import flet as ft
import paramiko
import os
from pathlib import Path

# --- КОНФИГУРАЦИЯ ---
GIT_REPO_URL = "https://github.com/protototo/amnezia-portmaster.git"
REMOTE_PROJECT_DIR = "amnezia-portmaster"


# --- Логика ---
def is_path_critically_dangerous(path_str: str) -> bool:
    """
    Проверяет путь на критические уязвимости (directory traversal, абсолютные пути).
    Возвращает True, если путь опасен, иначе False.
    Это не очистка, это жесткая проверка.
    """
    if not isinstance(path_str, str) or not path_str.strip():
        return True  # Пустой путь опасен

    path = path_str.strip()

    # 1. Запрещаем абсолютные пути
    if path.startswith('/'):
        return True

    # 2. Запрещаем попытки выхода из директории
    if '..' in path.split('/'):
        return True

    # 3. Запрещаем начинать с './' - это избыточно и может запутать
    if path.startswith('./'):
        return True

    # 4. Проверяем на наличие только разрешенных символов. Белый список лучше черного.
    # Разрешены: буквы (a-z, A-Z), цифры (0-9), точка, дефис, подчеркивание.
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', path):
        return True

    # 5. Имена "." и ".." сами по себе тоже запрещены.
    if path in ['.', '..']:
        return True

    return False


def show_monkey_with_grenade_dialog(page: ft.Page, dangerous_path: str):
    """
    Показывает фатальное модальное окно и предлагает только один выход - закрыть приложение.
    """

    def close_dialog(e):
        dialog.open = False
        page.update()
        page.window.destroy()

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Container(
            content=ft.Row([
                ft.Text("🐒💣", size=40),
                ft.Text(" КРИТИЧЕСКАЯ ОШИБКА!", size=20)
            ])
        ),
        content=ft.Container(
            content=ft.Text(
                f"Да ну нахер...\n\n"
                f"Конфигурация указывает на опасный путь для проекта: '{dangerous_path}'.\n\n"
                "Пытаться установить проект в корневую директорию или использовать спецсимволы — это как дать обезьяне гранату. Мы так не работаем.\n\n"
                "Исправь константу REMOTE_PROJECT_DIR в коде и попробуй снова.",
                size=14,
                text_align=ft.TextAlign.CENTER,
            ),
            padding=ft.padding.all(20),
        ),
        actions=[
            ft.ElevatedButton(
                "Понял, исправлюсь",
                on_click=close_dialog,
                color=ft.Colors.WHITE,
                bgcolor=ft.Colors.RED_700
            )
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.open(dialog)


# --- SSHClient остается без изменений, так как он был хорош ---
class SecureSSHClient:
    """
    Более безопасная и чистая обертка над Paramiko.
    Не хранит пароль и предоставляет четкий интерфейс для выполнения команд.
    """

    def __init__(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self, hostname, port, username, password=None, key_filename=None):
        """Подключается к серверу."""
        try:
            if key_filename:
                key_path = os.path.expanduser(key_filename)
                if not os.path.exists(key_path):
                    raise FileNotFoundError(f"Файл ключа не найден: {key_path}")
                try:
                    private_key = paramiko.Ed25519Key.from_private_key_file(key_path, password=password)
                except paramiko.ssh_exception.SSHException:
                    private_key = paramiko.RSAKey.from_private_key_file(key_path, password=password)
                self.client.connect(hostname, port, username, pkey=private_key, timeout=10)
            elif password:
                self.client.connect(hostname, port, username, password=password, timeout=10)
            else:
                raise ValueError("Необходимо указать пароль или путь к ключу.")
        except Exception as e:
            raise ConnectionError(f"Не удалось подключиться к {username}@{hostname}:{port}. Ошибка: {e}")

    def get_os_release_id(self) -> str:
        """Получает ID операционной системы с сервера."""
        stdin, stdout, stderr = self.client.exec_command("cat /etc/os-release | grep '^ID=' | cut -d'=' -f2")
        os_id = stdout.read().decode('utf-8').strip().replace('"', '')
        if not os_id:
            error = stderr.read().decode('utf-8')
            raise RuntimeError(f"Не удалось определить ОС. Ошибка: {error or 'неизвестная ошибка'}")
        return os_id

    def execute_command(self, command, log_callback, use_sudo=False, sudo_password=None):
        """
        Выполняет команду, опционально используя sudo.
        Пароль для sudo передается явно и нигде не сохраняется.
        """
        final_command = command
        if use_sudo:
            if not sudo_password:
                raise ValueError("Для выполнения команды с sudo необходимо предоставить пароль.")
            final_command = f"sudo -S -p '' {command}"

        #log_callback(f"$ {final_command.replace(sudo_password, '********')}\n")
        stdin, stdout, stderr = self.client.exec_command(final_command, get_pty=True)

        if use_sudo:
            stdin.write(sudo_password + '\n')
            stdin.flush()

        for line in iter(stdout.readline, ""):
            log_callback(line)
        exit_status = stdout.channel.recv_exit_status()

        if exit_status != 0:
            error_output = "".join(stderr.readlines())
            if exit_status == 1 and 'incorrect password' in error_output:
                raise PermissionError("Неверный пароль для sudo!")
            error_details = f"Команда '{command}' завершилась с кодом {exit_status}."
            if error_output:
                error_details += f"\nSTDERR:\n{error_output}"
            raise ChildProcessError(error_details)

        log_callback(f"УСПЕХ: Команда завершилась с кодом {exit_status}\n")

    def close(self):
        if self.client:
            self.client.close()


# --- Класс приложения остается без изменений ---
class InstallerApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "Установщик Amnezia Portmaster"
        self.page.vertical_alignment = ft.MainAxisAlignment.START
        self.page.window_width = 800
        self.page.window_height = 700
        self.ssh_client = None
        self.host_input = ft.TextField(label="IP-адрес или хост сервера", width=400)
        self.port_input = ft.TextField(label="Порт SSH", value="22", width=150)
        self.user_input = ft.TextField(label="Имя пользователя", value="root", width=400)
        self.auth_method = ft.RadioGroup(
            content=ft.Row([ft.Radio(value="password", label="Пароль"), ft.Radio(value="key", label="SSH-ключ")]),
            value="password",
            on_change=self._on_auth_method_change
        )
        self.password_input = ft.TextField(label="Пароль", password=True, can_reveal_password=True, width=400)
        self.key_file_path = ft.TextField(label="Путь к приватному SSH-ключу", read_only=True, width=300)
        self.key_picker = ft.FilePicker(on_result=self._on_key_file_picked)
        self.page.overlay.append(self.key_picker)
        self.pick_key_button = ft.ElevatedButton("Выбрать файл", on_click=lambda _: self.key_picker.pick_files(
            dialog_title="Выберите приватный SSH-ключ", allowed_extensions=["pem", "key"]
        ))
        self.password_container = ft.Container(content=self.password_input, visible=True)
        self.key_container = ft.Container(content=ft.Row([self.key_file_path, self.pick_key_button]), visible=False)
        self.log_output_column = ft.Column(spacing=5, expand=True)
        self.log_container = ft.Column([
            ft.Row([
                ft.Text("2. Логи установки", size=18, weight=ft.FontWeight.BOLD),
                ft.IconButton(icon=ft.Icons.COPY, tooltip="Копировать весь лог", on_click=self._copy_log_to_clipboard)
            ]),
            ft.Container(
                content=ft.ListView([self.log_output_column], auto_scroll=True, expand=True),
                border=ft.border.all(1, ft.Colors.OUTLINE), border_radius=ft.border_radius.all(5),
                padding=10, expand=True,
            )
        ], expand=True)
        self.install_button = ft.ElevatedButton("Установить", icon=ft.Icons.ROCKET_LAUNCH, on_click=self._install_click)
        self.progress_ring = ft.ProgressRing(visible=False)
        self._build_layout()

    def _on_auth_method_change(self, e):
        is_password = self.auth_method.value == "password"
        self.password_container.visible = is_password
        self.key_container.visible = not is_password
        self.page.update()

    def _on_key_file_picked(self, e: ft.FilePickerResultEvent):
        if e.files:
            self.key_file_path.value = e.files[0].path
            self.page.update()

    def _copy_log_to_clipboard(self, e):
        full_log = "\n".join([control.value for control in self.log_output_column.controls])
        self.page.set_clipboard(full_log)
        self.page.snack_bar = ft.SnackBar(ft.Text("Лог скопирован!"), duration=2000)
        self.page.snack_bar.open = True
        self.page.update()

    def _set_ui_locked(self, locked: bool):
        self.install_button.disabled = locked
        self.progress_ring.visible = locked
        for field in [self.host_input, self.port_input, self.user_input, self.password_input, self.key_file_path,
                      self.pick_key_button, self.auth_method]:
            field.disabled = locked
        self.page.update()

    def _log(self, message: str):
        cleaned_message = message.strip()
        if cleaned_message:
            self.log_output_column.controls.append(
                ft.Text(cleaned_message, font_family="Consolas", size=12, selectable=True))
            self.page.update()

    def _validate_inputs(self) -> bool:
        if not self.host_input.value:
            self._log("ОШИБКА: IP-адрес или хост не может быть пустым.")
            return False
        if not self.port_input.value.isdigit():
            self._log("ОШИБКА: Порт должен быть числом.")
            return False
        if not self.user_input.value:
            self._log("ОШИБКА: Имя пользователя не может быть пустым.")
            return False
        if self.auth_method.value == 'password' and not self.password_input.value:
            self._log("ОШИБКА: Пароль не может быть пустым при выбранном методе аутентификации.")
            return False
        if self.auth_method.value == 'key' and not self.key_file_path.value:
            self._log("ОШИБКА: Необходимо выбрать файл с SSH-ключом.")
            return False
        return True

    def _install_click(self, e):
        self.log_output_column.controls.clear()
        if not self._validate_inputs():
            self.page.update()
            return
        self._log("Начинаю установку...")
        self._set_ui_locked(True)
        threading.Thread(target=self._run_installation_thread, daemon=True).start()

    def _run_installation_thread(self):
        try:
            self.ssh_client = SecureSSHClient()
            self._log(f"Подключаюсь к {self.host_input.value}:{self.port_input.value}...")
            self.ssh_client.connect(
                hostname=self.host_input.value, port=int(self.port_input.value),
                username=self.user_input.value, password=self.password_input.value,
                key_filename=self.key_file_path.value if self.auth_method.value == "key" else None
            )
            self._log("Успешное подключение!")
            self._perform_server_setup()
            self._run_docker_compose()
            self._apply_network_rules()
            self._log("\n\n--- УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА! ---")
        except Exception as ex:
            self._log(f"\n\n--- КРИТИЧЕСКАЯ ОШИБКА УСТАНОВКИ ---\n{type(ex).__name__}: {ex}")
        finally:
            if self.ssh_client:
                self.ssh_client.close()
            self._set_ui_locked(False)

    def _perform_server_setup(self):
        self._log("Определяю операционную систему...")
        os_id = self.ssh_client.get_os_release_id()
        self._log(f"Обнаружена ОС: {os_id}")
        if os_id not in ["ubuntu", "debian"]:
            raise NotImplementedError(f"Неподдерживаемая ОС: {os_id}. Установка прервана.")
        setup_script_name = "setup_ubuntu.sh"
        self._log(f"Клонирую репозиторий в ~/{REMOTE_PROJECT_DIR}...")
        self.ssh_client.execute_command(
            f"rm -rf  ~/{REMOTE_PROJECT_DIR} && git clone {GIT_REPO_URL}  ~/{REMOTE_PROJECT_DIR}",
            self._log, use_sudo=False
        )
        project_path = f"~/{REMOTE_PROJECT_DIR}"
        setup_script_path = f"{project_path}/installer/{setup_script_name}"
        self._log(f"Подготавливаю сервер с помощью скрипта {setup_script_name}...")
        self.ssh_client.execute_command(f"chmod +x {setup_script_path}", self._log, use_sudo=False)
        self.ssh_client.execute_command(
            setup_script_path, self._log, use_sudo=True, sudo_password=self.password_input.value
        )
        self._log("Подготовка сервера завершена.")

    def _run_docker_compose(self):
        use_sudo = self.user_input.value != "root"
        project_path = f"./{REMOTE_PROJECT_DIR}"
        self._log(f"Запускаю docker compose в {project_path}...")
        self.ssh_client.execute_command(
            f"cd {project_path} && docker compose up --build -d", self._log, use_sudo=use_sudo,
            sudo_password=self.password_input.value if use_sudo else None
        )

    def _apply_network_rules(self):
        project_path = f"./{REMOTE_PROJECT_DIR}"
        rules_script_path = f"{project_path}/apply_portmaster_net_rules.sh"
        self._log("Применяю сетевые правила...")
        self.ssh_client.execute_command(f"chmod +x {rules_script_path}", self._log, use_sudo=False)
        self.ssh_client.execute_command(
            rules_script_path, self._log, use_sudo=True, sudo_password=self.password_input.value
        )

    def _build_layout(self):
        self.page.add(
            ft.Column([
                ft.Text("1. Данные для подключения", size=18, weight=ft.FontWeight.BOLD),
                self.host_input,
                ft.Row([self.port_input, self.user_input]),
                ft.Text("Метод аутентификации:"),
                self.auth_method,
                self.password_container,
                self.key_container,
                ft.Divider(),
                ft.Row([self.install_button, self.progress_ring]),
                ft.Divider(),
                self.log_container
            ], expand=True, scroll=ft.ScrollMode.ADAPTIVE)
        )
        self.page.update()


# --- ОБНОВЛЕННАЯ ТОЧКА ВХОДА ---
def main(page: ft.Page):
    """
    Главная функция. Сначала проводит "проверку на обезьяну",
    и только потом запускает основное приложение.
    """
    page.title = "Проверка конфигурации..."
    page.update()

    # Проверка заранее, но показ — позже
    if is_path_critically_dangerous(REMOTE_PROJECT_DIR):
        show_monkey_with_grenade_dialog(page, REMOTE_PROJECT_DIR)
        return  # Не создаём InstallerApp
    else:
        InstallerApp(page)


if __name__ == "__main__":
    ft.app(target=main)