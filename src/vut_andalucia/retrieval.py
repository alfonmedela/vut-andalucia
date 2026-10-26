"""Recuperación léxica BM25 sobre los fragmentos vigentes en una fecha.

El corpus son unos cientos de artículos vigentes: un índice en memoria se construye en
milisegundos y no necesita base de datos. El índice se calcula por fecha para que las
estadísticas (IDF) reflejen solo el derecho vigente ese día.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from vut_andalucia.corpus import Corpus
from vut_andalucia.embeddings import EmbeddingsError, IndiceDenso
from vut_andalucia.glossary import alias_indexacion, expandir_consulta
from vut_andalucia.models import Ambito, Clase, Fragmento
from vut_andalucia.text import sin_tildes, tokens

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resultado:
    fragmento: Fragmento
    puntuacion: float


class Vectorizador(Protocol):
    def vectores(self, textos: list[str]) -> npt.NDArray[np.float32]: ...


RRF_K = 60  # constante de Reciprocal Rank Fusion (Cormack et al., 2009)
# A igualdad de pertinencia, manda la norma; la sentencia la interpreta y el criterio
# administrativo solo orienta.
PESO_CLASE = {Clase.NORMA: 1.0, Clase.JURISPRUDENCIA: 0.9, Clase.DOCTRINA: 0.8}
MAX_POR_FUENTE = 3  # diversidad: que una sola fuente no acapare el contexto
CANDIDATOS = 50  # cuántos de cada lista entran en la fusión
# Similitud coseno mínima (bge-m3) para que un fragmento entre por la vía semántica. Calibrado:
# preguntas sin relación con el tema no pasan de ~0,40; las preguntas reales superan 0,45.
UMBRAL_DENSO = 0.45


def texto_indexable(f: Fragmento, titulo_norma: str = "") -> str:
    """Lo que ve BM25: cita, título de la norma, rúbricas, texto y alias del glosario."""
    return " ".join(
        [f.cita, titulo_norma, f.encabezado, *f.ruta, f.texto, alias_indexacion(f.texto)]
    )


class BM25:
    def __init__(self, documentos: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.frecuencias = [Counter(d) for d in documentos]
        self.longitudes = [len(d) for d in documentos]
        self.media = sum(self.longitudes) / max(len(documentos), 1) or 1.0
        df: Counter[str] = Counter()
        for d in self.frecuencias:
            df.update(d.keys())
        n = len(documentos)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def puntuar(self, consulta: list[str]) -> list[float]:
        terminos = [t for t in dict.fromkeys(consulta) if t in self.idf]
        salida = []
        for tf, largo in zip(self.frecuencias, self.longitudes, strict=True):
            norma = self.k1 * (1 - self.b + self.b * largo / self.media)
            salida.append(
                sum(
                    self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norma)
                    for t in terminos
                    if t in tf
                )
            )
        return salida


def _titulo(corpus: Corpus, f: Fragmento) -> str:
    norma = corpus.normas.get(f.norma_id)
    return norma.titulo if norma else ""


class Buscador:
    MAX_FECHAS_EN_CACHE = 32
    # Multiplicador de la puntuación de los fragmentos de una norma que la pregunta nombra.
    REFUERZO_MENCION = 1.6

    def __init__(
        self,
        corpus: Corpus,
        alias: dict[str, tuple[str, ...]] | None = None,
        denso: IndiceDenso | None = None,
        vectorizador: Vectorizador | None = None,
    ) -> None:
        self.corpus = corpus
        self.denso = denso.vigente_para(corpus) if denso is not None else None
        self.vectorizador = vectorizador
        # Qué búsqueda se usó en la última consulta: «híbrida» o «léxica» (y por qué).
        self.ultima_busqueda = "léxica"
        if alias is None:
            from vut_andalucia.sources import CATALOGO

            alias = {f.id: f.alias for f in CATALOGO if f.alias}
        self._alias = {
            norma: [re.compile(rf"(?<!\w){re.escape(sin_tildes(a))}(?!\w)") for a in lista]
            for norma, lista in alias.items()
        }
        # Se tokeniza una sola vez; el índice por fecha reutiliza los tokens.
        self._tokens = {
            f.id: tokens(texto_indexable(f, _titulo(corpus, f))) for f in corpus.fragmentos
        }
        self._cache: OrderedDict[dt.date, tuple[list[Fragmento], BM25]] = OrderedDict()

    def _indice(self, fecha: dt.date) -> tuple[list[Fragmento], BM25]:
        if fecha in self._cache:
            self._cache.move_to_end(fecha)
        else:
            vigentes = self.corpus.vigentes(fecha)
            self._cache[fecha] = (vigentes, BM25([self._tokens[f.id] for f in vigentes]))
            if len(self._cache) > self.MAX_FECHAS_EN_CACHE:
                self._cache.popitem(last=False)
        return self._cache[fecha]

    def normas_mencionadas(self, consulta: str) -> set[str]:
        plano = sin_tildes(consulta.lower())
        return {n for n, patrones in self._alias.items() if any(p.search(plano) for p in patrones)}

    def aplica(self, f: Fragmento, municipio: str | None) -> bool:
        """Las normas municipales solo cuentan para su municipio; el resto, siempre."""
        norma = self.corpus.normas.get(f.norma_id)
        if norma is None or norma.territorio != Ambito.MUNICIPAL:
            return True
        return municipio is not None and norma.municipio == municipio

    def buscar(
        self, consulta: str, fecha: dt.date, k: int = 8, municipio: str | None = None
    ) -> list[Resultado]:
        fragmentos, bm25 = self._indice(fecha)
        puntuaciones = bm25.puntuar(tokens(expandir_consulta(consulta)))
        puntuaciones = [
            p if self.aplica(f, municipio) else 0.0
            for f, p in zip(fragmentos, puntuaciones, strict=True)
        ]
        mencionadas = self.normas_mencionadas(consulta)
        if mencionadas:
            puntuaciones = [
                p * self.REFUERZO_MENCION if f.norma_id in mencionadas else p
                for f, p in zip(fragmentos, puntuaciones, strict=True)
            ]
        orden = sorted(range(len(fragmentos)), key=lambda i: puntuaciones[i], reverse=True)
        lexicos = [i for i in orden[:CANDIDATOS] if puntuaciones[i] > 0]
        densos = self._densos(consulta, fragmentos, municipio)
        if densos is None:
            final = {i: puntuaciones[i] * self._peso(fragmentos[i]) for i in lexicos}
        else:
            final = {}
            for lista in (lexicos, densos):
                for rango, i in enumerate(lista):
                    final[i] = final.get(i, 0.0) + 1.0 / (RRF_K + rango + 1)
            final = {i: v * self._peso(fragmentos[i]) for i, v in final.items()}
        elegidos: list[int] = []
        por_fuente: dict[str, int] = {}
        for i in sorted(final, key=lambda i: final[i], reverse=True):
            fuente = fragmentos[i].norma_id
            if por_fuente.get(fuente, 0) >= MAX_POR_FUENTE:
                continue
            por_fuente[fuente] = por_fuente.get(fuente, 0) + 1
            elegidos.append(i)
            if len(elegidos) == k:
                break
        return [Resultado(fragmentos[i], final[i]) for i in elegidos]

    def _peso(self, f: Fragmento) -> float:
        norma = self.corpus.normas.get(f.norma_id)
        return PESO_CLASE.get(norma.clase, 1.0) if norma else 1.0

    def _densos(
        self, consulta: str, fragmentos: list[Fragmento], municipio: str | None
    ) -> list[int] | None:
        """Índices de `fragmentos` ordenados por similitud semántica, o None si no hay."""
        if self.denso is None or self.vectorizador is None:
            self.ultima_busqueda = "léxica (sin índice semántico)"
            return None
        try:
            vector = self.vectorizador.vectores([consulta])[0]
        except EmbeddingsError as e:
            log.warning("Búsqueda semántica no disponible: %s", e)
            self.ultima_busqueda = "léxica (Ollama no disponible para embeddings)"
            return None
        similitud = self.denso.similitudes(vector)
        candidatos = [
            i
            for i, f in enumerate(fragmentos)
            if similitud.get(f.id, 0.0) >= UMBRAL_DENSO and self.aplica(f, municipio)
        ]
        candidatos.sort(key=lambda i: similitud[fragmentos[i].id], reverse=True)
        self.ultima_busqueda = "híbrida (BM25 + bge-m3)"
        return candidatos[:CANDIDATOS]
