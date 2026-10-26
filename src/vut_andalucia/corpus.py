"""Corpus en disco: un JSONL por norma y un manifiesto con la procedencia de cada texto."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from vut_andalucia.models import Fragmento, Norma

DIR_POR_DEFECTO = Path(__file__).parent / "data" / "corpus"
MANIFIESTO = "manifest.json"


class EntradaManifiesto(BaseModel):
    norma: Norma
    sha256: str
    descargado: dt.datetime
    fragmentos: int


@dataclass
class Corpus:
    normas: dict[str, Norma] = field(default_factory=dict)
    fragmentos: list[Fragmento] = field(default_factory=list)

    def por_id(self, fragmento_id: str) -> Fragmento:
        for f in self.fragmentos:
            if f.id == fragmento_id:
                return f
        raise KeyError(fragmento_id)

    def vigentes(self, fecha: dt.date) -> list[Fragmento]:
        return [f for f in self.fragmentos if f.vigente_en(fecha)]


def sha256(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def _escribir_atomico(ruta: Path, contenido: str) -> None:
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(contenido, encoding="utf-8")
    tmp.replace(ruta)


def guardar(
    directorio: Path,
    lote: list[tuple[Norma, list[Fragmento], bytes]],
    ahora: dt.datetime | None = None,
    catalogo: set[str] | None = None,
) -> list[EntradaManifiesto]:
    """Escribe varias normas y actualiza el manifiesto al final.

    Se llama con todo el lote ya descargado y parseado: si una norma falla, no se toca nada.
    """
    directorio.mkdir(parents=True, exist_ok=True)
    momento = ahora or dt.datetime.now(dt.UTC).replace(microsecond=0)
    manifiesto = leer_manifiesto(directorio)
    if catalogo is not None:
        # Una fuente que sale del catálogo sale también del corpus.
        for retirada in set(manifiesto) - catalogo:
            (directorio / f"{retirada}.jsonl").unlink(missing_ok=True)
            del manifiesto[retirada]
    entradas = []
    for norma, fragmentos, bruto in lote:
        _escribir_atomico(
            directorio / f"{norma.id}.jsonl",
            "".join(f.model_dump_json() + "\n" for f in fragmentos),
        )
        entrada = EntradaManifiesto(
            norma=norma, sha256=sha256(bruto), descargado=momento, fragmentos=len(fragmentos)
        )
        manifiesto[norma.id] = entrada
        entradas.append(entrada)
    _escribir_atomico(
        directorio / MANIFIESTO,
        json.dumps(
            {k: v.model_dump(mode="json") for k, v in sorted(manifiesto.items())},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return entradas


class CorpusCorrupto(RuntimeError):
    pass


def leer_manifiesto(directorio: Path) -> dict[str, EntradaManifiesto]:
    ruta = directorio / MANIFIESTO
    if not ruta.exists():
        return {}
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return {k: EntradaManifiesto.model_validate(v) for k, v in datos.items()}


def cargar(directorio: Path = DIR_POR_DEFECTO) -> Corpus:
    manifiesto = leer_manifiesto(directorio)
    if not manifiesto:
        raise FileNotFoundError(
            f"No hay corpus en {directorio}. Ejecuta `vut sync` para descargarlo."
        )
    corpus = Corpus()
    for norma_id, entrada in manifiesto.items():
        ruta = directorio / f"{norma_id}.jsonl"
        if not ruta.exists():
            raise CorpusCorrupto(f"El manifiesto cita {ruta.name}, pero el archivo no existe")
        with ruta.open(encoding="utf-8") as fh:
            fragmentos = [Fragmento.model_validate_json(linea) for linea in fh]
        if len(fragmentos) != entrada.fragmentos:
            raise CorpusCorrupto(
                f"{ruta.name}: {len(fragmentos)} fragmentos, "
                f"el manifiesto dice {entrada.fragmentos}"
            )
        corpus.normas[norma_id] = entrada.norma
        corpus.fragmentos.extend(fragmentos)
    return corpus
