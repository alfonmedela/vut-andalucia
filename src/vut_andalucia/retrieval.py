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
import threading
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
# Las preguntas reales mezclan varios asuntos. Si son largas, cada frase se busca también por
# separado y se fusiona con menos peso que la pregunta entera.
MIN_PALABRAS_SUBCONSULTAS = 25
PESO_SUBCONSULTA = 0.6


def subconsultas(consulta: str) -> list[str]:
    if len(consulta.split()) < MIN_PALABRAS_SUBCONSULTAS:
        return []
    frases = [f.strip(" ¿¡") for f in re.split(r"(?<=[.?!;])\s+|\n+", consulta)]
    return [f for f in frases if len(f.split()) >= 4][:4]


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


def _titulo(corpus: Corpus, f: Fragmento, alias: dict[str, tuple[str, ...]]) -> str:
    """Título de la norma y cómo la llama la gente («IVA», «LPH»): no está en el articulado."""
    norma = corpus.normas.get(f.norma_id)
    return " ".join([norma.titulo if norma else "", *alias.get(f.norma_id, ())])


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
        # Modo de la última búsqueda, solo para la CLI. En un servidor con peticiones
        # concurrentes se usa el que devuelve `buscar_con_modo`.
        self.ultima_busqueda = "léxica"
        con_vector = {i.split("::", 1)[0] for i in self.denso.ids} if self.denso else set()
        self._sin_vector = sum(f.id not in con_vector for f in corpus.fragmentos)
        if alias is None:
            from vut_andalucia.sources import CATALOGO

            alias = {f.id: f.alias for f in CATALOGO if f.alias}
        self._alias = {
            norma: [re.compile(rf"(?<!\w){re.escape(sin_tildes(a))}(?!\w)") for a in lista]
            for norma, lista in alias.items()
        }
        # Se tokeniza una sola vez; el índice por fecha reutiliza los tokens.
        self._tokens = {
            f.id: tokens(texto_indexable(f, _titulo(corpus, f, alias))) for f in corpus.fragmentos
        }
        self._cache: OrderedDict[dt.date, tuple[list[Fragmento], BM25]] = OrderedDict()
        self._candado = threading.Lock()

    def _indice(self, fecha: dt.date) -> tuple[list[Fragmento], BM25]:
        with self._candado:
            return self._indice_sin_candado(fecha)

    def _indice_sin_candado(self, fecha: dt.date) -> tuple[list[Fragmento], BM25]:
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
        return self.buscar_con_modo(consulta, fecha, k, municipio)[0]

    def buscar_con_modo(
        self, consulta: str, fecha: dt.date, k: int = 8, municipio: str | None = None
    ) -> tuple[list[Resultado], str]:
        """Resultados y el modo de búsqueda que se usó de verdad (híbrida, parcial o léxica)."""
        fragmentos, bm25 = self._indice(fecha)
        mencionadas = self.normas_mencionadas(consulta)
        consultas = [consulta, *subconsultas(consulta)]
        vectores, modo = self._vectores(consultas)
        self.ultima_busqueda = modo
        listas: list[tuple[float, list[int]]] = []
        for n, q in enumerate(consultas):
            peso = 1.0 if n == 0 else PESO_SUBCONSULTA
            puntuaciones = bm25.puntuar(tokens(expandir_consulta(q)))
            puntuaciones = [
                0.0
                if not self.aplica(f, municipio)
                else p * self.REFUERZO_MENCION
                if f.norma_id in mencionadas
                else p
                for f, p in zip(fragmentos, puntuaciones, strict=True)
            ]
            orden = sorted(range(len(fragmentos)), key=lambda i: puntuaciones[i], reverse=True)
            listas.append((peso, [i for i in orden[:CANDIDATOS] if puntuaciones[i] > 0]))
            if vectores is not None:
                listas.append((peso, self._densos(vectores[n], fragmentos, municipio)))
        final: dict[int, float] = {}
        for peso, lista in listas:
            for rango, i in enumerate(lista):
                final[i] = final.get(i, 0.0) + peso / (RRF_K + rango + 1)
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
        return [Resultado(fragmentos[i], final[i]) for i in elegidos], modo

    def _peso(self, f: Fragmento) -> float:
        norma = self.corpus.normas.get(f.norma_id)
        return PESO_CLASE.get(norma.clase, 1.0) if norma else 1.0

    def _vectores(self, consultas: list[str]) -> tuple[npt.NDArray[np.float32] | None, str]:
        if self.denso is None or self.vectorizador is None or not self.denso.ids:
            return None, "léxica (sin índice semántico)"
        try:
            vectores = self.vectorizador.vectores(consultas)
        except EmbeddingsError as e:
            log.warning("Búsqueda semántica no disponible: %s", e)
            return None, "léxica (Ollama no disponible para embeddings)"
        if vectores.shape[1] != self.denso.matriz.shape[1]:
            log.warning("Dimensión de embedding distinta de la del índice: ¿cambió el modelo?")
            return None, "léxica (embeddings incompatibles con el índice)"
        if self._sin_vector:
            return vectores, f"híbrida parcial ({self._sin_vector} fragmentos sin vector)"
        return vectores, "híbrida (BM25 + bge-m3)"

    def _densos(
        self, vector: npt.NDArray[np.float32], fragmentos: list[Fragmento], municipio: str | None
    ) -> list[int]:
        """Índices de `fragmentos` ordenados por similitud semántica, sobre el umbral."""
        assert self.denso is not None
        similitud = self.denso.similitudes(vector)
        candidatos = [
            i
            for i, f in enumerate(fragmentos)
            if similitud.get(f.id, 0.0) >= UMBRAL_DENSO and self.aplica(f, municipio)
        ]
        candidatos.sort(key=lambda i: similitud[fragmentos[i].id], reverse=True)
        return candidatos[:CANDIDATOS]
