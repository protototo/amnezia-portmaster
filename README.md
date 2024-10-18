# amnezia-portmaster: Dynamic Port Forwarding for Amnezia VPN Containers

This project provides a dynamic port forwarding solution for Amnezia VPN containers, allowing you to easily manage port mappings for your applications running behind the VPN. It leverages `iptables` for efficient port redirection and offers a user-friendly client script for adding, deleting, and testing port forwardings.

**Features:**

* Automated `iptables` rule management.
* Support for both TCP and UDP port forwarding.
* User-friendly client script for managing ports.
* Client-side disconnect script to cleanly remove port forwardings when the VPN disconnects.
* Automated deployment script simplifies setup within the Amnezia container.
* Configurable port ranges.

**Supported Operating Systems:**

* Server (Amnezia Container Host): Linux (tested on Ubuntu, Debian, CentOS - other distributions likely work but are untested).
* Client (where `install.py` and `portmaster-client.sh` are run): Linux, macOS (notifications supported).

**Dependencies:**

* **Server:**
    * Docker (required for Amnezia container)
    * `iptables` (included in most Linux distributions)
    * `bash` (for scripting)
    * `python3` (for the portmaster daemon)
* **Client:**
    * `python3` (for the installation script - `install.py`)
    * `paramiko` (Python SSH library for the installer)
    * `bash` (for the client script - `portmaster-client.sh`)
    * `nc` (netcat, for network communication - usually included by default)
    * `sudo` (potentially, depending on how Docker access is configured)
* **macOS Client (optional, for notifications):** `osascript`

**Python Modules (Client-Side Installation):**

```bash
pip3 install paramiko
```

**Installation:**

There are two installation methods available: automated (using the `install.py` script) and manual.

**Automated Installation (Recommended):**

1. Clone the repository: `git clone https://github.com/yourusername/amnezia-portmaster.git`
2. Navigate to the project directory: `cd amnezia-portmaster`
3. Run the installer: `python3 install.py`
    * The installer will guide you through the setup process, including SSH connection details, container selection, and port range configuration.

## Manual Installation (Amnezia OpenVPN Container)

If you prefer manual installation or encounter issues with the automated installer, follow these steps:

**Prerequisites:**

* A running Amnezia OpenVPN container (default name: `amnezia-openvpn`).
* `docker` installed and configured on your host machine.
* `bash`, `apk` available within the container.
* Basic familiarity with Linux command line and file permissions.

**1. Backup Existing Container and Data:**

* **Backup container image:**
```bash
docker commit amnezia-openvpn amnezia-openvpn-portmaster
```
**2. Stop and Rename the Existing Container:**

```bash
docker stop amnezia-openvpn
docker rename amnezia-openvpn amnezia-openvpn_old
```
**3. Create a New Container with Exposed Ports:**

```bash
docker run -d \
  --name amnezia-openvpn \
  --privileged \
  --cap-add=NET_ADMIN \
  --restart always \
  -p 32125:32125/udp \  # Replace with your OpenVPN port
  -p 40000-40099:40000-40099/tcp \ # Replace with desired exposed port range
  -p 40000-40099:40000-40099/udp \ # Replace with desired exposed port range
  -e PORTMASTER_IP=10.8.0.1 \ # Replace if necessary (see your OpenVPN server.conf)
  -e PORTMASTER_PORT=50000 \
  -e EXPOSED_PORT_RANGE=40000-40099 \ # Replace with desired exposed port range
  --network amnezia-dns-net \ # Replace with your Amnezia network name
  --security-opt label=disable \
  amnezia-openvpn-portmaster dumb-init /opt/amnezia/start.sh
```

**4. Prepare the Container:**

* **Enter the container:**
  ```bash
  docker exec -it amnezia-openvpn bash
  ```

* **Install necessary packages:**
  ```bash
  apk update
  apk add sudo python3
  ```

* **Create the `portmaster` user:**
  ```bash
  adduser -D -h /nonexistent -s /sbin/nologin portmaster
  ```

* **Grant sudo privileges to `portmaster` for `iptables`:**
  ```bash
  chmod +w /etc/sudoers
  echo "portmaster ALL=(ALL) NOPASSWD: /sbin/iptables" >> /etc/sudoers
  chmod -w /etc/sudoers  # Important: Restore original permissions
  ```

* **Create required directories and set ownership:**
  ```bash
  mkdir -p /opt/portmaster/log
  chown -R portmaster:portmaster /opt/portmaster
  ```

* **Exit the container:** `exit`


**5. Copy Files into the Container:**

```bash
docker cp portmaster/portmaster.sh amnezia-openvpn:/opt/portmaster/
docker cp portmaster/ovpn-client-disconnect.sh amnezia-openvpn:/opt/portmaster/
```

**6. Set Permissions within the Container:**

```bash
docker exec -it amnezia-openvpn chmod +x /opt/portmaster/*.sh
docker exec -it amnezia-openvpn chmod -R -w /opt/portmaster
docker exec -it amnezia-openvpn chmod +w /opt/portmaster/log
```

**7. Modify `start.sh` within the Container:**

* **Enter the container:**  `docker exec -it amnezia-openvpn bash`
* **Add the following to `/opt/amnezia/start.sh` (before the `tail -f /dev/null`):**
  ```bash
  sudo -E -u portmaster /opt/portmaster/portmaster.sh &
  ```
* **Add the following to the end of`/opt/amnezia/openvpn/server.conf`:**
  ```
  script-security 2
  client-disconnect /opt/portmaster/ovpn-client-disconnect.sh
  ```



**8. Restart the Container:**

```bash
docker restart amnezia-openvpn-portmaster
```

**9. Client Setup:**  Copy `client/portmaster-client.sh` to your client machine and update `SERVER_IP` and `SERVER_PORT`.

**Usage (Client):**

* **Adding a port:** `./client/portmaster-client.sh --add <port_number>`
* **Deleting a port:** `./client/portmaster-client.sh --delete <port_number>`
* **Disconnecting (clears all forwarded ports):** `./client/portmaster-client.sh --disconnect`
* **Testing port forwarding:** `./client/portmaster-client.sh --test <port_number>`

**License:**

 MIT License

**Contributing:**

Contributions are welcome! Please feel free to submit pull requests.

**Disclaimer:**

This software is provided as-is. Use at your own risk. Ensure you understand the security implications of port forwarding and configure your firewall appropriately.

