"""Montaje compartido por la CLI y la API: corpus, búsqueda híbrida y municipios."""

from __future__ import annotations

import os
from pathlib import Path

from vut_andalucia import corpus as corpus_mod
from vut_andalucia import embeddings, municipios
from vut_andalucia.retrieval import Buscador
from vut_andalucia.text import sin_tildes


def crear_buscador(
    directorio: Path = corpus_mod.DIR_POR_DEFECTO, semantico: bool = True
) -> Buscador:
    corpus = corpus_mod.cargar(directorio)
    if not semantico:
        return Buscador(corpus)
    vectorizador: embeddings.ClienteOllama | embeddings.VectorizadorConCache
    cache = os.environ.get("VUT_CACHE_CONSULTAS")
    if cache:
        # CI y reproducibilidad: vectores de consultas grabados, sin Ollama.
        vectorizador = embeddings.VectorizadorConCache(Path(cache))
    else:
        vectorizador = embeddings.ClienteOllama()
    return Buscador(corpus, denso=embeddings.cargar(directorio), vectorizador=vectorizador)


def resolver_municipio(valor: str) -> str:
    """Acepta el slug, el nombre con o sin tildes o el código INE; devuelve el slug."""
    plano = sin_tildes(valor.strip().lower())
    for m in municipios.todos():
        if plano in (m.slug, m.ine, sin_tildes(m.nombre.lower())):
            return m.slug
    candidatos = [m.nombre for m in municipios.todos() if plano in sin_tildes(m.nombre.lower())]
    sugerencia = f" ¿Quizá {', '.join(candidatos[:4])}?" if candidatos else ""
    raise ValueError(f"No conozco el municipio andaluz {valor!r}.{sugerencia}")
