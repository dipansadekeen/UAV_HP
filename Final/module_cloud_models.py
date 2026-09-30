# module_cloud_models.py

import time, requests
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


# new ///
def call_ollama_cloud(self, system_text: str, user_text: str, tag: str = "general") -> str:
    t0 = time.monotonic()
    # model_name = "gpt-oss:20b-cloud" # new
    # model_name = "nemotron-3-super:cloud" # new
    # model_name = "nemotron-3-nano:30b-cloud" # new
    model_name = "gemma4:31b-cloud" # new
    # model_name = "gpt-oss:120b-cloud" # new


    try:
        with open("api_key.txt", "r") as f:
            api_key = f.read().strip()

        payload = {
            "model": model_name,  #new
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text}
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "top_p": 0.9
            }
        }

        r = requests.post(
            "https://ollama.com/api/chat",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=360
        )

        if r.status_code != 200:
            print(f"[LLM CLOUD ERROR {tag}] {r.text[:300]}", flush=True)

        r.raise_for_status()

        raw = r.json()["message"]["content"]
        dt_ms = (time.monotonic() - t0) * 1000.0

        self.last_llm_latency_ms = dt_ms # new
        self.last_llm_model_name = model_name #new

        parsed = extract_json(raw)
        self.log_llm_io(tag, system_text, user_text, raw, parsed, dt_ms)

        return raw

    except Exception as e:
        print(f"[LLM CLOUD FAIL {tag}] {e}", flush=True)
        return ""


def call_ollama(self, system_text: str, user_text: str, tag: str = "general") -> str:
    payload = {
        "model": self.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text}
        ],
        "stream": False,
        "format": "json",  #  FORCE VALID JSON
        "options": {
            "temperature": 0,
            "top_p": 0.9
        }
    }
    t0 = time.monotonic()
    r = requests.post(
        f"{self.OLLAMA_URL}/api/chat",
        json=payload,
        timeout=self.OLLAMA_TIMEOUT_SEC,
    )
    r.raise_for_status()
    
    raw = r.json()["message"]["content"]
    dt_ms = (time.monotonic() - t0) * 1000.0

    self.last_llm_latency_ms = dt_ms # new
    self.last_llm_model_name = self.OLLAMA_MODEL # new

    parsed = extract_json(raw)  # may be None
    self.log_llm_io(tag, system_text, user_text, raw, parsed, dt_ms)

    return raw