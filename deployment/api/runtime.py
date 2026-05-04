import json
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

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


def detect_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda", 0)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def ensure_model_artifacts(runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    downloads = {
        "zipvoice_ca.pt": (CATALAN_MODEL_REPO, "zipvoice_ca.pt"),
        "model.json": (HUGGINGFACE_REPO, "zipvoice/model.json"),
        "tokens.txt": (HUGGINGFACE_REPO, "zipvoice/tokens.txt"),
    }

    for local_name, (repo_id, filename) in downloads.items():
        destination = runtime_dir / local_name
        if destination.is_file():
            continue
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
