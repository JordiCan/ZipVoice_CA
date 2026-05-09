import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError
import safetensors.torch
import torch
from huggingface_hub import hf_hub_download

from zipvoice.bin.infer_zipvoice import HUGGINGFACE_REPO, MODEL_DIR, get_vocoder
from zipvoice.models.zipvoice import ZipVoice
from zipvoice.models.zipvoice_distill import ZipVoiceDistill
from zipvoice.tokenizer.tokenizer import (
    EmiliaTokenizer,
    EspeakTokenizer,
    LibriTTSTokenizer,
    SimpleTokenizer,
)
from zipvoice.utils.checkpoint import load_checkpoint
from zipvoice.utils.feature import VocosFbank
from zipvoice.utils.tensorrt import load_trt

CATALAN_MODEL_REPO = "ebellob/ZipVoice-CA"
logger = logging.getLogger(__name__)
DEFAULT_SAMPLE_TEXTS = [
    {
        "id": "greeting",
        "label": "Short greeting",
        "text": "Bon dia, com estàs?",
        "prompt_text": "Això és una prova de veu.",
    },
    {
        "id": "news",
        "label": "News style",
        "text": "Avui el temps serà variable amb intervals de núvols i algunes clarianes.",
        "prompt_text": "La locució és clara i natural per a una demostració.",
    },
    {
        "id": "assistant",
        "label": "Virtual assistant",
        "text": "La teva comanda s'ha processat correctament i ja està en camí.",
        "prompt_text": "Parlo amb un to proper i tranquil.",
    },
]
DEFAULT_SAMPLE_TEXTS_FILE = Path(__file__).with_name("sample_texts.json")


def get_s3_client() -> Optional[BaseClient]:
    bucket = os.environ.get("ZIPVOICE_S3_BUCKET")
    if not bucket:
        return None

    session_kwargs: dict[str, str] = {}
    region = os.environ.get("ZIPVOICE_S3_REGION")
    profile = os.environ.get("ZIPVOICE_AWS_PROFILE")
    endpoint_url = os.environ.get("ZIPVOICE_S3_ENDPOINT_URL")
    if region:
        session_kwargs["region_name"] = region
    if profile:
        session_kwargs["profile_name"] = profile

    session = boto3.session.Session(**session_kwargs)
    return session.client("s3", endpoint_url=endpoint_url)


def download_s3_file(
    s3_client: BaseClient,
    bucket: str,
    key: str,
    destination: Path,
) -> bool:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        s3_client.download_file(bucket, key, str(destination))
        logger.info("Downloaded s3://%s/%s into %s", bucket, key, destination)
        return True
    except (BotoCoreError, ClientError):
        logger.exception("Failed to download s3://%s/%s", bucket, key)
        return False


def list_s3_example_audio() -> list[dict[str, str]]:
    bucket = os.environ.get("ZIPVOICE_S3_BUCKET")
    prefix = os.environ.get("ZIPVOICE_S3_EXAMPLES_PREFIX", "").strip("/")
    s3_client = get_s3_client()
    if not bucket or not prefix or s3_client is None:
        return []

    normalized_prefix = f"{prefix}/"
    try:
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=normalized_prefix)
    except (BotoCoreError, ClientError):
        logger.exception("Failed to list example objects in s3://%s/%s", bucket, normalized_prefix)
        return []

    examples: list[dict[str, str]] = []
    for item in response.get("Contents", []):
        key = item.get("Key", "")
        if not key or key.endswith("/"):
            continue
        try:
            url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=int(os.environ.get("ZIPVOICE_S3_EXAMPLE_URL_TTL", "3600")),
            )
        except (BotoCoreError, ClientError):
            logger.exception("Failed to generate presigned URL for s3://%s/%s", bucket, key)
            continue

        examples.append(
            {
                "name": Path(key).name,
                "s3_key": key,
                "url": url,
            }
        )

    return examples


def prepare_sample_texts_file(runtime_dir: Path) -> Optional[Path]:
    path = os.environ.get("ZIPVOICE_SAMPLE_TEXTS_FILE")
    if path:
        sample_path = Path(path)
        if sample_path.is_file():
            return sample_path
        logger.warning("Sample texts file %s does not exist", sample_path)

    s3_bucket = os.environ.get("ZIPVOICE_S3_BUCKET")
    s3_key = os.environ.get("ZIPVOICE_S3_SAMPLE_TEXTS_KEY")
    s3_client = get_s3_client()
    if s3_bucket and s3_key and s3_client is not None:
        destination = runtime_dir / "sample_texts.json"
        if destination.is_file() or download_s3_file(
            s3_client=s3_client,
            bucket=s3_bucket,
            key=s3_key,
            destination=destination,
        ):
            return destination

    if DEFAULT_SAMPLE_TEXTS_FILE.is_file():
        return DEFAULT_SAMPLE_TEXTS_FILE

    return None


def load_sample_texts(runtime_dir: Path) -> list[dict[str, str]]:
    sample_file = prepare_sample_texts_file(runtime_dir)
    if sample_file is None:
        return DEFAULT_SAMPLE_TEXTS

    path = str(sample_file)
    if not path:
        return DEFAULT_SAMPLE_TEXTS

    sample_path = Path(path)
    if not sample_path.is_file():
        logger.warning("Sample texts file %s does not exist, using defaults", sample_path)
        return DEFAULT_SAMPLE_TEXTS

    with open(sample_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("ZIPVOICE_SAMPLE_TEXTS_FILE must contain a JSON list")
    return data


def detect_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda", 0)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def ensure_model_artifacts(runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    s3_bucket = os.environ.get("ZIPVOICE_S3_BUCKET")
    s3_client = get_s3_client()
    s3_downloads = {
        "zipvoice_ca.pt": os.environ.get("ZIPVOICE_S3_CHECKPOINT_KEY"),
        "model.json": os.environ.get("ZIPVOICE_S3_MODEL_CONFIG_KEY"),
        "tokens.txt": os.environ.get("ZIPVOICE_S3_TOKENS_KEY"),
    }
    hf_downloads = {
        "zipvoice_ca.pt": (CATALAN_MODEL_REPO, "zipvoice_ca.pt"),
        "model.json": (HUGGINGFACE_REPO, "zipvoice/model.json"),
        "tokens.txt": (HUGGINGFACE_REPO, "zipvoice/tokens.txt"),
    }

    for local_name, (repo_id, filename) in hf_downloads.items():
        destination = runtime_dir / local_name
        if destination.is_file():
            continue

        s3_key = s3_downloads.get(local_name)
        if s3_bucket and s3_key and s3_client is not None:
            if download_s3_file(
                s3_client=s3_client,
                bucket=s3_bucket,
                key=s3_key,
                destination=destination,
            ):
                continue
            logger.warning(
                "Falling back to Hugging Face for %s after S3 download failure",
                local_name,
            )

        downloaded_path = Path(hf_hub_download(repo_id=repo_id, filename=filename))
        shutil.copy2(downloaded_path, destination)
        logger.info("Downloaded %s from %s into %s", filename, repo_id, destination)

    return runtime_dir


def resolve_model_assets(
    model_name: str,
    model_dir: Optional[str] = None,
    checkpoint_name: str = "model.pt",
):
    if model_dir is not None:
        model_dir = Path(model_dir)
        if not model_dir.is_dir():
            raise FileNotFoundError(f"{model_dir} does not exist")
        for filename in [checkpoint_name, "model.json", "tokens.txt"]:
            if not (model_dir / filename).is_file():
                raise FileNotFoundError(f"{model_dir / filename} does not exist")
        logger.info(
            "Using %s in local model dir %s, checkpoint %s",
            model_name,
            model_dir,
            checkpoint_name,
        )
        return (
            model_dir / checkpoint_name,
            model_dir / "model.json",
            model_dir / "tokens.txt",
        )

    logger.info("Using pretrained %s model from Hugging Face", model_name)
    model_ckpt = hf_hub_download(
        HUGGINGFACE_REPO, filename=f"{MODEL_DIR[model_name]}/model.pt"
    )
    model_config = hf_hub_download(
        HUGGINGFACE_REPO, filename=f"{MODEL_DIR[model_name]}/model.json"
    )
    token_file = hf_hub_download(
        HUGGINGFACE_REPO, filename=f"{MODEL_DIR[model_name]}/tokens.txt"
    )
    return Path(model_ckpt), Path(model_config), Path(token_file)


def build_tokenizer(tokenizer_name: str, token_file: str, lang: str):
    if tokenizer_name == "emilia":
        return EmiliaTokenizer(token_file=token_file)
    if tokenizer_name == "libritts":
        return LibriTTSTokenizer(token_file=token_file)
    if tokenizer_name == "espeak":
        return EspeakTokenizer(token_file=token_file, lang=lang)
    assert tokenizer_name == "simple"
    return SimpleTokenizer(token_file=token_file)


def load_inference_pipeline(
    model_name: str = "zipvoice",
    model_dir: Optional[str] = None,
    checkpoint_name: str = "model.pt",
    tokenizer_name: str = "emilia",
    lang: str = "en-us",
    vocoder_path: Optional[str] = None,
    trt_engine_path: Optional[str] = None,
    device: Optional[torch.device] = None,
) -> dict[str, Any]:
    model_ckpt, model_config_path, token_file = resolve_model_assets(
        model_name=model_name,
        model_dir=model_dir,
        checkpoint_name=checkpoint_name,
    )

    tokenizer = build_tokenizer(
        tokenizer_name=tokenizer_name,
        token_file=str(token_file),
        lang=lang,
    )
    tokenizer_config = {"vocab_size": tokenizer.vocab_size, "pad_id": tokenizer.pad_id}

    with open(model_config_path, "r") as f:
        model_config = json.load(f)

    if model_name == "zipvoice":
        model = ZipVoice(**model_config["model"], **tokenizer_config)
    else:
        assert model_name == "zipvoice_distill"
        model = ZipVoiceDistill(**model_config["model"], **tokenizer_config)

    if str(model_ckpt).endswith(".safetensors"):
        safetensors.torch.load_model(model, model_ckpt)
    elif str(model_ckpt).endswith(".pt"):
        load_checkpoint(filename=model_ckpt, model=model, strict=True)
    else:
        raise NotImplementedError(f"Unsupported model checkpoint format: {model_ckpt}")

    device = device or detect_device()
    logger.info("Device: %s", device)

    model = model.to(device)
    model.eval()

    if trt_engine_path:
        load_trt(model, trt_engine_path)

    vocoder = get_vocoder(vocoder_path)
    vocoder = vocoder.to(device)
    vocoder.eval()

    if model_config["feature"]["type"] != "vocos":
        raise NotImplementedError(
            f"Unsupported feature type: {model_config['feature']['type']}"
        )

    return {
        "model": model,
        "vocoder": vocoder,
        "tokenizer": tokenizer,
        "feature_extractor": VocosFbank(),
        "device": device,
        "sampling_rate": model_config["feature"]["sampling_rate"],
        "model_config": model_config,
        "model_ckpt": model_ckpt,
        "token_file": token_file,
    }
