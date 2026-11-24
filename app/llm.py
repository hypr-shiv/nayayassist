"""
LLM Provider adapters and contract enforcement.
Must NOT decide what is legal content or score confidence (Architectural Rule 4.1).
Default local provider: Ollama with qwen2.5:3b (optimized for 8GB RAM systems).
"""

import json
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

import httpx

from app import config
from app.models import LLMExplainOutput, LLMQAOutput


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def generate_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Calls the model and returns parsed JSON. Raises Exception on parse or network failure."""
        pass


class NoneProvider(LLMProvider):
    name = "none"

    def generate_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        raise RuntimeError("No LLM provider configured. Operating in 100% deterministic rule mode.")


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str = config.OLLAMA_URL, model: str = config.OLLAMA_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.0,
                "top_p": 0.1
            }
        }
        
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get("response", "{}")
            return json.loads(response_text)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str = config.GEMINI_API_KEY, model: str = config.GEMINI_MODEL):
        if config.DATA_MODE == "real":
            raise RuntimeError("Gemini provider is strictly blocked when DATA_MODE=real to prevent data leaks (ADR-006).")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini provider.")
        self.api_key = api_key
        self.model = model

    def generate_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"System:\n{system_prompt}\n\nUser:\n{user_prompt}"}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "response_mime_type": "application/json"
            }
        }
        
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("No response returned from Gemini API")
            part_text = candidates[0]["content"]["parts"][0]["text"]
            return json.loads(part_text)


def get_llm_provider() -> LLMProvider:
    """Factory returning configured LLMProvider."""
    provider_name = config.LLM_PROVIDER
    if provider_name == "ollama":
        return OllamaProvider()
    elif provider_name == "gemini":
        return GeminiProvider()
    return NoneProvider()
