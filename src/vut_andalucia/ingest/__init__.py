"""Sincroniza el corpus con las fuentes oficiales."""

from __future__ import annotations

from pathlib import Path

import httpx

from vut_andalucia import corpus
from vut_andalucia.ingest import boe
from vut_andalucia.models import Fragmento, Norma
from vut_andalucia.sources import CATALOGO, Fuente

Lote = tuple[Norma, list[Fragmento], bytes]


def descargar_fuente(fuente: Fuente, client: httpx.Client) -> Lote:
    if fuente.fuente != "boe":
        raise NotImplementedError(f"Fuente {fuente.fuente} aún no soportada ({fuente.id})")
    meta, xml = boe.descargar(fuente.id, client)
    norma = boe.norma_desde_metadatos(meta, fuente.corta)
    fragmentos = boe.parsear(xml, norma)
    if not fragmentos:
        raise boe.BOEError(f"{fuente.id}: el parser no extrajo ningún fragmento")
    return norma, fragmentos, xml


def sincronizar(
    directorio: Path = corpus.DIR_POR_DEFECTO,
    fuentes: tuple[Fuente, ...] = CATALOGO,
    client: httpx.Client | None = None,
) -> list[corpus.EntradaManifiesto]:
    """Descarga y parsea todo; solo si nada falla, escribe el corpus."""
    propio = client is None
    client = client or httpx.Client(
        timeout=60, headers={"User-Agent": boe.USER_AGENT}, transport=httpx.HTTPTransport(retries=3)
    )
    try:
        lote = [descargar_fuente(f, client) for f in fuentes]
    finally:
        if propio:
            client.close()
    return corpus.guardar(directorio, lote)
