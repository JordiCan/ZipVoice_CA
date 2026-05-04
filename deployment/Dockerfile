FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    espeak-ng \
    libsndfile1 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements_zipvoice.txt requirements_api.txt ./

RUN pip install --upgrade pip && \
    pip install -r requirements_zipvoice.txt && \
    pip install -r requirements_api.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "deployment.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
