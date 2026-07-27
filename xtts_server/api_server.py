import os
import io
import uvicorn
import torch
import asyncio
import numpy as np

# FIX for PyTorch 2.6+
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    if "weights_only" not in kwargs:
        kwargs["weights_only"] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
from TTS.api import TTS
import soundfile as sf

app = FastAPI()

# ═══════════════════════════════════════════════════════════════
# CPU-оптимизации PyTorch
# ═══════════════════════════════════════════════════════════════
torch.set_num_threads(4)          # не съедать все ядра Ultra 5
torch.set_num_interop_threads(2)

device = "cpu"
print(f"[XTTS-v2] Loading model on {device} (threads={torch.get_num_threads()})...")
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
print("[XTTS-v2] Ready on http://localhost:5003")

# Не более 2 одновременных генераций — предотвращает RAM-спайк
tts_semaphore = asyncio.Semaphore(2)

# Кэш: путь к ref_audio -> (gpt_cond_latent, speaker_embedding)
# ЭТО ГЛАВНАЯ ЭКОНОМИЯ. Один раз вычислили — переиспользуем.
_speaker_cache: dict[str, tuple | None] = {}

def _get_cached_latents(ref_audio_path: str):
    """Предвычислить и закэшировать speaker conditioning."""
    if ref_audio_path not in _speaker_cache:
        try:
            model = tts.synthesizer.tts_model
            gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
                audio_path=ref_audio_path
            )
            _speaker_cache[ref_audio_path] = (gpt_cond_latent, speaker_embedding)
            print(f"[XTTS] Cached speaker latents: {os.path.basename(ref_audio_path)}")
        except Exception as e:
            print(f"[XTTS] Latent cache failed ({e}), will use slow path")
            _speaker_cache[ref_audio_path] = None
    return _speaker_cache[ref_audio_path]


class TTSRequest(BaseModel):
    text: str
    ref_audio: str
    ref_text: str = ""


@app.post("/inference")
async def inference(req: TTSRequest):
    if not os.path.exists(req.ref_audio):
        return Response(content=b"Ref audio not found", status_code=400)

    async with tts_semaphore:
        try:
            latents = _get_cached_latents(req.ref_audio)

            if latents is not None:
                # Быстрый путь: reused latents, без повторной загрузки wav
                gpt_cond_latent, speaker_embedding = latents
                out = tts.synthesizer.tts_model.inference(
                    req.text,
                    language="ru",
                    gpt_cond_latent=gpt_cond_latent,
                    speaker_embedding=speaker_embedding,
                )
                # Нормализация выхода под разные версии Coqui TTS
                wav = out.get("wav") if isinstance(out, dict) else out
                if isinstance(wav, torch.Tensor):
                    wav = wav.cpu().numpy()
            else:
                # Fallback: стандартный метод, но без записи на диск
                wav = tts.tts(text=req.text, speaker_wav=req.ref_audio, language="ru")
                if isinstance(wav, list):
                    wav = np.array(wav, dtype=np.float32)

            # Пишем WAV прямо в RAM, без tempfile на диске
            buf = io.BytesIO()
            sf.write(buf, wav, 24000, format="WAV")
            buf.seek(0)
            return Response(content=buf.read(), media_type="audio/wav")

        except Exception as e:
            import traceback
            err = f"XTTS Error: {e}\n{traceback.format_exc()}"
            print(err)
            return Response(content=err.encode(), status_code=500)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5003)