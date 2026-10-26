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

from vut_andalucia.ingest.comun import fragmentos_de_version, fusionar
from vut_andalucia.models import Fragmento, Norma
from vut_andalucia.text import cita_de_bloque

API = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
USER_AGENT = "vut-andalucia/0.1 (+https://github.com/alfonmedela/vut-andalucia)"

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


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        datos = r.json()
    except ValueError as e:
        raise BOEError(f"{r.request.url} no devolvió JSON: {r.text[:120]!r}") from e
    if not isinstance(datos, dict):
        raise BOEError(f"{r.request.url} devolvió {type(datos).__name__}, no un objeto")
    return datos


def metadatos(norma_id: str, client: httpx.Client) -> dict[str, Any]:
    r = client.get(f"{API}/id/{norma_id}/metadatos", headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise BOEError(f"{r.request.url} respondió {r.status_code}")
    data = _json(r).get("data")
    if not data:
        raise BOEError(f"Metadatos vacíos para {norma_id}")
    return data[0] if isinstance(data, list) else data  # type: ignore[no-any-return]


def posteriores(norma_id: str, client: httpx.Client) -> list[dict[str, Any]]:
    """Referencias posteriores del análisis jurídico del BOE (derogaciones, convalidación…)."""
    r = client.get(f"{API}/id/{norma_id}/analisis", headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise BOEError(f"{r.request.url} respondió {r.status_code}")
    datos = _json(r).get("data") or [{}]
    refs = (datos[0] if isinstance(datos, list) else datos).get("referencias") or {}
    salida: list[dict[str, Any]] = []
    bloques = refs.get("posteriores") or []
    for b in bloques if isinstance(bloques, list) else [bloques]:
        items = b.get("posterior", []) if isinstance(b, dict) else []
        salida.extend(items if isinstance(items, list) else [items])
    return salida


def pendiente_de_convalidacion(meta: dict[str, Any], refs: list[dict[str, Any]]) -> str | None:
    """Aviso si es un decreto-ley que, según el BOE, aún no ha sido convalidado."""
    rango = (meta.get("rango") or {}).get("texto", "")
    if "decreto-ley" not in rango.lower():
        return None
    relaciones = " ".join((r.get("relacion") or {}).get("texto", "") for r in refs).lower()
    if "convalida" in relaciones or "deroga" in relaciones or meta.get("estatus_derogacion") == "S":
        return None
    camara = (
        "el Parlamento de Andalucía"
        if meta.get("identificador", "").startswith("BOJA")
        else "el Congreso de los Diputados"
    )
    numero = meta.get("numero_oficial") or meta.get("identificador")
    return (
        f"Procede del {rango} {numero}, que el BOE aún no recoge como convalidado por "
        f"{camara}. Si no se convalida en plazo, queda derogado y esta redacción decae."
    )


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


_ESTADOS = (
    ("estatus_derogacion", "fecha_derogacion", "Derogada"),
    ("estatus_anulacion", "fecha_anulacion", "Anulada"),
    ("vigencia_agotada", "fecha_vigencia_agotada", "Vigencia agotada"),
)


def estado_de_vigencia(meta: dict[str, Any]) -> tuple[str | None, dt.date | None]:
    """(aviso, fecha de fin) si el BOE marca la norma como derogada, anulada o agotada."""
    for estatus, campo_fecha, rotulo in _ESTADOS:
        if meta.get(estatus) != "S":
            continue
        valor = meta.get(campo_fecha) or ""
        if not valor:
            raise BOEError(
                f"{meta.get('identificador')}: el BOE la marca como «{rotulo}» sin fecha; "
                "revisa la norma e indica `hasta` en el catálogo"
            )
        fin = _fecha(valor[:8], str(meta.get("identificador")))
        return f"{rotulo} desde el {fin.strftime('%d/%m/%Y')}.", fin
    return None, None


def parsear(xml: bytes, norma: Norma) -> list[Fragmento]:
    raiz = ET.fromstring(xml)
    texto = raiz.find("./data/texto")
    if texto is None:
        raise BOEError(f"XML sin <texto> para {norma.id}")
    fragmentos: list[Fragmento] = []
    ruta: dict[str, str] = {}
    ignorados: Counter[str] = Counter()
    # Avisos de efectos diferidos, por id de fragmento. Se aplican tras fusionar: solo los
    # recibe el texto que cambió en esa versión, no los apartados que siguen igual.
    diferidos: dict[str, str] = {}
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
        fragmentos.extend(
            fusionar(_fragmentos_de_bloque(bloque, versiones, norma, contexto, diferidos))
        )
    fragmentos = [
        f.model_copy(update={"nota": " ".join(x for x in (f.nota, diferidos[f.id]) if x)})
        if f.id in diferidos
        else f
        for f in fragmentos
    ]
    if ignorados:
        log.info("%s: bloques no normativos ignorados: %s", norma.id, dict(ignorados))
    repetidos = sorted(i for i, c in Counter(f.id for f in fragmentos).items() if c > 1)
    if repetidos:
        raise BOEError(f"{norma.id}: identificadores de fragmento repetidos: {repetidos[:5]}")
    return fragmentos


_MESES = {
    m: i
    for i, m in enumerate(
        "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre "
        "diciembre".split(),
        1,
    )
}
_EFECTOS = re.compile(
    r"con efectos? (?:desde |a partir |de |del )?(?:el |la fecha |del )?(?:de )?"
    r"(\d{1,2})(?:º)? de (" + "|".join(_MESES) + r") de (\d{4})",
    re.I,
)


def efectos_de_version(version: ET.Element) -> tuple[dt.date | None, str | None]:
    """Fecha de efectos de una versión cuando difiere de su fecha de vigencia.

    El BOE fecha cada versión el día en que entra en vigor la norma modificadora, pero esta
    puede diferir sus efectos («Se modifica, con efectos de 1 de diciembre de 2026…»). Esa
    información solo está en las notas de la versión. Devuelve (fecha desde la que el nuevo
    texto produce efectos, aviso para el usuario), o (None, None) si no hay diferimiento.
    """
    norma = version.get("id_norma") or ""
    vigencia = version.get("fecha_vigencia") or ""
    if not norma or not vigencia:
        return None, None
    notas = " ".join(_texto(bq) for bq in version.iter("blockquote"))
    # Cada nota termina en «Ref. BOE-A-…»; solo cuentan las de la norma de esta versión.
    trozos = [t for t in re.split(r"(?<=Ref\. )(?=\S)", notas)]
    propias = []
    for i in range(1, len(trozos)):
        if trozos[i].startswith(norma):
            propias.append(trozos[i - 1].rsplit(". ", 1)[-1] if i > 1 else trozos[0])
    fechas = set()
    for nota in propias:
        m = _EFECTOS.search(nota)
        if m:
            fechas.add(dt.date(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1))))
    if not fechas:
        return None, None
    inicio = dt.datetime.strptime(vigencia, "%Y%m%d").date()
    if len(fechas) > 1:
        lista = ", ".join(sorted(f.strftime("%d/%m/%Y") for f in fechas))
        return None, f"Parte de esta redacción tiene efectos diferidos ({lista}); revisa la norma."
    efectos = fechas.pop()
    if efectos <= inicio:
        return None, f"Redacción con efectos desde el {efectos.strftime('%d/%m/%Y')}."
    return efectos, (
        f"El BOE la publica como vigente desde el {inicio.strftime('%d/%m/%Y')}, pero la "
        f"norma que la introduce ({norma}) difiere sus efectos al "
        f"{efectos.strftime('%d/%m/%Y')}."
    )


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
    bloque: ET.Element,
    versiones: list[ET.Element],
    norma: Norma,
    ruta: list[str],
    diferidos: dict[str, str] | None = None,
) -> Iterator[Fragmento]:
    bloque_id = bloque.get("id", "")
    donde = f"{norma.id}#{bloque_id}"
    base_cita = cita_de_bloque(bloque.get("titulo", bloque_id))
    # Algunas versiones recientes llegan sin fecha de vigencia: se usa la de publicación y se avisa.
    fechadas = []
    diferidas: dict[int, str] = {}  # id(versión) -> aviso de efectos diferidos o inciertos
    for v in versiones:
        vigencia = _fecha(v.get("fecha_vigencia") or v.get("fecha_publicacion") or "", donde)
        efectos, aviso = efectos_de_version(v)
        if aviso:
            diferidas[id(v)] = aviso
        fechadas.append((efectos or vigencia, v.get("fecha_publicacion") or "", v))
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
        # Las notas «siempreSeVe» son advertencias jurídicas (p. ej. una anulación parcial por
        # sentencia), no historia de redacciones: viajan con el fragmento y se muestran siempre.
        avisos = [
            _texto(bq)
            for bq in version.iter("blockquote")
            if "siempreSeVe" in (bq.get("class") or "")
        ]
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
        for f in fragmentos_de_version(
            norma=norma,
            bloque_id=bloque_id,
            base_cita=base_cita,
            encabezado=encabezado or bloque.get("titulo", ""),
            parrafos=parrafos,
            ruta=ruta,
            desde=desde,
            hasta=hasta,
            url=f"https://www.boe.es/buscar/act.php?id={norma.id}#{bloque_id}",
            vigencia_incierta=not version.get("fecha_vigencia"),
            modificado_por=version.get("id_norma"),
            nota=" ".join(avisos) or None,
        ):
            if diferidos is not None and id(version) in diferidas:
                diferidos[f.id] = diferidas[id(version)]
            yield f
