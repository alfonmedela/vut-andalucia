"""Sincroniza el corpus con las fuentes oficiales."""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

import httpx

from vut_andalucia import corpus
from vut_andalucia.ingest import boe, boe_diario, boja, documento, eurlex
from vut_andalucia.models import Fragmento, Norma
from vut_andalucia.sources import CATALOGO, Fuente

Lote = tuple[Norma, list[Fragmento], bytes]
DIR_FUENTES = Path(__file__).parent.parent / "data" / "fuentes"

log = logging.getLogger(__name__)


class SincronizacionError(RuntimeError):
    pass


def _norma(fuente: Fuente, **datos: str | None) -> Norma:
    return Norma(
        id=fuente.id,
        titulo=datos.get("titulo") or fuente.titulo or fuente.corta,
        corta=fuente.corta,
        rango=datos.get("rango") or fuente.rango or "",
        ambito=datos.get("ambito") or fuente.territorio.value,
        url=datos.get("url") or "",
        eli=datos.get("eli"),
        fuente=fuente.fuente,
        clase=fuente.clase,
        territorio=fuente.territorio,
        municipio=fuente.municipio,
        estado=" ".join(e for e in (fuente.estado, datos.get("estado")) if e) or None,
    )


def _filtra_bloques(fuente: Fuente, fragmentos: list[Fragmento]) -> list[Fragmento]:
    if not fuente.bloques:
        return fragmentos
    elegidos = [f for f in fragmentos if f.bloque_id in fuente.bloques]
    faltan = set(fuente.bloques) - {f.bloque_id for f in elegidos}
    if faltan:
        raise SincronizacionError(f"{fuente.id}: bloques inexistentes {sorted(faltan)}")
    return elegidos


def _cierra(fragmentos: list[Fragmento], hasta: dt.date | None) -> list[Fragmento]:
    """Una norma derogada o anulada en bloque deja de estar vigente en esa fecha."""
    if hasta is None:
        return fragmentos
    return [
        f.model_copy(update={"vigente_hasta": hasta})
        if f.vigente_hasta is None or f.vigente_hasta > hasta
        else f
        for f in fragmentos
        if f.vigente_desde < hasta
    ]


def descargar_fuente(fuente: Fuente, client: httpx.Client, dir_fuentes: Path = DIR_FUENTES) -> Lote:
    fin: dt.date | None = None
    if fuente.fuente == "boe":
        meta, bruto = boe.descargar(fuente.id, client)
        base = boe.norma_desde_metadatos(meta, fuente.corta)
        estado, fin = boe.estado_de_vigencia(meta)
        if "decreto-ley" in base.rango.lower():
            pendiente = boe.pendiente_de_convalidacion(meta, boe.posteriores(fuente.id, client))
            estado = " ".join(x for x in (estado, pendiente) if x) or None
        norma = _norma(
            fuente,
            titulo=base.titulo,
            rango=base.rango,
            ambito=base.ambito,
            url=base.url,
            eli=base.eli,
            estado=estado,
        )
        fragmentos = _filtra_bloques(fuente, boe.parsear(bruto, norma))
    elif fuente.fuente == "boe_diario":
        if fuente.desde is None:  # pragma: no cover - lo exige el catálogo
            raise SincronizacionError(f"{fuente.id}: falta `desde`")
        bruto = boe_diario.descargar(fuente.id, client)
        meta = boe_diario.metadatos(bruto)
        norma = _norma(
            fuente,
            titulo=meta.get("titulo"),
            rango=meta.get("rango"),
            ambito=meta.get("origen_legislativo"),
            url=f"https://www.boe.es/diario_boe/txt.php?id={fuente.id}",
        )
        fragmentos = boe_diario.parsear(bruto, norma, fuente.cita or "texto", fuente.desde)
    elif fuente.fuente == "boja":
        if fuente.origen is None:  # pragma: no cover
            raise SincronizacionError(f"{fuente.id}: falta `origen`")
        directorio = dir_fuentes / "boja" / fuente.origen
        url = boja.FICHA.format(id=fuente.origen)
        norma = _norma(fuente, url=url, ambito="Autonómico")
        fragmentos = _filtra_bloques(fuente, boja.parsear(directorio, norma, url))
        bruto = b"".join(p.read_bytes() for p in sorted(directorio.glob("*.html")))
    elif fuente.fuente == "eurlex":
        if fuente.origen is None or fuente.desde is None:  # pragma: no cover
            raise SincronizacionError(f"{fuente.id}: faltan `origen` o `desde`")
        bruto = eurlex.descargar(fuente.origen, client)
        norma = _norma(fuente, url=eurlex.EURLEX.format(celex=fuente.origen), ambito="UE")
        fragmentos = _filtra_bloques(
            fuente, eurlex.parsear(bruto, norma, fuente.origen, fuente.desde)
        )
    elif fuente.fuente == "documento":
        if fuente.origen is None or fuente.desde is None:  # pragma: no cover
            raise SincronizacionError(f"{fuente.id}: faltan `origen` o `desde`")
        bruto = documento.obtener(fuente.origen, client, dir_fuentes)
        norma = _norma(fuente, url=documento.url_publica(fuente.origen))
        fragmentos = documento.parsear(bruto, norma, fuente)
    else:  # pragma: no cover - el catálogo ya valida el tipo
        raise SincronizacionError(f"Fuente {fuente.fuente} no soportada ({fuente.id})")
    fragmentos = _cierra(_cierra(fragmentos, fin), fuente.hasta)
    if not fragmentos:
        raise SincronizacionError(f"{fuente.id}: el parser no extrajo ningún fragmento")
    return norma, fragmentos, bruto


def avisar_decretos_ley(lote: list[Lote], client: httpx.Client) -> list[Lote]:
    """Marca los textos vigentes cuya redacción viene de un decreto-ley sin convalidar.

    Un real decreto-ley rige desde su publicación, pero decae si el Congreso no lo convalida en
    30 días hábiles (el RDL 26/2026, por ejemplo, fue derogado así). Hasta entonces, todo
    artículo que haya redactado lleva el aviso.
    """
    hoy = dt.date.today()
    candidatos = {
        f.modificado_por
        for _, fragmentos, _ in lote
        for f in fragmentos
        if f.modificado_por
        and re.fullmatch(r"(BOE-A|BOJA-b)-\d{4}-\d+", f.modificado_por)
        and int(f.modificado_por.split("-")[2]) >= hoy.year - 1
        and (f.vigente_hasta is None or f.vigente_hasta > hoy)
    }
    avisos: dict[str, str] = {}
    for ident in sorted(candidatos):
        try:
            meta = boe.metadatos(ident, client)
        except boe.BOEError as e:
            # Algunas versiones citan ids internos que la API no resuelve. No se puede saber si
            # esa redacción viene de un decreto-ley sin convalidar, y eso se dice en la cita.
            log.warning("No se pudo comprobar la norma modificadora %s: %s", ident, e)
            avisos[ident] = (
                f"No se pudo comprobar en el BOE la norma que dio esta redacción ({ident}); "
                "si fuera un decreto-ley, podría estar pendiente de convalidación."
            )
            continue
        aviso = boe.pendiente_de_convalidacion(meta, boe.posteriores(ident, client))
        if aviso:
            avisos[ident] = aviso
    if not avisos:
        return lote
    salida: list[Lote] = []
    for norma, fragmentos, bruto in lote:
        marcados = [
            f.model_copy(
                update={"nota": " ".join(x for x in (f.nota, avisos[f.modificado_por]) if x)}
            )
            if f.modificado_por in avisos and (f.vigente_hasta is None or f.vigente_hasta > hoy)
            else f
            for f in fragmentos
        ]
        salida.append((norma, marcados, bruto))
    return salida


def sincronizar(
    directorio: Path = corpus.DIR_POR_DEFECTO,
    fuentes: tuple[Fuente, ...] = CATALOGO,
    client: httpx.Client | None = None,
    dir_fuentes: Path = DIR_FUENTES,
) -> list[corpus.EntradaManifiesto]:
    """Descarga y parsea todo; solo si nada falla, escribe el corpus."""
    propio = client is None
    client = client or httpx.Client(
        timeout=60, headers={"User-Agent": boe.USER_AGENT}, transport=httpx.HTTPTransport(retries=3)
    )
    try:
        lote = [descargar_fuente(f, client, dir_fuentes) for f in fuentes]
        lote = avisar_decretos_ley(lote, client)
    finally:
        if propio:
            client.close()
    catalogo = {f.id for f in CATALOGO} if fuentes is CATALOGO else None
    return corpus.guardar(directorio, lote, catalogo=catalogo)
