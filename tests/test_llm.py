from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from vut_andalucia.llm import LLMError, Ollama


def _ollama(handler: Any) -> Ollama:
    o = Ollama(modelo="qwen3:8b", url="http://ollama.test")
    o._client = httpx.Client(transport=httpx.MockTransport(handler))
    return o


def test_ollama_envia_esquema_y_parsea_json() -> None:
    enviado: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        enviado.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"afirmaciones": []}'}})

    assert _ollama(handler).json("s", "u", {"type": "object"}) == {"afirmaciones": []}
    assert enviado["format"] == {"type": "object"} and enviado["options"]["temperature"] == 0
    assert enviado["think"] is False


@pytest.mark.parametrize(
    ("respuesta", "mensaje"),
    [
        (httpx.Response(500, text="boom"), "500"),
        (httpx.Response(200, json={"message": {"content": "no es json"}}), "JSON inválido"),
        (httpx.Response(200, json={"message": {"content": "[1, 2]"}}), "se esperaba un objeto"),
        (httpx.Response(200, text="<html>proxy</html>"), "no es JSON"),
        (
            httpx.Response(200, json={"done_reason": "length", "message": {"content": "{"}}),
            "se cortó",
        ),
    ],
)
def test_ollama_errores_explicitos(respuesta: httpx.Response, mensaje: str) -> None:
    with pytest.raises(LLMError, match=mensaje):
        _ollama(lambda _r: respuesta).json("s", "u", {})


def test_ollama_sin_servidor_explica_que_hacer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMError, match="ollama serve"):
        _ollama(handler).json("s", "u", {})


def test_ollama_timeout_es_llmerror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    with pytest.raises(LLMError, match="ReadTimeout"):
        _ollama(handler).json("s", "u", {})
