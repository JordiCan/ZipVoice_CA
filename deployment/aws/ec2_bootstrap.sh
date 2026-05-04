#!/usr/bin/env bash

set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-zipvoice-api}"
CONTAINER_NAME="${CONTAINER_NAME:-zipvoice-api}"
HOST_PORT="${HOST_PORT:-8000}"
CONTAINER_PORT="${CONTAINER_PORT:-8000}"

sudo apt update
sudo apt install -y docker.io git
sudo systemctl enable --now docker

sudo docker build -f deployment/Dockerfile -t "${IMAGE_NAME}" .

if sudo docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
    sudo docker rm -f "${CONTAINER_NAME}"
fi

sudo docker run -d \
    --name "${CONTAINER_NAME}" \
    -p "${HOST_PORT}:${CONTAINER_PORT}" \
    "${IMAGE_NAME}"

echo "Container ${CONTAINER_NAME} started on port ${HOST_PORT}"
echo "Health check: curl http://localhost:${HOST_PORT}/health"
echo "Swagger UI:   http://<EC2_PUBLIC_IP>:${HOST_PORT}/docs"
echo "Logs:         sudo docker logs -f ${CONTAINER_NAME}"
