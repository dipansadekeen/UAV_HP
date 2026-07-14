# module_cloud_models.py

import time
from pathlib import Path

from google import genai
from google.genai import types

from module_helper_functions import extract_json


GEMINI_MODEL_NAME = "gemini-2.5-flash"


def load_gemini_key(key_file="gemini_api.txt"):
    key_path = Path(__file__).resolve().parent / key_file

    with open(key_path, "r") as f:
        return f.read().strip()


GEMINI_API_KEY = load_gemini_key()
client = genai.Client(api_key=GEMINI_API_KEY)

def call_gemini_cloud(
    system_text: str,
    user_text: str,
    tag: str = "general",
    model_name: str = GEMINI_MODEL_NAME,
    log_fn=None,
    return_meta: bool = False,
):
    t0 = time.monotonic()

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system_text,
                temperature=0,
                top_p=0.9,
                response_mime_type="application/json",
            ),
        )

        raw = response.text.strip() if response.text else ""
        dt_ms = (time.monotonic() - t0) * 1000.0

        parsed = extract_json(raw)

        if log_fn is not None:
            log_fn(tag, system_text, user_text, raw, parsed, dt_ms)

        if return_meta:
            return {
                "raw": raw,
                "parsed": parsed,
                "latency_ms": dt_ms,
                "model_name": model_name,
                "tag": tag,
            }

        return raw

    except Exception as e:
        dt_ms = (time.monotonic() - t0) * 1000.0

        print(f"[LLM GEMINI FAIL {tag}] {e}", flush=True)

        if return_meta:
            return {
                "raw": "",
                "parsed": None,
                "latency_ms": dt_ms,
                "model_name": model_name,
                "tag": tag,
                "error": str(e),
            }

        return ""