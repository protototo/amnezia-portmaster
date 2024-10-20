import re
import os
import paramiko
from getpass import getpass
import json
import shutil
import sys
import time

WORK_DIR = ""
if getattr(sys, 'frozen', False):  # Check id script is compiled
    WORK_DIR = os.path.dirname(sys.executable)
else:
    WORK_DIR = os.path.dirname(__file__)

# Path to the payload script (by default, next to the main script)
PAYLOAD_SCRIPT_PATH = os.path.join(
    WORK_DIR, "deploy_portmaster.sh")
SUPPORTED_VPN_TYPES = ["Amnezia OpenVPN", "Amnezia Wireguard"]
DEFAULT_CONTAINERS = {
    "amnezia-openvpn": "openvpn",
    "amnezia-wireguard": "wireguard"
}

SUPPORTED_VPN_TYPES = ["Amnezia OpenVPN", "Amnezia Wireguard"]
DEFAULT_CONTAINERS = {
    "amnezia-openvpn": "openvpn",
    "amnezia-wireguard": "wireguard"
}

# Dictionary with file descriptions (English translation provided below)
PAYLOAD_EXPLANATION = {
    "server.conf": "main OpenVPN server configuration",
    "start.sh": "script that starts when the container starts and launches the OpenVPN and portmaster services",
    "portmaster.sh": "portmaster daemon, dynamically adds iptables rules",
    "ovpn-client-disconnect.sh": "script that removes port forwarding rules for disconnected VPN users"
}

DEFAULT_CONTAINER_TYPE = "openvpn"
USERNAME = ""
CLIENT_CONFIG = os.path.join(WORK_DIR, "client", "portmaster.conf")
PAYLOAD_DIR = os.path.join(WORK_DIR, "payload")
BACKUP_CONTAINER_NAME = ""
PORTMASTER_PORT = 50000


def get_user_input(prompt, validation_func=None, default=None, hide_input=False, require_confirmation=False):
    """Prompts the user for data, checks it, and returns the result.

    Args:
        prompt (str): The prompt text for input.
        validation_func (callable, optional): An input validation function.
        default (str, optional): The default value.
        hide_input (bool, optional): Whether to hide the input.
        require_confirmation (bool, optional): Whether to require confirmation. Defaults to False.

    Returns:
        str: The user-entered data after successful validation.
    """

    while True:
        if default:
            input_prompt = f"{prompt} [{default}]: "
        else:
            input_prompt = f"{prompt}: "

        if hide_input:
            user_input = getpass(input_prompt)
        else:
            user_input = input(input_prompt)

        user_input = user_input.strip()

        if not user_input and default is not None:  # Важно: проверяем default на None
            user_input = default

        if validation_func and not validation_func(user_input):
            print("Error: Invalid input. Please try again.")
            continue

        if not hide_input:
            print(f"You entered: {user_input}")

        if require_confirmation:  # Запрашиваем подтверждение, только если require_confirmation=True
            confirm = input("Confirm (Y/n): ").strip().lower()
            if confirm in ('y', ''):
                return user_input
            else:
                print("Input canceled. Please try again.")
        else:  # Если require_confirmation=False, возвращаем ввод без подтверждения
            return user_input


def write_client_conf(server_ip, server_port):
    # Используем '\r\n' для Windows, '\n' для остальных ОС
    newline = '\r\n' if os.name == 'nt' else '\n'
    with open(CLIENT_CONFIG, "w", newline=newline) as f:
        f.write(f"SERVER_IP={server_ip}{newline}")
        f.write(f"SERVER_PORT={server_port}{newline}")
        f.write(f"PORTS=(){newline}")


def validate_ip_address(ip):
    """Checks the validity of the IP address.

    Args:
        ip (str): IP address to check.

    Returns:
        bool: True if the IP address is valid, otherwise False.
    """
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or not 0 <= int(part) <= 255:
            return False
    return True


def get_ssh_connection(host, username, ssh_key=None, password=None):
    """Creates an SSH connection.

    Args:
        host (str): Server IP address.
        username (str): SSH username.
        ssh_key (paramiko.RSAKey, optional): SSH key object.
        password (str, optional): SSH password.

    Returns:
        paramiko.SSHClient: SSH connection object or None if the connection failed to establish.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        if ssh_key:
            ssh.connect(hostname=host, username=username, pkey=ssh_key)
        else:
            ssh.connect(hostname=host, username=username, password=password)
        return ssh
    except paramiko.ssh_exception.AuthenticationException:
        print("Error: Failed to authenticate on the server. Please check your login, password/key, and try again.")
        return None
    except paramiko.ssh_exception.NoValidConnectionsError:
        print(
            f"Error: Could not connect to the server {host}. Check the IP address and server availability.")
        return None


def check_docker_access(ssh):
    """Checks if the user has access to Docker.

    Args:
        ssh (paramiko.SSHClient): SSH connection object.

    Returns:
        bool: True if access is available, otherwise False.
    """
    _, stdout, stderr = ssh.exec_command("docker ps")
    error = stderr.read().decode()
    if "permission denied" in error.lower():
        return False
    return True


def get_os_info(ssh):
    """Identifies the operating system and Linux distribution on the server.

    Args:
        ssh (paramiko.SSHClient): SSH connection object.

    Returns:
        tuple: A tuple of strings (os_name, distribution), where:
               - os_name: "Linux", "Darwin" (macOS), or "Unknown" if the OS could not be determined.
               - distribution: The name of the Linux distribution (e.g., "Ubuntu", "CentOS") 
                              or None if the OS is not Linux.
    """
    _, stdout, _ = ssh.exec_command("uname -s")
    os_name = stdout.read().decode().strip()

    distribution = None
    if os_name == "Linux":
        _, stdout, _ = ssh.exec_command(
            "lsb_release -i -s 2>/dev/null || cat /etc/os-release | grep '^ID=' | cut -d'=' -f2 | tr -d '\"'")
        distribution = stdout.read().decode().strip()

    return os_name, distribution


def print_docker_access_help(os_name, distribution):
    """Prints help on how to grant access to Docker.

    Args:
        os_name (str): Operating system name.
        distribution (str): Linux distribution name (may be None).
    """
    print("Error: The current user does not have permission to access Docker.")
    if os_name == "Linux":
        if distribution:
            print(
                f"You probably need to add the user to the 'docker' group on the {distribution} distribution.")
            print(
                f"Connect to the server via SSH and run the command (as root or using sudo):")
            print(f"  sudo usermod -aG docker {USERNAME}")
        else:
            print(
                "Please refer to the documentation for your Linux distribution to learn how to grant access to Docker.")
    else:
        print(
            "Please refer to the Docker documentation for your operating system.")


def validate_container_index(index_str, containers):
    """Checks the validity of the entered container index.

    Args:
        index_str (str): The string entered by the user, which should represent the container number.
        containers (list): List of available containers.

    Returns:
        bool: True if the index is valid, otherwise False.
    """
    try:
        index = int(index_str)
        return 0 < index <= len(containers)
    except ValueError:
        return False


def get_exposed_ports(ssh, container_name):
    """Gets a list of exposed ports of the Docker container and groups them into ranges.

    Args:
        ssh (paramiko.SSHClient): SSH connection object.
        container_name (str): Container name.

    Returns:
        tuple: A tuple of two lists - `tcp_ports` and `udp_ports`.
               Each list contains strings of the form "port" or "port_range/protocol".
    """

    _, stdout, stderr = ssh.exec_command(f"docker inspect {container_name}")
    inspect_output = stdout.read().decode()
    if stderr.channel.recv_exit_status() != 0:
        print(
            f"Error retrieving container information: {stderr.read().decode()}")
        return [], []

    try:
        container_info = json.loads(inspect_output)[0]
        exposed_ports = container_info.get(
            "Config", {}).get("ExposedPorts", {})
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        print(f"Error parsing container information: {e}")
        return [], []

    tcp_ports = []
    udp_ports = []
    for port_proto in exposed_ports:
        port, proto = port_proto.split("/")
        port = int(port)

        if proto == "tcp":
            ports_list = tcp_ports
        elif proto == "udp":
            ports_list = udp_ports
        else:  # Handle unknown protocols
            print(
                f"Warning: Unknown protocol for port {port}: {proto}. Assuming TCP.")
            ports_list = tcp_ports

        if ports_list and isinstance(ports_list[-1], list) and ports_list[-1][-1] == port - 1:
            ports_list[-1].append(port)
        else:
            ports_list.append([port])

    # Converting port lists to strings
    tcp_ports = [
        f"{p[0]}-{p[-1]}/tcp" if len(p) > 1 else f"{p[0]}/tcp" for p in tcp_ports]
    udp_ports = [
        f"{p[0]}-{p[-1]}/udp" if len(p) > 1 else f"{p[0]}/udp" for p in udp_ports]

    return tcp_ports, udp_ports


def validate_port_range(port_range_str, used_ports):
    """Validates the port range.

    Args:
        port_range_str (str): Port range string in the format "start-end".
        used_ports (set): Set of already used ports.

    Returns:
        bool: True if the range is valid, otherwise False.
    """
    try:
        start, end = map(int, port_range_str.split('-'))
        if start <= 1024 or end > 65535 or start >= end:
            print("Error: The start of the range must be greater than 1024, the end must be no greater than 65535, "
                  "and the start must be less than the end.")
            return False
        for port in range(start, end + 1):
            if port in used_ports:
                print(f"Error: Port {port} is already in use.")
                return False
        return True
    except ValueError:
        print(
            "Error: Incorrect range format. Enter in the format 'start-end'.")
        return False


def get_mounted_volumes(ssh_client, container_name):
    """
    Retrieves the mounted volumes of a Docker container via SSH using docker inspect.

    Args:
        ssh_client: A paramiko SSHClient object connected to the remote host.
        container_name: The name of the Docker container.

    Returns:
        A list of dictionaries, where each dictionary represents a mount.
        Returns an empty list if there are no mounts or an error occurs.
    """

    try:
        _, stdout, stderr = ssh_client.exec_command(
            f"docker inspect {container_name}")
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            print(f"Error inspecting container: {stderr.read().decode()}")
            return []  # Return empty list on error

        inspect_output = stdout.read().decode()
        container_info = json.loads(inspect_output)

        # Handle cases where docker inspect returns an empty list (container not found)
        if not container_info:
            print(f"Container '{container_name}' not found.")
            return []

        # Get Mounts or empty list if not present
        mounts = container_info[0].get("Mounts", [])
        return mounts

    except (paramiko.SSHException, json.JSONDecodeError) as e:
        print(f"An error occurred: {e}")
        return []  # Return an empty list in case of exception


def prepare_payload_wg(ssh, container_name, port_range, tcp_ports, udp_ports):
    """Downloads files from the container, modifies them, and saves them in PAYLOAD_DIR.

    Args:
        ssh (paramiko.SSHClient): SSH connection object.
        container_name (str): Container name.
        port_range (str): Port range for portmaster.
        tcp_ports (list): List of container TCP ports.
        udp_ports (list): List of container UDP ports. 
    """
    AMNEZIA_DIR = os.path.join(PAYLOAD_DIR, "amnezia")
    WG_DIR = os.path.join(AMNEZIA_DIR, "wireguard")
    PORTMASTER_DIR = os.path.join(PAYLOAD_DIR, "portmaster")

    # Clear the directory
    if os.path.exists(PAYLOAD_DIR) and os.path.isdir(PAYLOAD_DIR):
        shutil.rmtree(PAYLOAD_DIR)

    os.makedirs(WG_DIR, exist_ok=True)
    os.makedirs(PORTMASTER_DIR, exist_ok=True)

    # 1. Downloading files
    for remote_path in ["/opt/amnezia/start.sh", "/opt/amnezia/wireguard/wg0.conf"]:
        # Download the file from the container to the host in a temporary directory
        temp_host_path = f"/tmp/{os.path.basename(remote_path)}"
        ssh.exec_command(
            f"docker cp {container_name}:{remote_path} {temp_host_path}")
        ssh.exec_command(
            f"chmod +r {temp_host_path}")
        local_path = ""
        if "wg0.conf" in remote_path:
            local_path = os.path.join(
                WG_DIR, os.path.basename(remote_path))
        else:
            local_path = os.path.join(
                AMNEZIA_DIR, os.path.basename(remote_path))
        try:
            sftp = ssh.open_sftp()
            sftp.get(temp_host_path, local_path)
            sftp.close()
        except Exception as e:
            print(f"Error downloading file {temp_host_path}: {e}")
            return

    # 2. Modify server.conf
    # server_conf_path = os.path.join(WG_DIR, "wg0.conf")
    # with open(server_conf_path, "r+") as f:
    #    server_conf_content = f.read()
    #    if "PostDown" not in server_conf_content:
    #        f.write("\nPostDown = /opt/amnezia/portmaster/wg-client-disconnect.sh %i\n")
    # 3. Modify start.sh
    start_sh_path = os.path.join(AMNEZIA_DIR, "start.sh")
    with open(start_sh_path, "r+", newline='\n') as f:
        lines = f.readlines()
        for i in range(len(lines) - 2, -1, -1):
            if lines[i].strip():
                lines.insert(
                    i + 1, "sudo -E -u portmaster /opt/portmaster/portmaster.sh &\n")
                break
        f.seek(0)
        f.writelines(lines)

    # 4. Determine the IP of the tun0 interface
    try:
        _, stdout, _ = ssh.exec_command(
            f"docker exec {container_name} ip addr show wg0 | grep 'inet ' | awk '{{print $2}}' | cut -d'/' -f1")
        portmaster_ip = stdout.read().decode().strip()
        if not portmaster_ip:
            print("Error: Could not determine the IP address of the wg0 interface.")
            exit
    except Exception as e:
        print(f"Error determining the IP address of tun0: {e}")
        exit

    write_client_conf(portmaster_ip, PORTMASTER_PORT)

    create_container_script = os.path.join(
        PAYLOAD_DIR, "create_container.sh")
    create_start_container_script(
        ssh, create_container_script, container_name, port_range, portmaster_ip, PORTMASTER_PORT)

    # 7. Create config for the bash script
    config_path = os.path.join(PAYLOAD_DIR, "deploy_config.sh")
    with open(config_path, "w", newline='\n') as f:
        f.write("#!/bin/bash\n\n")
        f.write(f"CONTAINER_NAME={container_name}\n")
        f.close
    # 8. Copy portmaster.sh to PORTMASTER_DIR
    try:
        my_script = os.path.join(WORK_DIR, "portmaster", "portmaster.sh")
        shutil.copy(my_script, PORTMASTER_DIR)
    except FileNotFoundError:
        print(
            "Error: File portmaster.sh not found in the current directory.")
        exit

    # 9. Copy client-disconnect to PORTMASTER_DIR
    try:
        my_script = os.path.join(
            WORK_DIR, "portmaster", "wg-client-disconnect.sh")
        shutil.copy(my_script, PORTMASTER_DIR)
    except FileNotFoundError:
        print(
            "Error: File wg-client-disconnect.sh not found in the current directory.")
        exit

    # 10. Copy deploy.sh to PORTMASTER_DIR
    try:
        my_script = os.path.join(WORK_DIR, "deploy.sh")
        shutil.copy(my_script, PAYLOAD_DIR)
    except FileNotFoundError:
        print(
            "Error: File deploy.sh not found in the current directory.")
        exit


def create_start_container_script(ssh, script_path, container_name, port_range, portmaster_ip, portmaster_port):
    # Получаем информацию о контейнере
    inspect_cmd = f"docker inspect {container_name}"
    stdin, stdout, stderr = ssh.exec_command(inspect_cmd)
    container_info = json.loads(stdout.read().decode())[0]

    # Начинаем составление команды
    run_args = [f"docker run -d --name {container_name}"]

    # Получаем уже проброшенные порты
    existing_ports = container_info.get(
        "HostConfig", {}).get("PortBindings", {})
    for port_proto, bindings in existing_ports.items():
        for binding in bindings:
            host_port = binding["HostPort"]
            run_args.append(f"-p {host_port}:{port_proto}")

    # Обработка диапазона портов
    run_args.append(f"-p {port_range}:{port_range}/udp")
    run_args.append(f"-p {port_range}:{port_range}/tcp")

    # Добавляем Volume
    volumes = container_info.get("Mounts", [])
    for volume in volumes:
        source = volume["Source"]
        destination = volume["Destination"]
        run_args.append(f"-v {source}:{destination}")

    # 4. Добавляем переменные окружения
    env_vars = container_info.get("Config", {}).get("Env", [])
    for env in env_vars:
        run_args.append(f"-e {env}")
    run_args.append(f"-e PORTMASTER_IP={portmaster_ip}")
    run_args.append(f"-e PORTMASTER_PORT={portmaster_port}")
    run_args.append(f"-e EXPOSED_PORT_RANGE={port_range}")
    # 5. Добавляем сеть
    network_mode = container_info["HostConfig"].get("NetworkMode", "default")
    if network_mode:
        run_args.append(f"--network {network_mode}")

    # 6. Добавляем рестарт политику
    restart_policy = container_info["HostConfig"].get(
        "RestartPolicy", {}).get("Name", "")
    if restart_policy:
        run_args.append(f"--restart {restart_policy}")

    # Добавляем устройства (если есть)
    devices = container_info["HostConfig"].get("Devices", [])
    for device in devices:
        path_on_host = device["PathOnHost"]
        path_in_container = device["PathInContainer"]
        run_args.append(f"--device {path_on_host}:{path_in_container}")

    # Добавляем капабилити
    capabilities = container_info["HostConfig"].get("CapAdd", [])
    for cap in capabilities:
        run_args.append(f"--cap-add {cap}")

    networks = list(container_info["NetworkSettings"]["Networks"].keys())

    # Привилегированный режим
    privileged = container_info["HostConfig"].get("Privileged", False)
    if privileged:
        run_args.append("--privileged")

    # Образ контейнера
    image = container_info["Config"]["Image"]
    run_args.append(f"{image} dumb-init /opt/amnezia/start.sh")

    # Сохраняем команду в скрипт
    with open(script_path, "w", newline='\n') as f:
        f.write("#!/bin/bash\n")
        # Записываем все аргументы, кроме последнего, с переносом строки
        for arg in run_args[:-1]:
            f.write(f"{arg}  \\\n")
        # Записываем последний аргумент без переноса строки
        f.write(f"{run_args[-1]}\n")
        for network in networks:
            if network != "bridge":
                f.write(f"docker network connect {network} {container_name}\n")
    print(f"Скрипт сохранен: {script_path}")


def prepare_payload_openvpn(ssh, container_name, port_range, tcp_ports, udp_ports):
    """Downloads files from the container, modifies them, and saves them in PAYLOAD_DIR.

    Args:
        ssh (paramiko.SSHClient): SSH connection object.
        container_name (str): Container name.
        port_range (str): Port range for portmaster.
        tcp_ports (list): List of container TCP ports.
        udp_ports (list): List of container UDP ports. 
    """
    AMNEZIA_DIR = os.path.join(PAYLOAD_DIR, "amnezia")
    OPENVPN_DIR = os.path.join(AMNEZIA_DIR, "openvpn")
    PORTMASTER_DIR = os.path.join(PAYLOAD_DIR, "portmaster")

    # Clear the directory
    if os.path.exists(PAYLOAD_DIR) and os.path.isdir(PAYLOAD_DIR):
        shutil.rmtree(PAYLOAD_DIR)

    os.makedirs(OPENVPN_DIR, exist_ok=True)
    os.makedirs(PORTMASTER_DIR, exist_ok=True)

    # 1. Downloading files
    for remote_path in ["/opt/amnezia/start.sh", "/opt/amnezia/openvpn/server.conf"]:
        # Download the file from the container to the host in a temporary directory
        temp_host_path = f"/tmp/{os.path.basename(remote_path)}"
        ssh.exec_command(
            f"docker cp {container_name}:{remote_path} {temp_host_path}")
        ssh.exec_command(
            f"chmod +r {temp_host_path}")
        local_path = ""
        if "server.conf" in remote_path:
            local_path = os.path.join(
                OPENVPN_DIR, os.path.basename(remote_path))
        else:
            local_path = os.path.join(
                AMNEZIA_DIR, os.path.basename(remote_path))
        try:
            sftp = ssh.open_sftp()
            sftp.get(temp_host_path, local_path)
            sftp.close()
        except Exception as e:
            print(f"Error downloading file {temp_host_path}: {e}")
            return

    # 2. Modify server.conf
    server_conf_path = os.path.join(OPENVPN_DIR, "server.conf")
    with open(server_conf_path, "r+", newline='\n') as f:
        server_conf_content = f.read()
        if "client-disconnect" not in server_conf_content:
            f.write("\nscript-security 2\n")
            f.write(
                "client-disconnect /opt/portmaster/ovpn-client-disconnect.sh\n")

    # 3. Modify start.sh
    start_sh_path = os.path.join(AMNEZIA_DIR, "start.sh")
    with open(start_sh_path, "r+", newline='\n') as f:
        lines = f.readlines()
        for i in range(len(lines) - 2, -1, -1):
            if lines[i].strip():
                lines.insert(
                    i + 1, "sudo -E -u portmaster /opt/portmaster/portmaster.sh &\n")
                break
        f.seek(0)
        f.writelines(lines)

    # 4. Determine the IP of the tun0 interface
    try:
        _, stdout, _ = ssh.exec_command(
            f"docker exec {container_name} ip addr show tun0 | grep 'inet ' | awk '{{print $2}}' | cut -d'/' -f1")
        portmaster_ip = stdout.read().decode().strip()
        if not portmaster_ip:
            print("Error: Could not determine the IP address of the tun0 interface.")
            exit
    except Exception as e:
        print(f"Error determining the IP address of tun0: {e}")
        exit

    write_client_conf(portmaster_ip, PORTMASTER_PORT)

    # Create create_container.sh
    create_container_script = os.path.join(
        PAYLOAD_DIR, "create_container.sh")
    create_start_container_script(
        ssh, create_container_script, container_name, port_range, portmaster_ip, PORTMASTER_PORT)

    # 7. Create config for the bash script
    config_path = os.path.join(PAYLOAD_DIR, "deploy_config.sh")
    with open(config_path, "w", newline='\n') as f:
        f.write("#!/bin/bash\n\n")
        f.write(f"CONTAINER_NAME={container_name}\n")
        f.close
    # 8. Copy portmaster.sh to PORTMASTER_DIR
    try:
        my_script = os.path.join(WORK_DIR, "portmaster", "portmaster.sh")
        shutil.copy(my_script, PORTMASTER_DIR)
    except FileNotFoundError:
        print(
            "Error: File portmaster.sh not found in the current directory.")
        exit

    # 9. Copy ovpn-client-disconnect.sh to PORTMASTER_DIR
    try:
        my_script = os.path.join(
            WORK_DIR, "portmaster", "ovpn-client-disconnect.sh")
        shutil.copy(my_script, PORTMASTER_DIR)
    except FileNotFoundError:
        print(
            "Error: File ovpn-client-disconnect.sh not found in the current directory.")
        exit

    # 10. Copy deploy.sh to PORTMASTER_DIR
    try:
        my_script = os.path.join(WORK_DIR, "deploy.sh")
        shutil.copy(my_script, PAYLOAD_DIR)
    except FileNotFoundError:
        print(
            "Error: File deploy.sh not found in the current directory.")
        exit


def print_directory_structure(dir_path, indent=""):
    """Prints the hierarchical directory structure with pseudo-graphics."""
    for item in os.listdir(dir_path):
        if item.startswith("."):  # Ignore files and directories starting with '.'
            continue
        item_path = os.path.join(dir_path, item)
        if os.path.isdir(item_path):
            print(f"{indent}+-- {item}/")
            print_directory_structure(item_path, indent + "    ")
        else:
            explanation = PAYLOAD_EXPLANATION.get(item)
            if explanation:
                print(f"{indent}+-- {item} <-- {explanation}")
            else:
                print(f"{indent}+-- {item}")


def upload_payload(ssh, container_type, payload_dir):
    """Copies the contents of payload_dir to the server, including nested directories.

    Args:
        ssh (paramiko.SSHClient): SSH connection object.
        container_name (str): Container name.
        container_type (str): Container type (openvpn, wireguard).
        payload_dir (str): Path to the directory with the payload.

    Returns:
        str: The absolute path to the deploy.sh script on the server or None if an error occurred.
    """

    payload_dir_name = f"payload-{container_type}"
    remote_payload_dir = f"./{payload_dir_name}"

    # 1. Creating a directory on the server
    try:
        with ssh.open_sftp() as sftp:
            remote_payload_dir = sftp.normalize(remote_payload_dir)
            try:
                if remote_payload_dir != "/":
                    ssh.exec_command(
                        f"rm -rf {payload_dir_name}")  # Delete a possible tail
                else:
                    print("Trying to delete the root? Hell no...")
                    exit
            except:
                pass
            sftp.mkdir(remote_payload_dir)
            print(f"Directory {remote_payload_dir} created.")
    except Exception as e:
        print(f"Error creating directory on the server: {e}")
        return None

    # 2. Recursively copy files and directories
    def recursive_upload(sftp, local_dir, remote_dir):
        for item in os.listdir(local_dir):
            local_path = os.path.join(local_dir, item)
            remote_path = os.path.join(remote_dir, item).replace('\\', '/')
            if os.path.isfile(local_path):
                sftp.put(local_path, remote_path)
            elif os.path.isdir(local_path):
                try:
                    sftp.mkdir(remote_path)
                except IOError:
                    pass  # The directory may already exist
                recursive_upload(sftp, local_path, remote_path)
    try:
        with ssh.open_sftp() as sftp:
            recursive_upload(sftp, payload_dir, remote_payload_dir)
    except Exception as e:
        print(f"Error copying files to the server: {e}")
        return None

    # 3. Set execute permissions for scripts
    try:
        ssh.exec_command(f"chmod +x {remote_payload_dir}/*.sh")
    except Exception as e:
        print(f"Error setting execute permissions for scripts: {e}")
        return None

    # 4. Return the path to deploy.sh on the server
    deploy_script_path = f"{remote_payload_dir}/deploy.sh"
    return deploy_script_path


def execute_remote_script(ssh, script_path):
    """Runs the script on the server and displays its output in real time.

    Args:
        ssh (paramiko.SSHClient): SSH connection object.
        script_path (str): Path to the script on the server.
        container_name (str): Name of the source container.

    Returns:
        str: The name of the backup container or None if it could not be determined.
    """
    bcp_cont_name = ""
    try:
        transport = ssh.get_transport()
        channel = transport.open_session()
        channel.exec_command(f"bash {script_path}")

        while True:
            if channel.recv_ready():
                output = channel.recv(1024).decode()
                print(output, end="")

                # Search for the string with the backup container name
                if "BACKUP CONTAINER NAME:" in output:
                    bcp_cont_name = output.split(
                        "BACKUP CONTAINER NAME: ")[-1].strip()
                    print(
                        f"Container backed up under the name: {bcp_cont_name}")

            if channel.exit_status_ready():
                exit_code = channel.recv_exit_status()
                if exit_code == 0:
                    print(f"\nScript {script_path} completed successfully.")
                else:
                    print(
                        f"\nError executing script {script_path}. Exit code: {exit_code}")
                break

    except Exception as e:
        print(f"Error executing script: {e}")
    return bcp_cont_name


def check_container_exists(ssh, container_name):
    try:
        # Команда для поиска контейнера по имени (включая остановленные)
        command = f"docker ps -a --filter name={container_name} --format '{{{{.Names}}}}'"

        # Выполнение команды через SSH
        stdin, stdout, stderr = ssh.exec_command(command)

        # Чтение вывода
        output = stdout.read().decode().strip()

        # Если контейнер с таким именем существует, он будет в выводе
        if output == container_name:
            return True
        else:
            return False
    except Exception as e:
        print(f"Error checking container: {e}")
        return False

def main():
    """Main function of the script."""

    print("The installer script will install portmaster port forwarder into AmneziaVPN container")
    print("Currently Amnezia OpenVPN and Amnezia WireGuard are supported")
    print("Please proceed with SSH connecton info")
    print("\n")
    while True:
        # 1. Request SSH connection details
        host = get_user_input(
            "Enter the server IP address", validation_func=validate_ip_address)
        USERNAME = os.getlogin()
        USERNAME = get_user_input(
            "Enter the SSH username", default=USERNAME, require_confirmation=True)

        auth_method = get_user_input("Select authorization method (1 - by key, 2 - by password)",
                                     lambda x: x in ['1', '2'],
                                     default='1')

        if auth_method == '1':
            while True:
                key_path = get_user_input(
                    f"Enter the path to the SSH key file", default=os.path.expanduser("~/.ssh/id_rsa"))
                key_password = get_user_input("Enter the password for the key (leave blank if there is no password)",
                                              hide_input=True, default="")
                try:
                    ssh_key = paramiko.RSAKey.from_private_key_file(
                        key_path, password=key_password)
                    password = None
                    break
                except FileNotFoundError:
                    print(f"Error: Key file not found: {key_path}")
                except paramiko.ssh_exception.PasswordRequiredException:
                    print("Error: This key requires a password.")
                except paramiko.ssh_exception.SSHException as e:
                    print(f"Error loading key: {e}")
        else:
            password = get_user_input(
                "Enter the SSH password", hide_input=True)
            ssh_key = None

        # 2. Establish SSH connection
        ssh = get_ssh_connection(host, USERNAME, ssh_key, password)

        if ssh:  # Check if the connection is established
            print("Connection established!")
            break  # Exit the loop if the connection is established
        else:
            print("Try entering the connection details again.")
    if ssh:
        # 3. Check Docker permissions
        if not check_docker_access(ssh):
            os_name, distribution = get_os_info(ssh)
            print_docker_access_help(os_name, distribution)
            ssh.close()
            exit(1)  # Exit if Docker access is not available

        # 4. Get the list of containers from the host
        containers_output = ssh.exec_command(
            "docker ps -a --format '{{.Names}}'")[1].read().decode().strip()
        containers = containers_output.split('\n')

        # 5. Display the list of available containers with their types
        print("Available containers:")
        default_container_index = None
        for index, container_name in enumerate(containers):
            container_type = DEFAULT_CONTAINERS.get(container_name)
            if container_type:
                display_name = f"{container_name} ({container_type})"
                if container_type == DEFAULT_CONTAINER_TYPE and default_container_index is None:
                    default_container_index = index + 1
            else:
                display_name = container_name
            print(f"{index + 1}. {display_name}")

        # 6. Prompt the user to choose a container
        selected_container = None
        selected_index_str = get_user_input(
            "Enter the container number",
            validation_func=lambda x: validate_container_index(
                x, containers),  # Pass the validation function
            default=str(
                default_container_index) if default_container_index is not None else None,
            require_confirmation=True
        )
        selected_container = containers[int(selected_index_str) - 1]
        container_type = DEFAULT_CONTAINERS.get(selected_container)
        print(f"Selected container: {selected_container}")
        if container_type:
            print(f"Container type: {container_type}")
        else:
            print(
                f"Container type is not defined! The installer supports containers " + ", ".join(SUPPORTED_VPN_TYPES))
            print(
                f"If you have a different type of container, please refer to the documentation and perform the installation manually.")
            print(
                f"If you just renamed the container, specify its type.")
            print(
                f"If you choose custom, the installer will install the scripts, run portmaster without touching the VPN server configuration.")
            print(
                f"You can continue, the installer will install the scripts, run portmaster, and leave the ")
            selected_install_types = ["openvpn", "wireguard", "custom"]
            # Display the list
            for index, install_type in enumerate(selected_install_types):
                print(f"{index + 1}. {install_type}")
            # Request user selection with validation and default value
            selected_index_str = get_user_input(
                "Select installation type",
                validation_func=lambda x: x.isdigit() and 1 <= int(
                    x) <= len(selected_install_types),  # Validation
                default=3  # Custom by default
            )
            # Get the selected installation type
            selected_type_index = int(selected_index_str) - 1
            container_type = selected_install_types[selected_type_index]
            print(f"Selected container type: {container_type}")

        # 7. Get exposed ports
        tcp_ports, udp_ports = get_exposed_ports(ssh, selected_container)
        print("The following ports are used in the container:")
        print("TCP ports:", tcp_ports)
        print("UDP ports:", udp_ports)

        # 8. Determine the used ports
        used_ports = set()
        for port_str in tcp_ports + udp_ports:
            if '-' in port_str:
                start, end = map(int, port_str.split('/')[0].split('-'))
                used_ports.update(range(start, end + 1))
            else:
                port = int(port_str.split('/')[0])
                used_ports.add(port)

        # 9. Generate a default port range
        default_start = 40000
        while any(port in used_ports for port in range(default_start, default_start + 100)):
            default_start += 1
        default_port_range = f"{default_start}-{default_start + 99}"

        # 10. Request port range from the user
        port_range = get_user_input(
            f"Enter the port range",
            validation_func=lambda x: validate_port_range(
                x, used_ports),
            default=default_port_range
        )

        print(f"Selected port range: {port_range}")

        if container_type == "openvpn":
            prepare_payload_openvpn(
                ssh, selected_container, port_range, tcp_ports, udp_ports)
        elif container_type == "wireguard":
            # Assuming you have this function
            prepare_payload_wg(ssh, selected_container,
                               port_range, tcp_ports, udp_ports)
        else:
            print("Unsupported container type!")
            exit

        print(f"Ready to deploy to server {host}!")
        print("\n")
        print(
            f"Now you have a chance to check the configuration files and scripts before deploying.")
        print(
            f"Deployment will take a couple of minutes. During this time, the connection to the VPN may be interrupted.")
        print(
            f"If something goes wrong, the script will restore the container from the backup.")
        print("\n")
        print(f"Deployment directory: {PAYLOAD_DIR}")
        print_directory_structure(PAYLOAD_DIR)
        print("\n")
        confirmation = get_user_input(
            "Are you ready to continue?", lambda x: x.lower() in ['y', 'n'], default='n')
        if confirmation.lower() == 'y':
            remote_deploy_script = upload_payload(
                ssh, container_type, PAYLOAD_DIR)
            if remote_deploy_script:
                print(f"Installation is in progress on the server.")
                BACKUP_CONTAINER_NAME = execute_remote_script(
                    ssh, remote_deploy_script)
                print(
                    f"Installation complete. Connect to the VPN server.")
                confirmation = get_user_input("Were you able to connect to the VPN server?(y/N)",
                                              lambda x: x.lower() in [
                                                  'y', 'n'],
                                              default='n')
                if confirmation.lower() == "y":
                    print("Excellent! Deleting backups...")
                    ssh.exec_command(f"docker remove {BACKUP_CONTAINER_NAME}")
                    remote_deploy_dir = os.path.dirname(
                        remote_deploy_script)
                    if remote_deploy_dir and remote_deploy_dir != "/":  # Check that the directory is not root
                        _, stderr, _ = ssh.exec_command(
                            f"rm -rf {remote_deploy_dir}")
                        if stderr.read():
                            print(
                                f"Error deleting directory: {stderr.read().decode()}")
                        else:
                            print(
                                f"Directory {remote_deploy_dir} deleted.")
                    if os.path.exists(PAYLOAD_DIR) and os.path.isdir(PAYLOAD_DIR):
                        shutil.rmtree(PAYLOAD_DIR)

                    print(
                        f"Ports {port_range} successfully exposed in {container_name} container")
                    print(
                        f"You can now use portmaster-client.sh -add <port> to forward ports through VPN tunnel to your machine")
                    print(
                        f"You can also edit portmaster.conf to forward several ports for example PORTS=(10090 10091 10092....)")
                    print("Installation complete!")
                    time.sleep(10)
                else:
                    print(
                        "Something went wrong... restoring the container from the backup...")
                    have_backup = check_container_exists(ssh,BACKUP_CONTAINER_NAME)
                    if have_backup:
                        # Stop the container
                        stdin, stdout, stderr = ssh.exec_command(
                            f"docker stop {selected_container}")
                        stdout.channel.recv_exit_status()  # Wait for the container to stop

                        # Remove the container
                        stdin, stdout, stderr = ssh.exec_command(
                            f"docker remove {selected_container}")
                        stdout.channel.recv_exit_status()  # Wait for the container to be removed

                       # Rename the backup container
                        stdin, stdout, stderr = ssh.exec_command(
                            f"docker rename {BACKUP_CONTAINER_NAME} {selected_container}")
                        stdout.channel.recv_exit_status()  # Wait for the container to be renamed

                        # Start the container
                        stdin, stdout, stderr = ssh.exec_command(
                            f"docker start {selected_container}")
                        stdout.channel.recv_exit_status()  # Wait for the container to start
                        print(
                            "Container started from backup. All changes have been reverted...")
                        time.sleep(3)
                    else:
                        print("Script failed before creating backup...")
                        print("Your VPN Server container has not been touched.")
                        time.sleep(3)


if __name__ == "__main__":
    main()
