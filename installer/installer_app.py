import re
import threading
import flet as ft
import paramiko
import os

# --- КОНФИГУРАЦИЯ ---
GIT_REPO_URL = "https://github.com/protototo/amnezia-portmaster.git"
REMOTE_PROJECT_DIR = "amnezia-portmaster"


# --- Утилита проверки пути ---
def is_path_critically_dangerous(path_str: str) -> bool:
    if not isinstance(path_str, str) or not path_str.strip():
        return True
    path = path_str.strip()
    if path.startswith('/'):
        return True
    if '..' in path.split('/'):
        return True
    if path.startswith('./'):
        return True
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', path):
        return True
    if path in ('.', '..'):
        return True
    return False


# --- Диалог ошибки "обезьяна с гранатой" ---
def show_monkey_with_grenade_dialog(page: ft.Page, dangerous_path: str):
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
                f"Конфигурация REMOTE_PROJECT_DIR = '{dangerous_path}' небезопасна.\n\n"
                "Исправьте константу и перезапустите приложение.",
                size=14,
                text_align=ft.TextAlign.CENTER,
            ),
            padding=20,
        ),
        actions=[
            ft.ElevatedButton(
                "Понял",
                on_click=close_dialog,
                color=ft.Colors.WHITE,
                bgcolor=ft.Colors.RED_700
            )
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.open(dialog)


# --- SSH-клиент ---
class SecureSSHClient:
    def __init__(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self, hostname, port, username, password=None, key_filename=None):
        try:
            if key_filename:
                key_path = os.path.expanduser(key_filename)
                if not os.path.exists(key_path):
                    raise FileNotFoundError(f"SSH-ключ не найден: {key_path}")
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(key_path, password=password)
                except paramiko.ssh_exception.SSHException:
                    pkey = paramiko.RSAKey.from_private_key_file(key_path, password=password)
                self.client.connect(hostname, port, username, pkey=pkey, timeout=10)
            elif password:
                self.client.connect(hostname, port, username, password=password, timeout=10)
            else:
                raise ValueError("Нужно задать password или key_filename.")
        except Exception as e:
            raise ConnectionError(f"Не удалось подключиться к {username}@{hostname}:{port}: {e}")

    def get_os_release_id(self) -> str:
        stdin, stdout, stderr = self.client.exec_command(
            "grep '^ID=' /etc/os-release | cut -d'=' -f2"
        )
        os_id = stdout.read().decode().strip().strip('"')
        if not os_id:
            err = stderr.read().decode().strip()
            raise RuntimeError(f"Не удалось определить OS: {err or 'нет данных'}")
        return os_id

    def execute_command(self, command, log_callback, use_sudo=False, sudo_password=None,
                        working_dir: str | None = None):
        """
        Выполняет команду, опционально используя sudo и/или меняя рабочую директорию.
        """
        # АРХИТЕКТУРНОЕ РЕШЕНИЕ:
        # Если указана рабочая директория, мы объединяем команду cd с основной командой.
        # Это гарантирует, что основная команда выполнится в нужном месте.
        if working_dir:
            # `cd` сначала, потом `&&` чтобы вторая команда выполнилась только если `cd` прошел успешно
            command_to_run = f"cd {working_dir} && {command}"
        else:
            command_to_run = command

        final_command = command_to_run
        if use_sudo:
            if not sudo_password:
                raise ValueError("Для выполнения команды с sudo необходимо предоставить пароль.")
            # Важно: sudo применяется к исходной команде, а не к `cd`
            final_command = f"cd {working_dir} && sudo -S -p '' {command}" if working_dir else f"sudo -S -p '' {command}"

        if use_sudo:
            log_callback(f"$ {final_command.replace(sudo_password or '', '********')}\n")

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

            error_details = f"Команда '{command}' в директории '{working_dir or '~'}' завершилась с кодом {exit_status}."
            if error_output:
                error_details += f"\nSTDERR:\n{error_output}"
            raise ChildProcessError(error_details)

        log_callback(f"УСПЕХ: Команда завершилась с кодом {exit_status}\n")

    def close(self):
        self.client.close()


# --- Главное приложение установки ---
class InstallerApp:
    def __init__(self, page: ft.Page):
        self.page = page
        page.title = "Установщик Amnezia Portmaster"
        page.window_width = 800
        page.window_height = 700

        # Поля ввода
        self.host = ft.TextField(label="Host/IP", width=400)
        self.port = ft.TextField(label="SSH Port", value="22", width=150)
        self.user = ft.TextField(label="User", value="root", width=400)
        self.auth_method = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="password", label="Password"),
                ft.Radio(value="key", label="SSH Key")
            ]),
            value="password",
            on_change=self._on_auth_change
        )
        self.password = ft.TextField(label="Password", password=True, width=400)
        self.key_path = ft.TextField(label="Key Path", read_only=True, width=300)
        self.key_picker = ft.FilePicker(on_result=self._on_key_picked)
        page.overlay.append(self.key_picker)
        self.pick_btn = ft.ElevatedButton("Choose Key", on_click=lambda _:
            self.key_picker.pick_files(dialog_title="Select SSH Key", allowed_extensions=["pem", "key"])
        )
        self.pass_container = ft.Container(content=self.password, visible=True)
        self.key_container = ft.Container(content=ft.Row([self.key_path, self.pick_btn]), visible=False)

        # Лог и кнопки
        self.log_col = ft.Column(expand=True, spacing=5)
        self.install_btn = ft.ElevatedButton("Install", icon=ft.Icons.ROCKET_LAUNCH, on_click=self._on_install)
        self.progress = ft.ProgressRing(visible=False)

        self._build_ui()

    def _on_auth_change(self, e):
        use_pass = self.auth_method.value == "password"
        self.pass_container.visible = use_pass
        self.key_container.visible = not use_pass
        self.page.update()

    def _on_key_picked(self, e):
        if e.files:
            self.key_path.value = e.files[0].path
            self.page.update()

    def _log(self, msg: str):
        text = msg.rstrip("\n")
        if text:
            self.log_col.controls.append(ft.Text(text, font_family="Consolas", size=12))
            self.page.update()

    def _lock_ui(self, lock: bool):
        self.install_btn.disabled = lock
        self.progress.visible = lock
        for ctl in (self.host, self.port, self.user, self.password, self.pick_btn, self.auth_method):
            ctl.disabled = lock
        self.page.update()

    def _validate(self) -> bool:
        if not self.host.value:
            self._log("ERROR: host empty")
            return False
        if not self.port.value.isdigit():
            self._log("ERROR: port must be a number")
            return False
        if self.auth_method.value == "password" and not self.password.value:
            self._log("ERROR: password required")
            return False
        if self.auth_method.value == "key" and not self.key_path.value:
            self._log("ERROR: key file required")
            return False
        return True

    def _on_install(self, e):
        self.log_col.controls.clear()
        if not self._validate():
            return
        self._lock_ui(True)
        threading.Thread(target=self._install_thread, daemon=True).start()

    def _install_thread(self):
        client = SecureSSHClient()
        try:
            self._log(f"Connecting to {self.host.value}:{self.port.value}...")
            client.connect(
                hostname=self.host.value,
                port=int(self.port.value),
                username=self.user.value,
                password=self.password.value if self.auth_method.value=="password" else None,
                key_filename=self.key_path.value if self.auth_method.value=="key" else None
            )
            self._log("Connected!")
            self._setup_server(client)
            self._deploy_docker(client)
            self._apply_network_rules(client)
            self._log("\n--- INSTALL COMPLETE ---")
        except Exception as ex:
            self._log(f"\n--- FATAL ERROR ---\n{type(ex).__name__}: {ex}")
        finally:
            client.close()
            self._lock_ui(False)

    def _setup_server(self, client: SecureSSHClient):
        self._log("Detecting OS...")
        os_id = client.get_os_release_id()
        self._log(f"OS: {os_id}")
        if os_id not in ("ubuntu", "debian"):
            raise NotImplementedError(f"Unsupported OS: {os_id}")
        remote = f"~/{REMOTE_PROJECT_DIR}"
        # клоним и запускаем setup
        cmd = (
            f"rm -rf {remote} && "
            f"git clone {GIT_REPO_URL} {remote} && "
            f"chmod +x {remote}/installer/setup_ubuntu.sh && "
            f"sudo -S {remote}/installer/setup_ubuntu.sh"
        )
        client.execute_command(cmd, self._log, use_sudo=True, sudo_password=self.password.value)

    def _deploy_docker(self, client: SecureSSHClient):
        remote = f"~/{REMOTE_PROJECT_DIR}"
        # одной командой переходим и поднимаем compose
        cmd = f"docker compose up --build -d"
        use_sudo = self.user.value != "root"
        client.execute_command(cmd, self._log, use_sudo=use_sudo, sudo_password=self.password.value if use_sudo else None, working_dir=remote)

    def _apply_network_rules(self, client: SecureSSHClient):
        remote = f"~/{REMOTE_PROJECT_DIR}"
        use_sudo = self.user.value != "root"
        client.execute_command( f"chmod +x {remote}/apply_portmaster_net_rules.sh", self._log, use_sudo=False)
        client.execute_command(f"{remote}/apply_portmaster_net_rules.sh", self._log, use_sudo=use_sudo, sudo_password=self.password.value if use_sudo else None,
                               working_dir=remote)


    def _build_ui(self):
        self.page.add(
            ft.Column([
                ft.Text("1. Connection", size=18, weight=ft.FontWeight.BOLD),
                self.host,
                ft.Row([self.port, self.user]),
                ft.Text("Auth method:"),
                self.auth_method,
                self.pass_container,
                self.key_container,
                ft.Divider(),
                ft.Row([self.install_btn, self.progress]),
                ft.Divider(),
                ft.Column([
                    ft.Text("2. Logs", size=18, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        content=ft.ListView([self.log_col], auto_scroll=True, expand=True),
                        border=ft.border.all(1, ft.Colors.OUTLINE),
                        border_radius=5,
                        padding=10,
                        expand=True
                    )
                ], expand=True),
            ], expand=True, scroll=ft.ScrollMode.ADAPTIVE)
        )
        self.page.update()


def main(page: ft.Page):
    page.title = "Config Check..."
    if is_path_critically_dangerous(REMOTE_PROJECT_DIR):
        show_monkey_with_grenade_dialog(page, REMOTE_PROJECT_DIR)
    else:
        InstallerApp(page)


if __name__ == "__main__":
    ft.app(target=main)
