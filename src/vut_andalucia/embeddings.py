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


def contexto_de(corpus: Corpus) -> dict[str, str]:
    """Por norma: abreviatura, título y alias. El articulado no dice «IVA» ni «pisos
    turísticos», pero quien pregunta sí; sin esto el vector no lo relaciona."""
    from vut_andalucia.sources import CATALOGO

    alias = {f.id: f.alias for f in CATALOGO}
    return {
        n.id: f"{n.corta} ({', '.join(alias.get(n.id, ()))}). {n.titulo}"
        for n in corpus.normas.values()
    }


def texto_para_embedding(f: Fragmento, contexto: str = "") -> str:
    return f"{contexto}\n{f.cita}. {f.encabezado}\n{' › '.join(f.ruta)}\n{f.texto}"


TRAMO = 1400  # caracteres de texto por vector en fragmentos largos
SOLAPE = 300


def tramos(texto: str) -> list[str]:
    """Un fragmento largo se vectoriza por tramos solapados: así lo que está al final (p. ej. la
    regla de las 30 noches del art. 20.Uno.23.º LIVA) no queda fuera de lo que ve el modelo.
    Cada tramo lleva la cabecera (norma, cita, rúbrica) para no perder el contexto."""
    if len(texto) <= MAX_CHARS:
        return [texto]
    lineas = texto.split("\n")
    cabecera = "\n".join(lineas[:3])
    cuerpo = "\n".join(lineas[3:])
    salida, i = [], 0
    while i < len(cuerpo):
        salida.append(f"{cabecera}\n{cuerpo[i : i + TRAMO]}")
        if i + TRAMO >= len(cuerpo):
            break
        i += TRAMO - SOLAPE
    return salida


def huella(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()[:16]


class ClienteOllama:
    def __init__(self, url: str | None = None, modelo: str = MODELO, timeout: float = 15) -> None:
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
        try:
            datos = r.json().get("embeddings")
        except ValueError as e:
            raise EmbeddingsError(f"Ollama devolvió una respuesta que no es JSON: {e}") from e
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
        ctx = contexto_de(corpus)
        actuales = {
            f.id: huella(texto_para_embedding(f, ctx.get(f.norma_id, "")))
            for f in corpus.fragmentos
        }

        def base(i: str) -> str:
            return i.split("::", 1)[0]

        filas = [
            n for n, i in enumerate(self.ids) if actuales.get(base(i)) == self.huellas.get(base(i))
        ]
        faltan = len(actuales) - len({base(self.ids[n]) for n in filas})
        if faltan:
            log.warning("%d fragmentos sin embedding al día: ejecuta `vut embed`", faltan)
        return IndiceDenso(
            ids=[self.ids[n] for n in filas],
            matriz=self.matriz[filas],
            modelo=self.modelo,
            huellas={base(self.ids[n]): self.huellas[base(self.ids[n])] for n in filas},
        )

    def similitudes(self, vector: npt.NDArray[np.float32]) -> dict[str, float]:
        """Similitud por fragmento: la del mejor de sus tramos («id::n» → «id»)."""
        puntos = self.matriz @ vector
        salida: dict[str, float] = {}
        for ident, p in zip(self.ids, puntos.tolist(), strict=True):
            base = ident.split("::", 1)[0]
            if p > salida.get(base, -1.0):
                salida[base] = p
        return salida


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
    ids: list[str] = []
    filas: list[npt.NDArray[np.float32]] = []
    huellas: dict[str, str] = {}
    pendientes: list[tuple[int, str]] = []
    ctx = contexto_de(corpus)
    for f in corpus.fragmentos:
        texto = texto_para_embedding(f, ctx.get(f.norma_id, ""))
        h = huella(texto)
        huellas[f.id] = h
        partes = tramos(texto)
        for n, parte in enumerate(partes):
            ident = f.id if len(partes) == 1 else f"{f.id}::{n}"
            ids.append(ident)
            if ident in reutilizables and huellas_previas.get(f.id) == h:
                filas.append(reutilizables[ident])
            else:
                filas.append(np.zeros(0, dtype=np.float32))
                pendientes.append((len(filas) - 1, parte))
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
    conocidos = {i.split("::", 1)[0] for i in indice.ids}
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
        claves = [f"{MODELO}::{t}" for t in textos]
        faltan = [t for t, c in zip(textos, claves, strict=True) if c not in self._cache]
        if faltan:
            if not (self.grabar and self.cliente):
                raise EmbeddingsError(f"{len(faltan)} consulta(s) sin vector grabado en caché")
            for t, v in zip(faltan, self.cliente.vectores(faltan), strict=True):
                self._cache[f"{MODELO}::{t}"] = [round(float(x), 5) for x in v]
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            self.ruta.write_text(
                json.dumps(self._cache, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
        return np.asarray([self._cache[c] for c in claves], dtype=np.float32)
