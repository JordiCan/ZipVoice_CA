#!/usr/bin/env bash

set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-zipvoice-api}"
CONTAINER_NAME="${CONTAINER_NAME:-zipvoice-api}"
HOST_PORT="${HOST_PORT:-8000}"
CONTAINER_PORT="${CONTAINER_PORT:-8000}"
MODEL_VOLUME_NAME="${MODEL_VOLUME_NAME:-zipvoice-model-cache}"
MODEL_DIR_IN_CONTAINER="${MODEL_DIR_IN_CONTAINER:-/app/models/zipvoice_ca_runtime}"
ENV_ARGS=()

for var_name in \
    ZIPVOICE_DEMO_MODE \
    ZIPVOICE_WORKER_TOKEN \
    ZIPVOICE_S3_BUCKET \
    ZIPVOICE_S3_REGION \
    ZIPVOICE_S3_ENDPOINT_URL \
    ZIPVOICE_AWS_PROFILE \
    ZIPVOICE_S3_CHECKPOINT_KEY \
    ZIPVOICE_S3_MODEL_CONFIG_KEY \
    ZIPVOICE_S3_TOKENS_KEY \
    ZIPVOICE_S3_SAMPLE_TEXTS_KEY \
    ZIPVOICE_S3_EXAMPLES_PREFIX \
    ZIPVOICE_S3_EXAMPLE_URL_TTL \
    ZIPVOICE_S3_SAMPLES_PREFIX \
    ZIPVOICE_S3_RESULTS_PREFIX \
    ZIPVOICE_S3_SAMPLES_MANIFEST_KEY \
    ZIPVOICE_S3_CACHED_RESULTS_MANIFEST_KEY \
    ZIPVOICE_JOB_POLL_INTERVAL_SECONDS \
    ZIPVOICE_JOB_RESULT_URL_TTL \
    ZIPVOICE_FRONTEND_DIST_DIR \
    ZIPVOICE_SAMPLE_TEXTS_FILE \
    ZIPVOICE_MAX_TEXT_CHARS \
    ZIPVOICE_MAX_PROMPT_TEXT_CHARS \
    ZIPVOICE_MAX_PROMPT_AUDIO_BYTES \
    ZIPVOICE_MAX_PROMPT_AUDIO_SECONDS
do
    if [[ -n "${!var_name:-}" ]]; then
        ENV_ARGS+=(-e "${var_name}=${!var_name}")
    fi
done

sudo apt update
sudo apt install -y docker.io git
sudo systemctl enable --now docker

sudo docker build -f deployment/Dockerfile -t "${IMAGE_NAME}" .
sudo docker volume create "${MODEL_VOLUME_NAME}" >/dev/null

if sudo docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
    sudo docker rm -f "${CONTAINER_NAME}"
fi

sudo docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${HOST_PORT}:${CONTAINER_PORT}" \
    -e "ZIPVOICE_MODEL_DIR=${MODEL_DIR_IN_CONTAINER}" \
    "${ENV_ARGS[@]}" \
    -v "${MODEL_VOLUME_NAME}:${MODEL_DIR_IN_CONTAINER}" \
    "${IMAGE_NAME}"

echo "Container ${CONTAINER_NAME} started on port ${HOST_PORT}"
echo "Model cache volume: ${MODEL_VOLUME_NAME}"
echo "Health check: curl http://localhost:${HOST_PORT}/health"
echo "Frontend UI:  http://<EC2_PUBLIC_IP>:${HOST_PORT}/"
echo "OpenAPI docs: http://<EC2_PUBLIC_IP>:${HOST_PORT}/docs"
echo "Logs:         sudo docker logs -f ${CONTAINER_NAME}"
