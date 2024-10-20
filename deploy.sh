#!/bin/bash

# Defining script directory
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "$SCRIPT_DIR/deploy_config.sh"


backup_permissions() {
    local CONTAINER_NAME="$1"
    local SCRIPT_DIR="$2"
    local PERMISSIONS_FILE="${SCRIPT_DIR}/permissions_backup.txt"
    
    # Clear or create a file to store permissions
    > "$PERMISSIONS_FILE"
    
    echo "Creating permissions backup for container $CONTAINER_NAME in $PERMISSIONS_FILE"

    # Command to collect permissions inside the container
    docker exec "$CONTAINER_NAME" find /opt -exec stat -c "%a %n" {} \; > "$PERMISSIONS_FILE"
    
    # Check if the file was successfully created
    if [ $? -eq 0 ]; then
        echo "Permissions for directories and files in /opt of container $CONTAINER_NAME were successfully saved to $PERMISSIONS_FILE"
    else
        echo "Error creating permissions backup!"
    fi
}

restore_permissions() {
    local CONTAINER_NAME="$1"
    local SCRIPT_DIR="$2"
    local PERMISSIONS_FILE="${SCRIPT_DIR}/permissions_backup.txt"

    # Check if the permissions file exists
    if [ ! -f "$PERMISSIONS_FILE" ]; then
        echo "File $PERMISSIONS_FILE not found!"
        return 1
    fi

    echo "Restoring permissions in container $CONTAINER_NAME from file $PERMISSIONS_FILE"

    # Loop through the lines of the file and restore permissions inside the container
    while read -r line; do
        PERMISSIONS=$(echo "$line" | awk '{print $1}')
        FILE=$(echo "$line" | awk '{print $2}')
        
        # Check if the file or directory exists in the container
        if docker exec "$CONTAINER_NAME" test -e "$FILE"; then
            docker exec "$CONTAINER_NAME" chmod "$PERMISSIONS" "$FILE"
            echo "Restored permissions $PERMISSIONS for $FILE in the container"
        else
            echo "File or directory $FILE not found in the container, skipping..."
        fi
    done < "$PERMISSIONS_FILE"

    echo "Permissions restoration completed."
}

# Creating payload.tar
cd "$SCRIPT_DIR"
tar -cf "$SCRIPT_DIR/payload.tar" amnezia portmaster
echo "payload.tar created in $SCRIPT_DIR"

# Backup of the /opt/amnezia directory in the container
docker exec "$CONTAINER_NAME" chmod +w /tmp
docker exec "$CONTAINER_NAME" tar -cf "/tmp/$CONTAINER_NAME.tar" -C /opt amnezia
docker cp "$CONTAINER_NAME":/tmp/$CONTAINER_NAME.tar "$SCRIPT_DIR/$CONTAINER_NAME.tar"
docker exec "$CONTAINER_NAME" chmod -w /tmp
echo "The /opt/amnezia directory in the container is archived in $SCRIPT_DIR/$CONTAINER_NAME.tar"

# Back up permissions for the container's /opt directory to a file
backup_permissions "$CONTAINER_NAME" "$SCRIPT_DIR"

# Stop and rename the container
CONTAINER_COUNT=$(docker ps -a | wc -l)
echo "Stopping container: $CONTAINER_NAME"
docker stop "$CONTAINER_NAME"
# sleep 30 # time to think
WAIT_TIME=0
TIMEOUT=10  # Timeout in seconds

while [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME")" == "true" ]]; do
  if [[ $WAIT_TIME -ge $TIMEOUT ]]; then
    echo "Container $CONTAINER_NAME did not stop within $TIMEOUT seconds. Exiting script."
    exit 1
  fi
  echo "Waiting for container $CONTAINER_NAME to stop... ($WAIT_TIME seconds)"
  sleep 1
  ((WAIT_TIME++))
done

echo "Container $CONTAINER_NAME successfully stopped."
docker rename "$CONTAINER_NAME" "${CONTAINER_NAME}_bak_${CONTAINER_COUNT}"
echo "Container stopped and renamed to ${CONTAINER_NAME}_bak_${CONTAINER_COUNT}"

# For the Python script to parse the string and understand the name of the backed-up container
echo "BACKUP CONTAINER NAME: ${CONTAINER_NAME}_bak_${CONTAINER_COUNT}"

# Start a new container
"$SCRIPT_DIR/create_container.sh" > /dev/null
echo "Script create_container.sh launched. Waiting for container to start..."

# Wait for the container to start with a timeout of 30 seconds
TIMEOUT=30  # Timeout in seconds
WAIT_TIME=0

while [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME")" != "true" ]]; do
  if [[ $WAIT_TIME -ge $TIMEOUT ]]; then
    echo "Container $CONTAINER_NAME did not start within $TIMEOUT seconds. Exiting script."
    exit 1
  fi
  echo "Waiting for container $CONTAINER_NAME to start... ($WAIT_TIME seconds)"
  sleep 1
  ((WAIT_TIME++))
done

echo "Container $CONTAINER_NAME started."

# Transfer and extract the archive to the new container
docker exec "$CONTAINER_NAME" chmod +w /tmp
docker cp "$SCRIPT_DIR/$CONTAINER_NAME.tar" "$CONTAINER_NAME":/tmp
# docker exec "$CONTAINER_NAME" ls -l /tmp
# sleep 10
docker exec "$CONTAINER_NAME" chmod -R +w /opt # don't forget to restore write permissions from the file later
docker exec "$CONTAINER_NAME" tar -xvf /tmp/$CONTAINER_NAME.tar -C /opt/

# docker exec "$CONTAINER_NAME" ls -l /opt/openvpn/clients
# sleep 10
echo "Archive transferred to the new container and extracted."

# Delete temporary archives
docker exec "$CONTAINER_NAME" rm /tmp/$CONTAINER_NAME.tar
echo "Temporary archives deleted."

# Install sudo and Python 3 in the container
docker exec "$CONTAINER_NAME" apk update
docker exec "$CONTAINER_NAME" apk add sudo python3
echo "sudo and Python 3 installed in the container."

# Save file permissions
SUDOERS_PERMISSIONS=$(docker exec "$CONTAINER_NAME" stat -c '%a' /etc/sudoers)
echo "File permissions saved."

# Set write permissions for files
docker exec "$CONTAINER_NAME" chmod +w /etc/sudoers # don't forget to remove permissions later!
echo "Write permissions to sudoers set."

# 10. Copy and unpack payload.tar
docker cp "$SCRIPT_DIR/payload.tar" "$CONTAINER_NAME":/tmp
# docker exec "$CONTAINER_NAME" ls -l /tmp/payload.tar

docker exec "$CONTAINER_NAME" tar -xvf /tmp/payload.tar -C /opt/
# docker exec "$CONTAINER_NAME" ls -l /opt/portmaster/
# sleep 10
docker exec "$CONTAINER_NAME" rm /tmp/payload.tar
docker exec "$CONTAINER_NAME" chmod -w /tmp
echo "payload copied to the container and unpacked."

# 11-14. Create user, configure sudo, directories, and permissions
docker exec "$CONTAINER_NAME" adduser -D -h /nonexistent -s /sbin/nologin portmaster
docker exec "$CONTAINER_NAME" sh -c 'echo "portmaster ALL=(ALL) NOPASSWD: /sbin/iptables" >> /etc/sudoers'
docker exec "$CONTAINER_NAME" mkdir /opt/portmaster/log
docker exec "$CONTAINER_NAME" chown -R portmaster:portmaster /opt/portmaster
docker exec "$CONTAINER_NAME" sh -c 'chmod +x /opt/portmaster/*.sh'
echo "User portmaster created, sudo configured, directories and permissions set."

# Restore permissions
docker exec "$CONTAINER_NAME" chmod "$SUDOERS_PERMISSIONS" /etc/sudoers
docker exec "$CONTAINER_NAME" chmod -R +w /opt/portmaster/log
# Restore permissions for the /opt directory as it was
restore_permissions "$CONTAINER_NAME" "$SCRIPT_DIR"
echo "File permissions restored."
echo "Restarting container(may take a while)..."
# Restart the container
docker restart "$CONTAINER_NAME"
echo "Container $CONTAINER_NAME restarted."