"""The model step: one Gemini call reads the rendered evidence pack and returns the diagnosis as JSON.

Answers are cached on disk by model, prompt version and a hash of the pack text, so re-scoring never costs a
request. A day's quota running out raises QuotaExhausted, so a run can stop cleanly.
"""
import hashlib
import json
import os
import pathlib
import time

from . import prompts

MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
RPM = float(os.getenv("GEMINI_RPM", "8"))


class LLMError(Exception):
    pass


class QuotaExhausted(LLMError):
    pass


class Gemini:
    def __init__(self, model: str | None = None, cache_dir=None, offline: bool = False):
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else None
        self.offline = offline
        self.client = None
        if not offline:
            from google import genai
            self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
        self._next = 0.0

    def ask(self, key: str, text: str) -> dict:
        h = hashlib.sha256(text.encode()).hexdigest()[:12]
        path = self.cache_dir / self.model / f"{key}.{prompts.VERSION}.{h}.json" if self.cache_dir else None
        if path and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            raise LLMError(f"no cached answer for {key}")
        answer, usage = self._call(text)
        out = {"model": self.model, "prompt_version": prompts.VERSION, "pack_hash": h, "usage": usage, "answer": answer}
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        return out

    def _call(self, text: str) -> tuple[dict, dict]:
        from google.genai import errors, types

        cfg = types.GenerateContentConfig(system_instruction=prompts.SYSTEM, response_mime_type="application/json",
                                          response_json_schema=prompts.SCHEMA, temperature=0.0, max_output_tokens=4000,
                                          automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        delay = 10.0
        for attempt in range(MAX_RETRIES + 1):
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next = time.monotonic() + 60.0 / RPM
            try:
                r = self.client.models.generate_content(model=self.model, contents=[text], config=cfg)
                um = r.usage_metadata
                usage = {"input_tokens": getattr(um, "prompt_token_count", 0) or 0,
                         "output_tokens": (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)}
                if not r.text:
                    raise LLMError("empty response")
                return json.loads(r.text), usage
            except errors.APIError as e:
                details = (getattr(e, "details", None) or {}).get("error", {}).get("details", [])
                if any("PerDay" in v.get("quotaId", "") for d in details for v in d.get("violations", [])):
                    raise QuotaExhausted(f"daily quota used up for {self.model}") from e
                if getattr(e, "code", None) not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                    raise LLMError(f"Gemini {getattr(e, 'code', '?')}: {str(e)[:200]}") from e
            except json.JSONDecodeError as e:
                if attempt == MAX_RETRIES:
                    raise LLMError(f"invalid JSON: {e}") from e
            except LLMError:
                raise
            except Exception as e:
                if attempt == MAX_RETRIES:
                    raise LLMError(f"transport error: {type(e).__name__}: {str(e)[:200]}") from e
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
        raise LLMError("unreachable")
