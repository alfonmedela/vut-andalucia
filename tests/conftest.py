from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vut_andalucia import corpus as corpus_mod
from vut_andalucia import embeddings
from vut_andalucia.ingest import boe
from vut_andalucia.models import Fragmento, Norma
from vut_andalucia.retrieval import Buscador

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def norma_lph() -> Norma:
    meta = json.loads((FIXTURES / "boe_lph_metadatos.json").read_text(encoding="utf-8"))["data"][0]
    return boe.norma_desde_metadatos(meta, "LPH")


@pytest.fixture(scope="session")
def fragmentos_lph(norma_lph: Norma) -> list[Fragmento]:
    return boe.parsear((FIXTURES / "boe_lph_extracto.xml").read_bytes(), norma_lph)


@pytest.fixture(scope="session")
def corpus_real() -> corpus_mod.Corpus:
    """Corpus publicado con el paquete (src/vut_andalucia/data/corpus)."""
    return corpus_mod.cargar()


CACHE_CONSULTAS = Path(__file__).parent / "fixtures" / "consultas-bge-m3.json"


@pytest.fixture(scope="session")
def buscador(corpus_real: corpus_mod.Corpus) -> Buscador:
    """Búsqueda híbrida, como en producción, con los vectores de las consultas en caché.

    `VUT_GRABAR_CONSULTAS=1 uv run pytest` calcula (con Ollama) los que falten.
    """
    grabar = os.environ.get("VUT_GRABAR_CONSULTAS") == "1"
    return Buscador(
        corpus_real,
        denso=embeddings.cargar(),
        vectorizador=embeddings.VectorizadorConCache(
            CACHE_CONSULTAS, embeddings.ClienteOllama() if grabar else None, grabar=grabar
        ),
    )


@pytest.fixture(scope="session")
def buscador_lexico(corpus_real: corpus_mod.Corpus) -> Buscador:
    return Buscador(corpus_real)
