# AWS Deployment for ZipVoice-CA

ZipVoice-CA now uses a hybrid architecture for demos: the public frontend and API run on EC2, while the real model executes on a local worker machine with enough compute.

If you want the AWS Academy launch flow, use [AWS_ACADEMY_GUIDE.md](/root/ZipVoice_CA/deployment/aws/AWS_ACADEMY_GUIDE.md).

## Architecture

```text
Browser
  -> React frontend on EC2
  -> FastAPI job API on EC2
  -> S3 for samples, manifests, model assets, and outputs
  -> local worker polling EC2 and running ZipVoice-CA
```

This keeps the deployed cloud interface and storage pipeline while avoiding the need to run the full inference stack on an undersized EC2 instance.

## Included files

- `deployment/api/main.py`: hybrid FastAPI app with sample listing, job queue, worker callbacks, and SPA serving
- `deployment/api/runtime.py`: S3 manifests, presigned URLs, model loading helpers, and upload/download utilities
- `deployment/worker/worker.py`: polling worker for local inference
- `deployment/frontend/`: Vite + React demo UI
- `deployment/Dockerfile`: multi-stage image that builds the frontend and serves the API
- `deployment/aws/ec2_bootstrap.sh`: helper script for a prepared EC2 instance

## Recommended S3 layout

```text
s3://your-bucket/zipvoice-ca/
├── models/
│   ├── zipvoice_ca.pt
│   ├── model.json
│   └── tokens.txt
├── examples/
│   ├── greeting.wav
│   ├── broadcast.wav
│   └── assistant.wav
├── manifests/
│   ├── samples_manifest.json
│   └── cached_results_manifest.json
└── results/
    └── ...
```

The repository includes local manifest templates:

- [s3_artifacts/examples/samples_manifest.json](/root/ZipVoice_CA/s3_artifacts/examples/samples_manifest.json)
- [s3_artifacts/examples/cached_results_manifest.json](/root/ZipVoice_CA/s3_artifacts/examples/cached_results_manifest.json)

Each sample entry should include `id`, `label`, `text`, `prompt_text`, and `prompt_audio_s3_key`.

## EC2 environment

Recommended environment variables for the API container:

```bash
export ZIPVOICE_DEMO_MODE=hybrid
export ZIPVOICE_WORKER_TOKEN=change-me
export ZIPVOICE_S3_BUCKET=your-bucket
export ZIPVOICE_S3_REGION=us-east-1
export ZIPVOICE_S3_CHECKPOINT_KEY=zipvoice-ca/models/zipvoice_ca.pt
export ZIPVOICE_S3_MODEL_CONFIG_KEY=zipvoice-ca/models/model.json
export ZIPVOICE_S3_TOKENS_KEY=zipvoice-ca/models/tokens.txt
export ZIPVOICE_S3_SAMPLES_PREFIX=zipvoice-ca/examples
export ZIPVOICE_S3_RESULTS_PREFIX=zipvoice-ca/results
export ZIPVOICE_S3_SAMPLES_MANIFEST_KEY=zipvoice-ca/manifests/samples_manifest.json
export ZIPVOICE_S3_CACHED_RESULTS_MANIFEST_KEY=zipvoice-ca/manifests/cached_results_manifest.json
export ZIPVOICE_JOB_POLL_INTERVAL_SECONDS=5
export ZIPVOICE_JOB_RESULT_URL_TTL=3600
```

Prefer attaching an IAM Role to the EC2 instance instead of storing static AWS keys in the machine or container.

## EC2 deployment

1. Launch an Ubuntu instance and open `22/tcp` plus `8000/tcp`.
2. Clone the repository on the instance.
3. Export the environment variables above.
4. Start the container:

```bash
chmod +x deployment/aws/ec2_bootstrap.sh
./deployment/aws/ec2_bootstrap.sh
```

5. Verify the service:

```bash
curl http://YOUR_PUBLIC_IP:8000/health
curl http://YOUR_PUBLIC_IP:8000/samples
```

6. Open the demo UI:

```text
http://YOUR_PUBLIC_IP:8000/
```

OpenAPI docs remain available at:

```text
http://YOUR_PUBLIC_IP:8000/docs
```

## Local worker

Run the worker from your local machine or a stronger host:

```bash
export EC2_API_URL=http://YOUR_PUBLIC_IP:8000
export WORKER_TOKEN=change-me
export WORKER_ID=my-laptop
export ZIPVOICE_S3_BUCKET=your-bucket
export ZIPVOICE_S3_REGION=us-east-1
export ZIPVOICE_S3_CHECKPOINT_KEY=zipvoice-ca/models/zipvoice_ca.pt
export ZIPVOICE_S3_MODEL_CONFIG_KEY=zipvoice-ca/models/model.json
export ZIPVOICE_S3_TOKENS_KEY=zipvoice-ca/models/tokens.txt
export ZIPVOICE_S3_RESULTS_PREFIX=zipvoice-ca/results
python -m deployment.worker.worker
```

The worker only makes outbound requests to EC2 and S3. No inbound ports are required on your local machine.

## API endpoints

- `GET /health`: hybrid mode, S3 status, worker heartbeat, and sample count
- `GET /samples`: sample metadata and presigned prompt-audio URLs
- `POST /jobs`: create an inference job from a `sample_id`
- `GET /jobs/pending`: worker-only endpoint to claim the next pending job
- `POST /jobs/{job_id}/result`: worker-only endpoint to publish success or failure
- `GET /jobs/{job_id}`: job status, cached flag, result key, and result URL when available

Example job creation:

```bash
curl -X POST "http://localhost:8000/jobs" \
  -H "Content-Type: application/json" \
  -d '{"sample_id":"greeting"}'
```

## Notes

- Jobs are stored only in memory on the API instance.
- Sample manifests and cached demo outputs survive restarts because they live in S3.
- If a sample has a cached output listed in `cached_results_manifest.json`, the API returns it immediately as `served_from_cache`.
