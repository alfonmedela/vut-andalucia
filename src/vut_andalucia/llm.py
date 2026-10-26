"""Proveedores de LLM detrás de una interfaz mínima: devolver JSON que cumpla un esquema."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, Protocol

import httpx


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    nombre: str

    def json(self, sistema: str, usuario: str, esquema: dict[str, Any]) -> dict[str, Any]: ...


class Ollama:
    """LLM local vía Ollama, con salida estructurada (parámetro `format` = JSON Schema)."""

    def __init__(
        self, modelo: str | None = None, url: str | None = None, timeout: float = 300
    ) -> None:
        self.modelo = modelo or os.environ.get("VUT_OLLAMA_MODEL", "qwen3:8b")
        self.url = (url or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
        self.nombre = f"ollama:{self.modelo}"
        self._client = httpx.Client(timeout=timeout)

    def json(self, sistema: str, usuario: str, esquema: dict[str, Any]) -> dict[str, Any]:
        cuerpo = {
            "model": self.modelo,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            "format": esquema,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_ctx": 16384},
        }
        try:
            r = self._client.post(f"{self.url}/api/chat", json=cuerpo)
        except httpx.ConnectError as e:
            raise LLMError(
                f"No se pudo conectar con Ollama en {self.url}. ¿Está arrancado (`ollama serve`)?"
            ) from e
        except httpx.HTTPError as e:
            raise LLMError(f"Error de red con Ollama ({type(e).__name__}): {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Ollama respondió {r.status_code}: {r.text[:300]}")
        try:
            cuerpo_r = r.json()
        except ValueError as e:
            raise LLMError(f"Ollama devolvió una respuesta que no es JSON: {r.text[:200]!r}") from e
        if cuerpo_r.get("done_reason") == "length":
            raise LLMError("La respuesta de Ollama se cortó por longitud (num_predict/num_ctx)")
        return _parsear_json(cuerpo_r.get("message", {}).get("content", ""), self.nombre)


class Falso:
    """LLM determinista para tests: aplica una función a (sistema, usuario)."""

    def __init__(self, responder: Callable[[str, str], dict[str, Any]]) -> None:
        self.nombre = "falso"
        self._responder = responder

    def json(self, sistema: str, usuario: str, esquema: dict[str, Any]) -> dict[str, Any]:
        return self._responder(sistema, usuario)


def _parsear_json(contenido: str, origen: str) -> dict[str, Any]:
    try:
        datos = json.loads(contenido)
    except json.JSONDecodeError as e:
        raise LLMError(f"{origen} devolvió JSON inválido: {contenido[:200]!r}") from e
    if not isinstance(datos, dict):
        raise LLMError(f"{origen} devolvió {type(datos).__name__}, se esperaba un objeto")
    return datos


def crear(proveedor: str, modelo: str | None = None) -> LLM:
    if proveedor == "ollama":
        return Ollama(modelo)
    raise ValueError(f"Proveedor desconocido: {proveedor}")
