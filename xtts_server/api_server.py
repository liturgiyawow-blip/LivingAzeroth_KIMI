import os
import io
import uvicorn
import torch
import tempfile
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
from TTS.api import TTS

app = FastAPI()

device = "cuda" if torch.cuda.is_available() else "cpu"
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

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        tts.tts_to_file(
            text=req.text,
            speaker_wav=req.ref_audio,
            language="ru",
            file_path=tmp_path
        )

        with open(tmp_path, "rb") as f:
            data = f.read()

        return Response(content=data, media_type="audio/wav")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5002)