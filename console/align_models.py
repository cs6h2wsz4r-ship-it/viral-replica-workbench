"""Read-only alignment model checks shared by ASR, settings and installers."""
from __future__ import annotations

import json
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS_DIR = ROOT / "runtime/models/torch/hub/checkpoints"
HF_HUB = ROOT / "runtime/models/huggingface/hub"
EN_MODEL_SIZE = 377_664_473
EN_MODEL_URL = "https://download.pytorch.org/torchaudio/models/wav2vec2_fairseq_base_ls960_asr_ls960.pth"
_download_lock = threading.RLock()


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _hf_status(repo: Path) -> str:
    if not repo.exists():
        return "missing"
    try:
        # from_pretrained uses main; an unrelated cached revision is not sufficient.
        revision = (repo / "refs/main").read_text(encoding="utf-8").strip()
        if not revision or Path(revision).name != revision:
            return "broken"
        snapshot = repo / "snapshots" / revision
        for name in ("config.json", "vocab.json", "preprocessor_config.json"):
            if not isinstance(json.loads((snapshot / name).read_text(encoding="utf-8")), dict):
                return "broken"
        weights = [snapshot / name for name in ("model.safetensors", "pytorch_model.bin")]
        return "ok" if any(p.is_file() and p.stat().st_size > 0 for p in weights) else "broken"
    except (OSError, ValueError):
        return "broken"


def check_align_model(language: str | None) -> dict:
    info = {"language": language, "model": "未配置默认模型", "type": "unknown",
            "path": "runtime/models", "status": "missing", "download_url": ""}
    try:
        from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF, DEFAULT_ALIGN_MODELS_TORCH
        if language in DEFAULT_ALIGN_MODELS_TORCH:
            import torchaudio
            model = DEFAULT_ALIGN_MODELS_TORCH[language]
            info.update(model=model, type="torchaudio", path=_relative(CHECKPOINTS_DIR))
            source = getattr(torchaudio.pipelines, model)._path
            filename = Path(urlparse(source).path).name
            path = CHECKPOINTS_DIR / filename
            info.update(path=_relative(path), download_url=(source if source.startswith("https://") else
                        f"https://download.pytorch.org/torchaudio/models/{source}"))
            if language == "en":
                info.update(download_url=EN_MODEL_URL, size=EN_MODEL_SIZE)
            if path.exists():
                info["status"] = "ok" if (path.is_file() and zipfile.is_zipfile(path) and
                    (language != "en" or path.stat().st_size == EN_MODEL_SIZE)) else "broken"
        elif language in DEFAULT_ALIGN_MODELS_HF:
            model = DEFAULT_ALIGN_MODELS_HF[language]
            path = HF_HUB / ("models--" + model.replace("/", "--"))
            info.update(model=model, type="huggingface", path=_relative(path), download_url=model,
                        status=_hf_status(path))
        else:
            info["detail"] = "WhisperX 没有此语言的默认对齐模型映射，无法确定模型名和具体存放路径"
    except Exception as exc:
        info.update(status="broken", detail=f"无法检查本地对齐模型：{type(exc).__name__}: {exc}")
    return info


def report() -> list[dict]:
    return [check_align_model(language) for language in ("zh", "en")]


def unavailable_reason(info: dict) -> str:
    state = "文件不完整或已损坏，请重新下载后替换" if info["status"] == "broken" else "未安装本地对齐模型"
    reason = f"语言 {info['language']}，模型 {info['model']}：{state}；应放到 {info['path']}"
    if info.get("download_url"):
        reason += f"；下载来源：{info['download_url']}"
    if info.get("size"):
        reason += f"；完整大小：{info['size']} 字节"
    if info.get("detail"):
        reason += f"；{info['detail']}"
    return reason


@contextmanager
def block_torch_downloads():
    """Serialize temporary hub guards so concurrent loads cannot restore too early."""
    import torch.hub

    def blocked(*args, **kwargs):
        raise RuntimeError("对齐模型仅允许使用本地文件，禁止下载（torch.hub）")

    with _download_lock:
        original = torch.hub.download_url_to_file
        torch.hub.download_url_to_file = blocked
        try:
            yield
        finally:
            torch.hub.download_url_to_file = original
