"""Proveedores de LLM detrás de una interfaz mínima: devolver JSON que cumpla un esquema."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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


def esquema_estricto(esquema: dict[str, Any]) -> dict[str, Any]:
    """La salida estructurada de Claude exige `additionalProperties: false` en cada objeto."""
    copia: dict[str, Any] = json.loads(json.dumps(esquema))

    def recorre(nodo: Any) -> None:
        if isinstance(nodo, dict):
            if nodo.get("type") == "object":
                nodo.setdefault("additionalProperties", False)
            for v in nodo.values():
                recorre(v)
        elif isinstance(nodo, list):
            for v in nodo:
                recorre(v)

    recorre(copia)
    return copia


class Claude:
    """Claude vía la API de Anthropic, con salida JSON garantizada por esquema.

    El prompt de sistema es estable y va con `cache_control`: cada pregunta solo paga de
    nuevo los fragmentos y la pregunta. Si el modelo rechaza la petición por política, la API
    la reintenta en el modelo de respaldo recomendado (`fallbacks: "default"`).
    """

    def __init__(self, modelo: str | None = None, cliente: Any = None) -> None:
        self.modelo = modelo or os.environ.get("VUT_CLAUDE_MODEL", "claude-opus-5-5")
        self.nombre = f"claude:{self.modelo}"
        self.contexto_amplio = True
        if cliente is None:
            try:
                import anthropic
            except ImportError as e:
                raise LLMError("Para usar Claude instala el extra: `uv sync --extra claude`") from e
            cliente = anthropic.Anthropic(timeout=180, max_retries=2)
        self._cliente = cliente

    def json(self, sistema: str, usuario: str, esquema: dict[str, Any]) -> dict[str, Any]:
        import anthropic

        try:
            r = self._cliente.beta.messages.create(
                model=self.modelo,
                max_tokens=16000,
                system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": usuario}],
                output_config={
                    "effort": os.environ.get("VUT_CLAUDE_EFFORT", "medium"),
                    "format": {"type": "json_schema", "schema": esquema_estricto(esquema)},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise LLMError(
                "Claude rechazó la credencial: define ANTHROPIC_API_KEY o usa `ant auth login`"
            ) from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude: límite de peticiones alcanzado; reintenta en un momento") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude respondió {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"No se pudo conectar con la API de Claude: {e}") from e
        if r.stop_reason == "refusal":
            raise LLMError("Claude declinó responder a esta petición")
        if r.stop_reason == "max_tokens":
            raise LLMError("La respuesta de Claude se cortó por longitud (max_tokens)")
        texto = next((b.text for b in r.content if getattr(b, "type", "") == "text"), "")
        return _parsear_json(texto, self.nombre)


class ClaudeSuscripcion:
    """Claude a través de Claude Code en modo no interactivo (`claude -p`).

    Usa la sesión iniciada en Claude Code (suscripción Pro o Max), sin clave de API: pensado
    para uso local y para evaluar. Sin herramientas, sin personalizaciones (`--safe-mode`) y
    sin guardar la sesión; la salida se valida contra el esquema (`--json-schema`).
    """

    def __init__(self, modelo: str | None = None, timeout: float = 300) -> None:
        self.modelo = modelo or os.environ.get("VUT_CLAUDE_CLI_MODEL", "opus")
        self.nombre = f"claude-suscripcion:{self.modelo}"
        self.contexto_amplio = True
        self.timeout = timeout
        self._ejecutable = shutil.which("claude")
        if self._ejecutable is None:
            raise LLMError("No encuentro `claude` en el PATH: instala Claude Code e inicia sesión")

    def json(self, sistema: str, usuario: str, esquema: dict[str, Any]) -> dict[str, Any]:
        assert self._ejecutable is not None
        orden = [
            self._ejecutable, "-p", "--safe-mode", "--no-session-persistence",
            "--tools", "", "--output-format", "json", "--model", self.modelo,
            "--effort", os.environ.get("VUT_CLAUDE_EFFORT", "medium"),
            "--system-prompt", sistema,
            "--json-schema", json.dumps(esquema_estricto(esquema), ensure_ascii=False),
        ]  # fmt: skip
        try:
            proceso = subprocess.run(
                orden, input=usuario, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=self.timeout, check=False,
            )  # fmt: skip
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"Claude Code no respondió en {self.timeout:.0f} s") from e
        except OSError as e:
            raise LLMError(f"No se pudo ejecutar Claude Code: {e}") from e
        try:
            salida = json.loads(proceso.stdout)
        except json.JSONDecodeError as e:
            detalle = (proceso.stderr or proceso.stdout)[:300]
            raise LLMError(f"Claude Code falló (código {proceso.returncode}): {detalle}") from e
        if not isinstance(salida, dict) or salida.get("is_error"):
            error = salida.get("result") if isinstance(salida, dict) else salida
            raise LLMError(f"Claude Code devolvió un error: {str(error)[:300]}")
        datos = salida.get("structured_output")
        if isinstance(datos, dict):
            return datos
        return _parsear_json(str(salida.get("result", "")), self.nombre)


class Falso:
    """LLM determinista para tests: aplica una función a (sistema, usuario).

    La revisión de significado (esquema con `juicios`) la atiende `revisor`; si no se da, da
    por respaldadas todas las afirmaciones."""

    def __init__(
        self,
        responder: Callable[[str, str], dict[str, Any]],
        revisor: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.nombre = "falso"
        self._responder = responder
        self._revisor = revisor

    def json(self, sistema: str, usuario: str, esquema: dict[str, Any]) -> dict[str, Any]:
        if "juicios" in esquema.get("properties", {}):
            if self._revisor is not None:
                return self._revisor(sistema, usuario)
            n = usuario.count("<afirmacion n=")
            return {
                "juicios": [{"n": i, "respaldada": True, "motivo": ""} for i in range(1, n + 1)]
            }
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
    if proveedor == "claude":
        return Claude(modelo)
    if proveedor == "suscripcion":
        return ClaudeSuscripcion(modelo)
    raise ValueError(f"Proveedor desconocido: {proveedor}")
