from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vut_andalucia import api
from vut_andalucia.llm import Falso, LLMError
from vut_andalucia.retrieval import Buscador


@pytest.fixture()
def cliente(buscador: Buscador, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api, "buscador", lambda: buscador)
    return TestClient(api.app)


def _eventos(texto: str) -> dict[str, Any]:
    return {
        m.group(1): json.loads(m.group(2))
        for m in re.finditer(r"event: (\w+)\ndata: (.*)\n\n", texto)
    }


def _llm_17_12(_s: str, usuario: str) -> dict[str, Any]:
    etiqueta = re.search(r'etiqueta="(F\d+)" cita="art\. 17\.12 LPH"', usuario)
    assert etiqueta
    return {
        "afirmaciones": [
            {
                "texto": "Hace falta el voto de tres quintas partes.",
                "tipo": "regla",
                "fuente": etiqueta.group(1),
                "cita_literal": "requerirá el voto favorable de las tres quintas partes del total",
            }
        ],
        "preguntas_pendientes": [],
        "fuera_de_ambito": False,
    }


def test_preguntar_envia_fuentes_y_luego_respuesta(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "llm", lambda: Falso(_llm_17_12))
    r = cliente.post(
        "/api/preguntar",
        json={"pregunta": "mayoría junta prohibir pisos turísticos", "fecha": "2026-10-26"},
    )
    assert r.status_code == 200
    ev = _eventos(r.text)
    assert list(ev) == ["fuentes", "respuesta"]
    assert ev["fuentes"]["fragmentos"]
    (a,) = ev["respuesta"]["afirmaciones"]
    assert a["fragmento"]["cita"] == "art. 17.12 LPH" and a["fragmento"]["norma"]["corta"] == "LPH"


def test_preguntar_sin_modelo_es_solo_busqueda(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "llm", lambda: None)
    ev = _eventos(cliente.post("/api/preguntar", json={"pregunta": "piso turístico vecinos"}).text)
    assert "solo_busqueda" in ev and "fuentes" in ev


def test_error_del_modelo_se_comunica(cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def falla(*_: Any) -> dict[str, Any]:
        raise LLMError("Ollama no responde")

    monkeypatch.setattr(api, "llm", lambda: Falso(falla))
    ev = _eventos(cliente.post("/api/preguntar", json={"pregunta": "piso turístico vecinos"}).text)
    assert ev["error"]["mensaje"] == "Ollama no responde"


def test_pregunta_bloqueada_no_usa_modelo_ni_cupo(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explota(*_: Any) -> dict[str, Any]:
        raise AssertionError("no debe llamarse al modelo")

    def sin_cupo() -> bool:
        raise AssertionError("no debe gastarse cupo")

    monkeypatch.setattr(api, "llm", lambda: Falso(explota))
    monkeypatch.setattr(api.cupo, "consumir", sin_cupo)
    pregunta = {"pregunta": "Tengo un piso en Barcelona, ¿qué hago?"}
    ev = _eventos(cliente.post("/api/preguntar", json=pregunta).text)
    assert ev["respuesta"]["motivo_abstencion"] == "otro_territorio"
    assert ev["fuentes"]["fragmentos"] == []


def test_validacion_de_entrada(cliente: TestClient) -> None:
    assert cliente.post("/api/preguntar", json={"pregunta": "x"}).status_code == 422
    r = cliente.post("/api/preguntar", json={"pregunta": "¿Puedo?", "municipio": "Narnia"})
    assert r.status_code == 422 and "Narnia" in r.json()["detail"]


def test_limite_por_ip() -> None:
    lim = api.LimitePorIP(2)
    assert lim.permitir("1.1.1.1") and lim.permitir("1.1.1.1")
    assert not lim.permitir("1.1.1.1") and lim.permitir("2.2.2.2")


def test_fragmento_con_linea_temporal(cliente: TestClient) -> None:
    r = cliente.get("/api/fragmento/BOE-A-1960-10906#adiecisiete.12@2025-04-03")
    assert r.status_code == 404  # «#» separa el fragmento de la URL si no se codifica
    r = cliente.get("/api/fragmento/BOE-A-1960-10906%23adiecisiete.12%402025-04-03")
    datos = r.json()
    assert r.status_code == 200 and len(datos["versiones"]) >= 3
    assert datos["versiones"][0]["vigente_desde"] < datos["vigente_desde"]


def test_fuentes_municipios_y_portada(cliente: TestClient) -> None:
    fuentes = cliente.get("/api/fuentes").json()
    assert any(f["territorio"] == "municipal" for f in fuentes["fuentes"])
    mun = cliente.get("/api/municipios").json()
    assert len(mun) == 785 and any(m["con_normas"] and m["slug"] == "malaga" for m in mun)
    portada = cliente.get("/")
    assert portada.status_code == 200 and "Pregunta a la ley" in portada.text
    # Los recursos llevan la huella de su contenido para que la caché no sirva versiones viejas.
    assert re.search(r'/static/app\.js\?v=[0-9a-f]{10}"', portada.text)
    assert re.search(r'/static/app\.css\?v=[0-9a-f]{10}"', portada.text)
    assert portada.headers["cache-control"] == "no-cache"
