# installer/installer_app.py

import flet as ft
import paramiko
import os
import time
from pathlib import Path

# --- КОНФИГУРАЦИЯ ---
GIT_REPO_URL = "https://github.com/protototo/amnezia-portmaster.git"
REMOTE_PROJECT_DIR = "amnezia-portmaster"


class SSHClient:
    """Обертка над Paramiko для удобства работы."""

    def __init__(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self, hostname, port, username, password=None, key_filename=None):
        """Подключается к серверу, поддерживая пароль и ключ."""
        if key_filename:
            # Убедимся, что путь к ключу в правильном формате
            key_path = os.path.expanduser(key_filename)
            if not os.path.exists(key_path):
                raise FileNotFoundError(f"Файл ключа не найден: {key_path}")

            # Paramiko может требовать, чтобы права на ключ были ограничены.
            # На Windows это не проверяется.

            private_key = paramiko.RSAKey.from_private_key_file(key_path)
            self.client.connect(hostname, port, username, pkey=private_key)
        elif password:
            self.client.connect(hostname, port, username, password=password)
        else:
            raise ValueError("Необходимо указать пароль или путь к ключу.")

    def execute_command(self, command, log_callback):
        """Выполняет команду и передает вывод в реальном времени."""
        log_callback(f"$ {command}\n")
        stdin, stdout, stderr = self.client.exec_command(command, get_pty=True)

        for line in iter(stdout.readline, ""):
            log_callback(line)

        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            error_output = "".join(stderr.readlines())
            log_callback(f"ОШИБКА: Команда завершилась с кодом {exit_status}\n")
            log_callback(error_output)
            raise Exception(f"Ошибка выполнения команды: {command}")

        log_callback(f"УСПЕХ: Команда завершилась с кодом {exit_status}\n")

    def close(self):
        self.client.close()


def main(page: ft.Page):
    page.title = "Установщик Portmaster Daemon"
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.window_width = 800
    page.window_height = 600

    # --- UI Элементы ---

    # Поля для ввода данных
    host_input = ft.TextField(label="IP-адрес или хост сервера", width=400)
    port_input = ft.TextField(label="Порт SSH", value="22", width=150)
    user_input = ft.TextField(label="Имя пользователя", value="root", width=400)

    # Переключатель типа аутентификации
    auth_method = ft.RadioGroup(content=ft.Row([
        ft.Radio(value="password", label="Пароль"),
        ft.Radio(value="key", label="SSH-ключ"),
    ]), value="password")

    # Поля для пароля и ключа
    password_input = ft.TextField(label="Пароль", password=True, can_reveal_password=True, width=400)

    def pick_key_file(e: ft.FilePickerResultEvent):
        if e.files:
            key_file_path.value = e.files[0].path
            page.update()

    key_picker = ft.FilePicker(on_result=pick_key_file)
    page.overlay.append(key_picker)
    key_file_path = ft.TextField(label="Путь к приватному SSH-ключу", read_only=True, width=300)
    pick_key_button = ft.ElevatedButton("Выбрать файл", on_click=lambda _: key_picker.pick_files(
        dialog_title="Выберите приватный SSH-ключ",
        allowed_extensions=["pem", "key"]  # Можно добавить другие расширения
    ))

    # Контейнеры для динамического отображения полей
    password_container = ft.Container(content=password_input, visible=True)
    key_container = ft.Container(content=ft.Row([key_file_path, pick_key_button]), visible=False)

    def on_auth_method_change(e):
        is_password = auth_method.value == "password"
        password_container.visible = is_password
        key_container.visible = not is_password
        page.update()

    auth_method.on_change = on_auth_method_change

    # Логи и кнопка установки
    log_view = ft.ListView(expand=True, spacing=5, auto_scroll=True)
    install_button = ft.ElevatedButton("Установить", icon=ft.Icons.ROCKET_LAUNCH)
    progress_ring = ft.ProgressRing(visible=False)

    # --- Логика установки ---

    def log_message(message: str):
        """Добавляет сообщение в лог и обновляет страницу."""
        log_view.controls.append(ft.Text(message.strip(), font_family="Consolas", size=12))
        page.update()

    def install_click(e):
        # Блокируем UI на время установки
        install_button.disabled = True
        progress_ring.visible = True
        log_view.controls.clear()
        log_message("Начинаю установку...")
        page.update()

        ssh = SSHClient()
        try:
            # 1. Подключение
            log_message(f"Подключаюсь к {host_input.value}:{port_input.value}...")
            ssh.connect(
                hostname=host_input.value,
                port=int(port_input.value),
                username=user_input.value,
                password=password_input.value if auth_method.value == "password" else None,
                key_filename=key_file_path.value if auth_method.value == "key" else None
            )
            log_message("Успешное подключение!")

            # 2. Подготовка сервера
            log_message("Проверяю необходимые утилиты (git, docker)...")
            ssh.execute_command("command -v git || (apt-get update && apt-get install -y git)", log_message)
            ssh.execute_command(
                "command -v docker || (curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh)",
                log_message)

            log_message("Проверяю docker-compose...")
            ssh.execute_command(
                "command -v docker-compose || (apt-get update && apt-get install -y docker-compose-plugin)",
                log_message)

            # 3. Клонирование репозитория
            log_message(f"Клонирую репозиторий в ~/{REMOTE_PROJECT_DIR}...")
            # Удаляем старую директорию, если она есть, для чистого клонирования
            ssh.execute_command(f"rm -rf {REMOTE_PROJECT_DIR} && git clone {GIT_REPO_URL} {REMOTE_PROJECT_DIR}",
                                log_message)

            # 4. Запуск сервиса
            project_path = f"~/{REMOTE_PROJECT_DIR}"
            log_message(f"Запускаю docker-compose в {project_path}...")
            ssh.execute_command(f"cd {project_path} && docker-compose up --build -d", log_message)

            # 5. Применение сетевых правил
            log_message("Применяю сетевые правила...")
            rules_script_path = f"{project_path}/apply_portmaster_net_rules.sh"
            ssh.execute_command(f"chmod +x {rules_script_path}", log_message)
            ssh.execute_command(f"sudo {rules_script_path}", log_message)

            log_message("\n\n--- УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА! ---")

        except Exception as ex:
            log_message(f"\n\n--- КРИТИЧЕСКАЯ ОШИБКА УСТАНОВКИ ---\n{ex}")
        finally:
            ssh.close()
            # Разблокируем UI
            install_button.disabled = False
            progress_ring.visible = False
            page.update()

    install_button.on_click = install_click

    # --- Сборка интерфейса ---
    page.add(
        ft.Column([
            ft.Text("1. Данные для подключения к серверу", size=18, weight=ft.FontWeight.BOLD),
            host_input,
            ft.Row([port_input, user_input]),
            ft.Text("Метод аутентификации:"),
            auth_method,
            password_container,
            key_container,
            ft.Divider(),
            ft.Row([install_button, progress_ring]),
            ft.Divider(),
            ft.Text("2. Логи установки", size=18, weight=ft.FontWeight.BOLD),
            ft.Container(
                content=log_view,
                border=ft.border.all(1, ft.Colors.OUTLINE),
                border_radius=ft.border_radius.all(5),
                padding=10,
                expand=True,
            )
        ], expand=True)
    )


if __name__ == "__main__":
    ft.app(target=main)