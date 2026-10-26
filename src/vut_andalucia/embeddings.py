"""Búsqueda semántica: embeddings bge-m3 (multilingüe, local vía Ollama) de cada fragmento.

BM25 encuentra «tres quintas partes»; los embeddings encuentran «¿pueden los vecinos echarme?»
aunque no comparta palabras con la ley. Los vectores del corpus se calculan una vez
(`vut embed`) y se guardan junto al corpus; en cada pregunta solo se calcula el de la pregunta.
Si Ollama no está disponible, la búsqueda sigue siendo léxica y la respuesta lo indica.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import numpy.typing as npt

from vut_andalucia.corpus import DIR_POR_DEFECTO, Corpus
from vut_andalucia.models import Fragmento

MODELO = os.environ.get("VUT_EMBED_MODEL", "bge-m3")
MAX_CHARS = 2000  # bge-m3 admite 8k tokens, pero el sentido de un apartado está al principio

log = logging.getLogger(__name__)


class EmbeddingsError(RuntimeError):
    pass


def texto_para_embedding(f: Fragmento) -> str:
    return f"{f.cita}. {f.encabezado}\n{f.texto}"[:MAX_CHARS]


def huella(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()[:16]


class ClienteOllama:
    def __init__(self, url: str | None = None, modelo: str = MODELO, timeout: float = 60) -> None:
        self.url = (url or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
        self.modelo = modelo
        self._client = httpx.Client(timeout=timeout)

    def vectores(self, textos: list[str]) -> npt.NDArray[np.float32]:
        try:
            r = self._client.post(
                f"{self.url}/api/embed", json={"model": self.modelo, "input": textos}
            )
        except httpx.HTTPError as e:
            raise EmbeddingsError(f"Ollama no responde en {self.url}: {e}") from e
        if r.status_code != 200:
            raise EmbeddingsError(f"Ollama respondió {r.status_code}: {r.text[:200]}")
        datos = r.json().get("embeddings")
        if not isinstance(datos, list) or len(datos) != len(textos):
            raise EmbeddingsError("Ollama devolvió un número de vectores distinto del pedido")
        m = np.asarray(datos, dtype=np.float32)
        normas = np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        return (m / normas).astype(np.float32)


@dataclass
class IndiceDenso:
    ids: list[str]
    matriz: npt.NDArray[np.float32]  # filas normalizadas
    modelo: str
    huellas: dict[str, str]

    def vigente_para(self, corpus: Corpus) -> IndiceDenso:
        """Solo los vectores cuyo texto sigue siendo el del corpus; avisa de los demás."""
        actuales = {f.id: huella(texto_para_embedding(f)) for f in corpus.fragmentos}
        filas = [n for n, i in enumerate(self.ids) if actuales.get(i) == self.huellas.get(i)]
        faltan = len(actuales) - len(filas)
        if faltan:
            log.warning("%d fragmentos sin embedding al día: ejecuta `vut embed`", faltan)
        return IndiceDenso(
            ids=[self.ids[n] for n in filas],
            matriz=self.matriz[filas],
            modelo=self.modelo,
            huellas={self.ids[n]: self.huellas[self.ids[n]] for n in filas},
        )

    def similitudes(self, vector: npt.NDArray[np.float32]) -> dict[str, float]:
        puntos = self.matriz @ vector
        return dict(zip(self.ids, puntos.tolist(), strict=True))


def _rutas(directorio: Path, modelo: str) -> tuple[Path, Path]:
    base = directorio / f"embeddings-{modelo.replace(':', '-')}"
    return base.with_suffix(".npy"), base.with_suffix(".json")


def construir(
    corpus: Corpus,
    cliente: ClienteOllama,
    directorio: Path = DIR_POR_DEFECTO,
    lote: int = 32,
) -> int:
    """Calcula (o reutiliza) los vectores de todos los fragmentos. Devuelve cuántos calculó."""
    previo = cargar(directorio, cliente.modelo, avisar=False)
    reutilizables: dict[str, npt.NDArray[np.float32]] = {}
    huellas_previas: dict[str, str] = {}
    _, ruta_meta = _rutas(directorio, cliente.modelo)
    if previo is not None and ruta_meta.exists():
        huellas_previas = json.loads(ruta_meta.read_text(encoding="utf-8"))["huellas"]
        reutilizables = {i: previo.matriz[n] for n, i in enumerate(previo.ids)}
    ids, filas, huellas = [], [], {}
    pendientes: list[tuple[int, str]] = []
    for f in corpus.fragmentos:
        texto = texto_para_embedding(f)
        h = huella(texto)
        ids.append(f.id)
        huellas[f.id] = h
        if f.id in reutilizables and huellas_previas.get(f.id) == h:
            filas.append(reutilizables[f.id])
        else:
            filas.append(np.zeros(0, dtype=np.float32))
            pendientes.append((len(filas) - 1, texto))
    for i in range(0, len(pendientes), lote):
        trozo = pendientes[i : i + lote]
        vectores = cliente.vectores([t for _, t in trozo])
        for (pos, _), v in zip(trozo, vectores, strict=True):
            filas[pos] = v
    matriz = np.vstack(filas).astype(np.float16) if filas else np.zeros((0, 0), np.float16)
    ruta_npy, ruta_meta = _rutas(directorio, cliente.modelo)
    np.save(ruta_npy, matriz)
    ruta_meta.write_text(
        json.dumps({"modelo": cliente.modelo, "ids": ids, "huellas": huellas}, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(pendientes)


def cargar(
    directorio: Path = DIR_POR_DEFECTO, modelo: str = MODELO, avisar: bool = True
) -> IndiceDenso | None:
    ruta_npy, ruta_meta = _rutas(directorio, modelo)
    if not ruta_npy.exists() or not ruta_meta.exists():
        if avisar:
            log.warning("No hay embeddings %s en %s; búsqueda solo léxica", modelo, directorio)
        return None
    meta = json.loads(ruta_meta.read_text(encoding="utf-8"))
    matriz = np.load(ruta_npy).astype(np.float32)
    if matriz.shape[0] != len(meta["ids"]):
        raise EmbeddingsError(
            f"{ruta_npy.name}: {matriz.shape[0]} vectores para {len(meta['ids'])} ids"
        )
    return IndiceDenso(ids=meta["ids"], matriz=matriz, modelo=modelo, huellas=meta["huellas"])


def fragmentos_sin_vector(corpus: Corpus, indice: IndiceDenso) -> list[str]:
    conocidos = set(indice.ids)
    return [f.id for f in corpus.fragmentos if f.id not in conocidos]


class VectorizadorConCache:
    """Vectores de consultas guardados en disco, para que tests y CI usen la búsqueda híbrida
    sin Ollama. Con `grabar=True` calcula y guarda los que falten; sin él, una consulta no
    grabada lanza `EmbeddingsError` y la búsqueda vuelve a ser léxica (y lo dice)."""

    def __init__(self, ruta: Path, cliente: ClienteOllama | None = None, grabar: bool = False):
        self.ruta = ruta
        self.cliente = cliente
        self.grabar = grabar
        self._cache: dict[str, list[float]] = (
            json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
        )

    def vectores(self, textos: list[str]) -> npt.NDArray[np.float32]:
        faltan = [t for t in textos if t not in self._cache]
        if faltan:
            if not (self.grabar and self.cliente):
                raise EmbeddingsError(f"{len(faltan)} consulta(s) sin vector grabado en caché")
            for t, v in zip(faltan, self.cliente.vectores(faltan), strict=True):
                self._cache[t] = [round(float(x), 5) for x in v]
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            self.ruta.write_text(
                json.dumps(self._cache, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
        return np.asarray([self._cache[t] for t in textos], dtype=np.float32)
