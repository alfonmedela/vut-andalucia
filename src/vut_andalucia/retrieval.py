"""Recuperación léxica BM25 sobre los fragmentos vigentes en una fecha.

El corpus son unos cientos de artículos vigentes: un índice en memoria se construye en
milisegundos y no necesita base de datos. El índice se calcula por fecha para que las
estadísticas (IDF) reflejen solo el derecho vigente ese día.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter, OrderedDict
from dataclasses import dataclass

from vut_andalucia.corpus import Corpus
from vut_andalucia.glossary import alias_indexacion, expandir_consulta
from vut_andalucia.models import Fragmento
from vut_andalucia.text import tokens


@dataclass(frozen=True)
class Resultado:
    fragmento: Fragmento
    puntuacion: float


def texto_indexable(f: Fragmento) -> str:
    return " ".join([f.cita, f.encabezado, *f.ruta, f.texto, alias_indexacion(f.texto)])


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


class Buscador:
    MAX_FECHAS_EN_CACHE = 32

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        # Se tokeniza una sola vez; el índice por fecha reutiliza los tokens.
        self._tokens = {f.id: tokens(texto_indexable(f)) for f in corpus.fragmentos}
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

    def buscar(self, consulta: str, fecha: dt.date, k: int = 8) -> list[Resultado]:
        fragmentos, bm25 = self._indice(fecha)
        puntuaciones = bm25.puntuar(tokens(expandir_consulta(consulta)))
        orden = sorted(range(len(fragmentos)), key=lambda i: puntuaciones[i], reverse=True)
        return [Resultado(fragmentos[i], puntuaciones[i]) for i in orden[:k] if puntuaciones[i] > 0]
