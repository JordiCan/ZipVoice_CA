# AWS Deployment for ZipVoice-CA

ZipVoice-CA includes a compact AWS deployment designed for fast demo delivery: a Dockerized FastAPI service running on EC2, with model artifacts and optional demo assets stored in Amazon S3.

If you want the step-by-step AWS Academy flow, use [AWS_ACADEMY_GUIDE.md](/root/ZipVoice_CA/deployment/aws/AWS_ACADEMY_GUIDE.md).

## Architecture

- `EC2`: runs the API container
- `Docker`: packages the runtime and serves FastAPI on port `8000`
- `S3`: stores the Catalan checkpoint and optional demo assets
- `Swagger UI`: provides a ready-to-use web interface at `/docs`

At startup, the API prepares a runtime directory with:

- `zipvoice_ca.pt`
- `model.json`
- `tokens.txt`

These files can be loaded directly from S3. If you only place the checkpoint in S3, the API can still fetch `model.json` and `tokens.txt` from the upstream ZipVoice repository.

## Included files

- `deployment/api/main.py`: FastAPI app, validation, and example endpoints
- `deployment/api/runtime.py`: model loading, S3 downloads, and sample asset helpers
- `deployment/api/sample_texts.json`: bundled Catalan example prompts
- `deployment/Dockerfile`: CPU image with health check support
- `deployment/aws/ec2_bootstrap.sh`: helper script for a prepared EC2 instance

## S3 asset layout

A practical bucket layout looks like this:

```text
s3://your-bucket/zipvoice-ca/
├── models/
│   ├── zipvoice_ca.pt
│   ├── model.json
│   └── tokens.txt
└── examples/
    ├── sample_texts.json
    ├── prompt_01.wav
    └── prompt_02.wav
```

Recommended environment variables:

- `ZIPVOICE_S3_BUCKET`
- `ZIPVOICE_S3_CHECKPOINT_KEY`
- `ZIPVOICE_S3_MODEL_CONFIG_KEY`
- `ZIPVOICE_S3_TOKENS_KEY`
- `ZIPVOICE_S3_SAMPLE_TEXTS_KEY`
- `ZIPVOICE_S3_EXAMPLES_PREFIX`
- `ZIPVOICE_S3_REGION`

Example:

```bash
export ZIPVOICE_S3_BUCKET=your-bucket
export ZIPVOICE_S3_REGION=us-east-1
export ZIPVOICE_S3_CHECKPOINT_KEY=zipvoice-ca/models/zipvoice_ca.pt
export ZIPVOICE_S3_MODEL_CONFIG_KEY=zipvoice-ca/models/model.json
export ZIPVOICE_S3_TOKENS_KEY=zipvoice-ca/models/tokens.txt
export ZIPVOICE_S3_SAMPLE_TEXTS_KEY=zipvoice-ca/examples/sample_texts.json
export ZIPVOICE_S3_EXAMPLES_PREFIX=zipvoice-ca/examples
```

## Local Docker run

```bash
docker build -f deployment/Dockerfile -t zipvoice-api .
docker volume create zipvoice-model-cache
docker run --rm \
  -p 8000:8000 \
  -e ZIPVOICE_MODEL_DIR=/app/models/zipvoice_ca_runtime \
  -e ZIPVOICE_S3_BUCKET="$ZIPVOICE_S3_BUCKET" \
  -e ZIPVOICE_S3_REGION="$ZIPVOICE_S3_REGION" \
  -e ZIPVOICE_S3_CHECKPOINT_KEY="$ZIPVOICE_S3_CHECKPOINT_KEY" \
  -e ZIPVOICE_S3_MODEL_CONFIG_KEY="$ZIPVOICE_S3_MODEL_CONFIG_KEY" \
  -e ZIPVOICE_S3_TOKENS_KEY="$ZIPVOICE_S3_TOKENS_KEY" \
  -e ZIPVOICE_S3_SAMPLE_TEXTS_KEY="$ZIPVOICE_S3_SAMPLE_TEXTS_KEY" \
  -e ZIPVOICE_S3_EXAMPLES_PREFIX="$ZIPVOICE_S3_EXAMPLES_PREFIX" \
  -v zipvoice-model-cache:/app/models/zipvoice_ca_runtime \
  zipvoice-api
```

Then open:

```text
http://localhost:8000/docs
```

## EC2 deployment

1. Launch an Ubuntu `t3.large` instance.
2. Open inbound rules for `22/tcp` and `8000/tcp`.
3. Install Docker and Git:

```bash
sudo apt update
sudo apt install -y docker.io git
sudo systemctl enable --now docker
```

4. Clone the repository:

```bash
git clone https://github.com/YOUR_USER/ZipVoice_CA.git
cd ZipVoice_CA
```

5. Export your S3 settings:

```bash
export ZIPVOICE_S3_BUCKET=your-bucket
export ZIPVOICE_S3_REGION=us-east-1
export ZIPVOICE_S3_CHECKPOINT_KEY=zipvoice-ca/models/zipvoice_ca.pt
export ZIPVOICE_S3_MODEL_CONFIG_KEY=zipvoice-ca/models/model.json
export ZIPVOICE_S3_TOKENS_KEY=zipvoice-ca/models/tokens.txt
export ZIPVOICE_S3_SAMPLE_TEXTS_KEY=zipvoice-ca/examples/sample_texts.json
export ZIPVOICE_S3_EXAMPLES_PREFIX=zipvoice-ca/examples
```

6. Start the deployment:

```bash
chmod +x deployment/aws/ec2_bootstrap.sh
./deployment/aws/ec2_bootstrap.sh
```

7. Verify the API:

```bash
curl http://YOUR_PUBLIC_IP:8000/health
curl http://YOUR_PUBLIC_IP:8000/examples
```

8. Open the interactive interface:

```text
http://YOUR_PUBLIC_IP:8000/docs
```

## API endpoints

- `GET /health`: service status, loaded device, runtime path, and artifact source
- `GET /examples`: sample texts and optional presigned S3 URLs for reference audio
- `POST /synthesize`: inference endpoint returning `audio/wav`

Example request:

```bash
curl -X POST "http://localhost:8000/synthesize" \
  -F "text=Bon dia, com estàs?" \
  -F "prompt_text=Això és una prova de veu." \
  -F "prompt_audio=@prompt.wav" \
  --output result.wav
```

## Request shaping

Default limits are tuned for a smooth demo experience:

- `text`: 300 characters
- `prompt_text`: 300 characters
- `prompt_audio`: 10 MB
- `prompt_audio` duration: 30 seconds

You can adjust them with:

- `ZIPVOICE_MAX_TEXT_CHARS`
- `ZIPVOICE_MAX_PROMPT_TEXT_CHARS`
- `ZIPVOICE_MAX_PROMPT_AUDIO_BYTES`
- `ZIPVOICE_MAX_PROMPT_AUDIO_SECONDS`

## Publishing notes

This deployment is well suited for academic demos, internal showcases, and lightweight inference endpoints. The `/docs` page offers an immediate web interface, while `/examples` gives users ready-made Catalan prompts and optional reference audio assets from S3.
