# installer/deploy.py

import subprocess
import sys
import os
import venv

VENV_DIR = ".venv_installer"


def run_command(command, error_message):
    """Хелпер для выполнения команд и обработки ошибок."""
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ОШИБКА: {error_message}")
        print(f"Детали: {e}")
        sys.exit(1)


def main():
    """Основная функция загрузчика."""
    print("--- Загрузчик деплоя Portmaster ---")

    # 1. Проверяем, есть ли Python 3
    if sys.version_info < (3, 8):
        print("ОШИБКА: Требуется Python 3.8 или новее.")
        sys.exit(1)

    # 2. Создаем виртуальное окружение, если его нет
    if not os.path.exists(VENV_DIR):
        print(f"Создаю виртуальное окружение в '{VENV_DIR}'...")
        venv.create(VENV_DIR, with_pip=True)
    else:
        print("Виртуальное окружение уже существует.")

    # 3. Определяем пути к исполняемым файлам в venv
    if sys.platform == "win32":
        pip_executable = os.path.join(VENV_DIR, "Scripts", "pip.exe")
        python_executable = os.path.join(VENV_DIR, "Scripts", "python.exe")
    else:
        pip_executable = os.path.join(VENV_DIR, "bin", "pip")
        python_executable = os.path.join(VENV_DIR, "bin", "python")

    # 4. Устанавливаем зависимости
    print("Устанавливаю зависимости из requirements.txt...")
    requirements_path = os.path.join(os.path.dirname(__file__), "requirements.txt")
    run_command(
        [pip_executable, "install", "-r", requirements_path],
        "Не удалось установить зависимости."
    )
    print("Зависимости успешно установлены.")

    # 5. Запускаем основное приложение-установщик
    print("Запускаю GUI-установщик...")
    installer_script_path = os.path.join(os.path.dirname(__file__), "installer_app.py")

    # Мы не используем run_command, чтобы видеть вывод Flet в реальном времени
    try:
        subprocess.run([python_executable, installer_script_path])
    except KeyboardInterrupt:
        print("\nУстановка прервана пользователем.")
    except Exception as e:
        print(f"Критическая ошибка при запуске установщика: {e}")


if __name__ == "__main__":
    main()