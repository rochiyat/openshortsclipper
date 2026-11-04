"""Rewrite a clip's copy (title, hook, caption, hashtags) for a channel's language.

Suggestions only: the caller shows them in the edit form and the user saves.
Uses the local OpenAI-compatible LLM when one is configured (llm_backend),
otherwise Gemini with the caller's key (X-Gemini-Key) or GEMINI_API_KEY.
"""
import json
import os
from typing import Optional

from pydantic import BaseModel

LANGUAGE_NAMES = {
    "id": "Indonesian (Bahasa Indonesia)", "en": "English", "ms": "Malay", "jv": "Javanese",
    "su": "Sundanese", "ar": "Arabic", "es": "Spanish", "pt": "Portuguese", "fr": "French",
    "de": "German", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "hi": "Hindi",
}

STYLE_NOTES = {
    "id": ("Write natural, conversational Indonesian the way Indonesian short-video "
           "creators talk (santai, not stiff textbook language), but no slang that "
           "would sound fake. Keep English loanwords that Indonesians actually use."),
}


class CopySuggestion(BaseModel):
    title: str
    hook_text: str
    caption: str
    hashtags: str


class CopyError(Exception):
    pass


def language_name(code: str) -> str:
    base = (code or "").split("-")[0].lower()
    return LANGUAGE_NAMES.get(base, code)


def build_prompt(clip: dict, language: str, platform: Optional[str]) -> str:
    lang = language_name(language)
    style = STYLE_NOTES.get((language or "").split("-")[0].lower(), "")
    where = f" for {platform}" if platform else ""
    return f"""You adapt the copy of a short vertical video{where}. Rewrite it in {lang}.

Rules:
- Keep the meaning and every fact; do not invent claims, numbers or quotes.
- title: at most 90 characters, curiosity without clickbait lies.
- hook_text: the on-screen hook, at most 10 words, 1-2 fitting emojis at most.
- caption: 1-3 short sentences that make people watch to the end; no hashtags here.
- hashtags: 3-6 relevant hashtags in one line, mostly in {lang}, separated by spaces.
{('- ' + style) if style else ''}

Current copy (may be in another language):
{json.dumps(clip, ensure_ascii=False, indent=2)}

Answer with JSON: {{"title": "...", "hook_text": "...", "caption": "...", "hashtags": "..."}}"""


def _clean(d: dict) -> dict:
    out = CopySuggestion(**d).model_dump()
    out["title"] = " ".join(out["title"].split())[:100]
    out["hook_text"] = " ".join(out["hook_text"].split())[:300]
    out["caption"] = out["caption"].strip()[:5000]
    out["hashtags"] = " ".join(out["hashtags"].split())[:1000]
    return out


def rewrite(clip: dict, language: str, platform: Optional[str] = None,
            gemini_key: Optional[str] = None) -> dict:
    prompt = build_prompt(clip, language, platform)
    import llm_backend
    if llm_backend.active():
        try:
            data, _cost = llm_backend.generate_json(prompt, CopySuggestion)
        except Exception as e:  # noqa: BLE001
            raise CopyError(f"Local LLM failed: {type(e).__name__}") from e
        return _clean(data)

    key = gemini_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise CopyError("Set your Gemini API key (settings) or GEMINI_API_KEY to rewrite copy")
    try:
        from google import genai
        from google.genai import types as genai_types
        import gemini_worker
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash-lite",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=CopySuggestion,
                temperature=0.6))
        gemini_worker.raise_if_blocked(response)
        return _clean(json.loads(response.text))
    except CopyError:
        raise
    except Exception as e:  # noqa: BLE001 — never echo the key; type + short reason
        raise CopyError(f"Gemini failed: {type(e).__name__}: {str(e)[:160]}") from e
