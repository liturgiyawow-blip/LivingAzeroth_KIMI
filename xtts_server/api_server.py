import os
import io
import uvicorn
import torch
import tempfile

# FIX for PyTorch 2.6+: XTTS models need weights_only=False
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

app = FastAPI()

device = "cpu"
print(f"[XTTS-v2] Loading model on {device}...")
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
print("[XTTS-v2] Ready on http://localhost:5002")


class TTSRequest(BaseModel):
    text: str
    ref_audio: str
    ref_text: str = ""


@app.post("/inference")
async def inference(req: TTSRequest):
    if not os.path.exists(req.ref_audio):
        return Response(content=b"Ref audio not found", status_code=400)

    tmp_path = None
    try:
        # Создаём временный файл (удалится автоматически при выходе из with)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        # Генерация через ПРОВЕРЕННЫЙ метод tts_to_file
        tts.tts_to_file(
            text=req.text,
            speaker_wav=req.ref_audio,
            language="ru",
            file_path=tmp_path,
        )

        # Читаем сгенерированный файл в RAM
        with open(tmp_path, "rb") as f:
            data = f.read()

        return Response(content=data, media_type="audio/wav")

    except Exception as e:
        import traceback
        err = f"XTTS Error: {e}\n{traceback.format_exc()}"
        print(err)
        return Response(content=err.encode(), status_code=500)

    finally:
        # ВАЖНО: удаляем временный файл в любом случае
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as del_err:
                print(f"[XTTS-v2] Failed to remove temp file: {del_err}")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5003)
