"""Ingesta desde la API de legislación consolidada del BOE.

La API devuelve, para cada bloque (artículo, disposición), todas sus versiones con su
fecha de vigencia. Cada versión se convierte en uno o varios fragmentos con su rango
de vigencia, lo que permite responder «¿qué decía la norma en tal fecha?».
Documentación: https://www.boe.es/datosabiertos/api/api.php
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterator
from typing import Any

import httpx

from vut_andalucia.models import Fragmento, Norma
from vut_andalucia.text import cita_de_bloque

API = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
USER_AGENT = "vut-andalucia/0.1 (+https://github.com/alfonmedela/vut-andalucia)"

# Un artículo largo se trocea por apartados numerados para que BM25 no diluya el apartado relevante.
MAX_CHARS_SIN_TROCEAR = 1200
_APARTADO = re.compile(r"^(\d{1,2})\.\s")
_LETRA = re.compile(r"^[a-zñ]\)\s")
# Una versión cuyo único contenido es una nota de derogación: el precepto no está en vigor.
_SIN_CONTENIDO = re.compile(
    r"^\(?\s*(queda\s+)?(derogad[oa]|anulad[oa]|suprimid[oa]|sin contenido)\s*\)?\.?$", re.I
)

log = logging.getLogger(__name__)


class BOEError(RuntimeError):
    pass


def _fecha(valor: str, donde: str) -> dt.date:
    try:
        return dt.datetime.strptime(valor, "%Y%m%d").date()
    except ValueError as e:
        raise BOEError(f"{donde}: fecha inválida o ausente ({valor!r})") from e


def _texto(elem: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(elem.itertext())).strip()


def descargar(norma_id: str, client: httpx.Client) -> tuple[dict[str, Any], bytes]:
    """Devuelve (metadatos, XML del texto consolidado con todas sus versiones)."""
    meta = client.get(f"{API}/id/{norma_id}/metadatos", headers={"Accept": "application/json"})
    texto = client.get(f"{API}/id/{norma_id}/texto", headers={"Accept": "application/xml"})
    for r in (meta, texto):
        if r.status_code != 200:
            raise BOEError(f"{r.request.url} respondió {r.status_code}")
    data = meta.json().get("data")
    if not data:
        raise BOEError(f"Metadatos vacíos para {norma_id}")
    return data[0] if isinstance(data, list) else data, texto.content


def norma_desde_metadatos(meta: dict[str, Any], corta: str) -> Norma:
    return Norma(
        id=meta["identificador"],
        titulo=meta["titulo"],
        corta=corta,
        rango=meta["rango"]["texto"],
        ambito=meta["ambito"]["texto"],
        url=meta.get("url_html_consolidada")
        or f"https://www.boe.es/buscar/act.php?id={meta['identificador']}",
        eli=meta.get("url_eli"),
        fuente="boe",
    )


def _apartados(parrafos: list[str]) -> list[tuple[str | None, list[str]]]:
    """Agrupa párrafos por apartado numerado ('1.', '2.'...). Lo previo al primero va con None.

    El BOE no marca de forma fiable el nivel de cada párrafo, así que se infiere: un '1.' que
    aparece con un apartado ya abierto empieza una lista anidada, que continúa mientras los
    números sigan su secuencia y termina en la siguiente letra ('b)') o párrafo sin número.
    """
    grupos: list[tuple[str | None, list[str]]] = [(None, [])]
    actual = 0
    anidado = 0  # último número de la lista anidada en curso; 0 si no hay
    for p in parrafos:
        m = _APARTADO.match(p)
        n = int(m.group(1)) if m else None
        if n is None:
            anidado = 0
        elif anidado and n == anidado + 1:
            anidado = n
        elif n == actual + 1:
            actual, anidado = n, 0
            grupos.append((str(n), [p]))
            continue
        elif n == 1 and actual >= 1:
            anidado = 1
        if _LETRA.match(p):
            anidado = 0
        grupos[-1][1].append(p)
    return [g for g in grupos if g[1]]


def _cita(base: str, apartado: str | None, corta: str) -> str:
    if apartado is None:
        return f"{base} {corta}"
    sep = "." if base.startswith("art.") else ", apdo. "
    return f"{base}{sep}{apartado} {corta}"


def parsear(xml: bytes, norma: Norma) -> list[Fragmento]:
    raiz = ET.fromstring(xml)
    texto = raiz.find("./data/texto")
    if texto is None:
        raise BOEError(f"XML sin <texto> para {norma.id}")
    fragmentos: list[Fragmento] = []
    ruta: dict[str, str] = {}
    ignorados: Counter[str] = Counter()
    for bloque in texto.findall("bloque"):
        versiones = bloque.findall("version")
        if not versiones:
            continue
        if bloque.get("tipo") == "encabezado":
            _actualiza_ruta(ruta, versiones[-1])
            continue
        if bloque.get("tipo") != "precepto":
            ignorados[bloque.get("tipo") or "?"] += 1
            continue
        es_disposicion = (bloque.get("titulo") or "").lower().startswith("disposici")
        contexto = [] if es_disposicion else list(ruta.values())
        fragmentos.extend(_fusionar(_fragmentos_de_bloque(bloque, versiones, norma, contexto)))
    if ignorados:
        log.info("%s: bloques no normativos ignorados: %s", norma.id, dict(ignorados))
    repetidos = sorted(i for i, c in Counter(f.id for f in fragmentos).items() if c > 1)
    if repetidos:
        raise BOEError(f"{norma.id}: identificadores de fragmento repetidos: {repetidos[:5]}")
    return fragmentos


def _fusionar(fragmentos: Iterator[Fragmento]) -> list[Fragmento]:
    """Une versiones consecutivas de un apartado cuyo texto no cambió.

    Una reforma reescribe el artículo entero en el BOE aunque solo toque un apartado; así cada
    apartado conserva la fecha en que su texto entró realmente en vigor.
    """
    ultimos: dict[str, int] = {}  # base_id -> posición en `salida`
    salida: list[Fragmento] = []
    for f in fragmentos:
        i = ultimos.get(f.base_id)
        previo = salida[i] if i is not None else None
        if (
            i is not None
            and previo is not None
            and previo.texto == f.texto
            and previo.vigente_hasta == f.vigente_desde
        ):
            salida[i] = previo.model_copy(
                update={
                    "vigente_hasta": f.vigente_hasta,
                    "vigencia_incierta": previo.vigencia_incierta or f.vigencia_incierta,
                }
            )
            continue
        ultimos[f.base_id] = len(salida)
        salida.append(f)
    return salida


def _actualiza_ruta(ruta: dict[str, str], version: ET.Element) -> None:
    niveles = {"titulo": 0, "capitulo": 1, "seccion": 2}
    partes: dict[str, list[str]] = {}
    for p in version.findall("p"):
        clase = (p.get("class") or "").split("_")[0]
        if clase in niveles:
            partes.setdefault(clase, []).append(_texto(p))
    for nivel, textos in partes.items():
        ruta[nivel] = ". ".join(textos)
        # Un nivel superior nuevo invalida los inferiores.
        for inferior, orden in niveles.items():
            if orden > niveles[nivel]:
                ruta.pop(inferior, None)
    ordenada = dict(sorted(ruta.items(), key=lambda kv: niveles[kv[0]]))
    ruta.clear()
    ruta.update(ordenada)


def _fragmentos_de_bloque(
    bloque: ET.Element, versiones: list[ET.Element], norma: Norma, ruta: list[str]
) -> Iterator[Fragmento]:
    bloque_id = bloque.get("id", "")
    donde = f"{norma.id}#{bloque_id}"
    base_cita = cita_de_bloque(bloque.get("titulo", bloque_id))
    # Algunas versiones recientes llegan sin fecha de vigencia: se usa la de publicación y se avisa.
    fechadas = [
        (
            _fecha(v.get("fecha_vigencia") or v.get("fecha_publicacion") or "", donde),
            v.get("fecha_publicacion") or "",
            v,
        )
        for v in versiones
    ]
    # Dos versiones con la misma fecha de vigencia: prevalece la última publicada.
    por_fecha: dict[dt.date, ET.Element] = {}
    for fecha, _pub, v in sorted(fechadas, key=lambda x: (x[0], x[1])):
        if fecha in por_fecha:
            log.warning(
                "%s: dos versiones con vigencia %s; se usa la última publicada", donde, fecha
            )
        por_fecha[fecha] = v
    fechas = list(por_fecha)
    versiones = list(por_fecha.values())
    for i, version in enumerate(versiones):
        desde = fechas[i]
        hasta = fechas[i + 1] if i + 1 < len(fechas) else None
        encabezado = ""
        parrafos: list[str] = []
        notas = {id(e) for bq in version.iter("blockquote") for e in bq.iter()}
        for p in version.iter("p"):  # incluye párrafos dentro de tablas; excluye las notas
            if id(p) in notas:
                continue
            t = _texto(p)
            if not t:
                continue
            if p.get("class") == "articulo" and not encabezado:
                encabezado = t
            else:
                parrafos.append(t)
        if not parrafos:
            log.warning("%s: versión %s sin párrafos; se omite", donde, desde)
            continue
        if len(parrafos) == 1 and _SIN_CONTENIDO.match(parrafos[0]):
            continue  # derogado o anulado desde `desde`: no hay texto en vigor que citar
        cuerpo = "\n".join(parrafos)
        grupos = _apartados(parrafos)
        trocear = len(cuerpo) > MAX_CHARS_SIN_TROCEAR and sum(1 for a, _ in grupos if a) >= 2
        piezas = grupos if trocear else [(None, parrafos)]
        for apartado, ps in piezas:
            sufijo = f".{apartado}" if apartado else ""
            if trocear and apartado is None:
                sufijo = ".0"
            yield Fragmento(
                id=f"{norma.id}#{bloque_id}{sufijo}@{desde.isoformat()}",
                norma_id=norma.id,
                bloque_id=bloque_id,
                apartado=apartado,
                cita=_cita(base_cita, apartado, norma.corta),
                encabezado=encabezado or bloque.get("titulo", ""),
                ruta=ruta,
                texto="\n".join(ps),
                vigente_desde=desde,
                vigente_hasta=hasta,
                vigencia_incierta=not version.get("fecha_vigencia"),
                modificado_por=version.get("id_norma"),
                url=f"https://www.boe.es/buscar/act.php?id={norma.id}#{bloque_id}",
            )
