# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY ./app/portmaster_daemon.py .
RUN apt-get update && apt-get install -y iptables iproute2 && rm -rf /var/lib/apt/lists/*
CMD ["python", "-u", "./portmaster_daemon.py"]