"""Disposiciones publicadas en el BOE que no se consolidan: sentencias, órdenes, anuncios.

El XML del diario (`/diario_boe/xml.php?id=…`) trae los metadatos y el texto íntegro de lo
publicado. Para una sentencia del Tribunal Supremo es la parte dispositiva (el fallo), que es
lo que obliga y lo que se cita.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET

import httpx

from vut_andalucia.ingest.comun import fragmentos_de_version
from vut_andalucia.models import Fragmento, Norma

DIARIO = "https://www.boe.es/diario_boe/xml.php?id={id}"


class BOEDiarioError(RuntimeError):
    pass


def descargar(ident: str, client: httpx.Client) -> bytes:
    r = client.get(DIARIO.format(id=ident))
    if r.status_code != 200 or b"<texto" not in r.content:
        raise BOEDiarioError(f"{ident}: el BOE respondió {r.status_code} sin texto")
    return r.content


def metadatos(xml: bytes) -> dict[str, str]:
    raiz = ET.fromstring(xml)
    meta = raiz.find("metadatos")
    if meta is None:
        raise BOEDiarioError("XML del diario sin <metadatos>")
    return {e.tag: (e.text or "").strip() for e in meta}


def fecha(valor: str) -> dt.date:
    try:
        return dt.datetime.strptime(valor, "%Y%m%d").date()
    except ValueError as e:
        raise BOEDiarioError(f"Fecha inválida en el diario: {valor!r}") from e


def parsear(xml: bytes, norma: Norma, base_cita: str, desde: dt.date) -> list[Fragmento]:
    raiz = ET.fromstring(xml)
    texto = raiz.find("texto")
    if texto is None:
        raise BOEDiarioError(f"{norma.id}: XML sin <texto>")
    parrafos = [re.sub(r"\s+", " ", "".join(p.itertext())).strip() for p in texto.iter("p")]
    parrafos = [p for p in parrafos if p]
    if not parrafos:
        raise BOEDiarioError(f"{norma.id}: el texto está vacío")
    return list(
        fragmentos_de_version(
            norma=norma,
            bloque_id="texto",
            base_cita=base_cita,
            encabezado=norma.titulo,
            parrafos=parrafos,
            ruta=[],
            desde=desde,
            hasta=None,
            url=f"https://www.boe.es/diario_boe/txt.php?id={norma.id}",
        )
    )
