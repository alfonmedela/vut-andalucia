"""API web y servidor de la interfaz: `vut web` o `uvicorn vut_andalucia.api:app`.

La pregunta se responde por Server-Sent Events en dos pasos: primero las fuentes consultadas
(al instante, sin LLM) y después la respuesta con sus citas ya verificadas.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vut_andalucia import municipios
from vut_andalucia.answer import (
    Preparacion,
    abstencion_previa,
    avisos_de,
    completar,
    contexto_amplio,
    preparar,
)
from vut_andalucia.llm import LLM, LLMError, crear
from vut_andalucia.models import Fragmento, Norma, Respuesta
from vut_andalucia.retrieval import Buscador
from vut_andalucia.servicio import crear_buscador

WEB = Path(__file__).parent / "web"
MENSAJE_SOLO_BUSQUEDA = (
    "Esta demo está en modo búsqueda: muestra los artículos vigentes más relevantes con su texto "
    "oficial, pero no redacta respuesta. Instálalo en local con Ollama para tener respuestas."
)
MAX_CHARS_PREGUNTA = 1200

log = logging.getLogger(__name__)
app = FastAPI(
    title="vut-andalucia",
    description="Normativa de viviendas de uso turístico en Andalucía, con citas verificadas.",
    version="0.1.0",
    docs_url=None if os.environ.get("VUT_PUBLICO") == "1" else "/docs",
    redoc_url=None,
    openapi_url=None if os.environ.get("VUT_PUBLICO") == "1" else "/openapi.json",
)

CSP = (
    "default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "style-src 'self' https://fonts.googleapis.com https://api.fontshare.com; "
    "font-src https://fonts.gstatic.com https://cdn.fontshare.com; "
    "object-src 'none'; base-uri 'none'; form-action 'self'"
)


@app.middleware("http")
async def cabeceras_de_seguridad(request: Request, call_next: Any) -> Any:
    respuesta = await call_next(request)
    respuesta.headers.setdefault("Content-Security-Policy", CSP)
    respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
    respuesta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return respuesta


@cache
def buscador() -> Buscador:
    return crear_buscador()


@cache
def llm() -> LLM | None:
    """El modelo configurado, o None en modo «solo búsqueda» (VUT_PROVEEDOR=ninguno)."""
    proveedor = os.environ.get("VUT_PROVEEDOR", "ollama")
    if proveedor == "ninguno":
        return None
    if proveedor == "suscripcion" and PUBLICO:
        # La suscripción de Claude es personal: una demo pública usa la API (`claude`).
        raise LLMError("VUT_PROVEEDOR=suscripcion no se admite con VUT_PUBLICO=1")
    return crear(proveedor, os.environ.get("VUT_MODELO") or None)


class LimitePorIP:
    """Límite de peticiones por IP en una ventana, con memoria acotada."""

    MAX_IPS = 10_000

    def __init__(self, maximo: int, ventana: float = 3600) -> None:
        self.maximo, self.ventana = maximo, ventana
        self._marcas: dict[str, list[float]] = {}

    def permitir(self, ip: str) -> bool:
        ahora = time.monotonic()
        marcas = [t for t in self._marcas.get(ip, []) if ahora - t < self.ventana]
        permitido = len(marcas) < self.maximo
        if permitido:
            marcas.append(ahora)
        self._marcas[ip] = marcas
        if len(self._marcas) > self.MAX_IPS:
            self._marcas = {
                k: v for k, v in self._marcas.items() if v and ahora - v[-1] < self.ventana
            }
        return permitido


class CupoDiario:
    """Tope global de preguntas al modelo por día: al agotarse, la demo pasa a solo búsqueda."""

    def __init__(self, maximo: int) -> None:
        self.maximo = maximo
        self._dia = dt.date.today()
        self._usadas = 0

    def consumir(self) -> bool:
        hoy = dt.date.today()
        if hoy != self._dia:
            self._dia, self._usadas = hoy, 0
        if self._usadas >= self.maximo:
            return False
        self._usadas += 1
        return True


def ip_cliente(request: Request) -> str:
    """La IP del cliente. Detrás de un proxy de confianza (VUT_PROXY=1), la que añadió el
    proxy: el último valor de X-Forwarded-For; el primero lo controla el cliente."""
    if os.environ.get("VUT_PROXY") == "1":
        cadena = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",")]
        if cadena and cadena[-1]:
            return cadena[-1]
    return request.client.host if request.client else "?"


# 0 o sin definir: sin límite (uso local). En la demo pública se fija con la variable.
limite = LimitePorIP(int(os.environ.get("VUT_PREGUNTAS_POR_HORA", "0")) or 10**9)
limite_busqueda = LimitePorIP(int(os.environ.get("VUT_BUSQUEDAS_POR_HORA", "0")) or 10**9)
cupo = CupoDiario(int(os.environ.get("VUT_PREGUNTAS_POR_DIA", "0")) or 10**9)
PUBLICO = os.environ.get("VUT_PUBLICO") == "1"
REINTENTAR = os.environ.get("VUT_REINTENTAR", "1") == "1"


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=3, max_length=MAX_CHARS_PREGUNTA)
    fecha: dt.date | None = None
    municipio: str | None = Field(default=None, max_length=80)


def _norma_json(n: Norma | None) -> dict[str, Any] | None:
    if n is None:
        return None
    m = municipios.por_slug().get(n.municipio or "")
    return {
        "id": n.id,
        "titulo": n.titulo,
        "corta": n.corta,
        "rango": n.rango,
        "clase": n.clase.value,
        "territorio": n.territorio.value,
        "municipio": m.nombre if m else None,
        "estado": n.estado,
        "url": n.url,
    }


def _fragmento_json(f: Fragmento, normas: dict[str, Norma]) -> dict[str, Any]:
    norma = normas.get(f.norma_id)
    return {
        "id": f.id,
        "base_id": f.base_id,
        "cita": f.cita,
        "encabezado": f.encabezado,
        "ruta": f.ruta,
        "texto": f.texto,
        "vigente_desde": f.vigente_desde.isoformat(),
        "vigente_hasta": f.vigente_hasta.isoformat() if f.vigente_hasta else None,
        "vigencia_incierta": f.vigencia_incierta,
        "avisos": avisos_de(f, norma),
        "url": f.url,
        "norma": _norma_json(norma),
    }


def _respuesta_json(r: Respuesta, normas: dict[str, Norma]) -> dict[str, Any]:
    m = municipios.por_slug().get(r.municipio or "")
    return {
        "pregunta": r.pregunta,
        "fecha": r.fecha.isoformat(),
        "municipio": {"slug": m.slug, "nombre": m.nombre, "provincia": m.provincia} if m else None,
        "municipio_detectado": r.municipio_detectado,
        "afirmaciones": [
            {
                "texto": a.texto,
                "tipo": a.tipo.value,
                "cita_literal": a.cita_literal,
                "corregida": a.corregida,
                "fragmento": _fragmento_json(a.fragmento, normas),
            }
            for a in r.afirmaciones
        ],
        "descartadas": r.descartadas,
        "no_respaldadas": r.no_respaldadas,
        "revisada": r.revisada,
        "motivo_abstencion": r.motivo_abstencion.value if r.motivo_abstencion else None,
        "preguntas_pendientes": r.preguntas_pendientes,
        "avisos": r.avisos,
        "modelo": r.modelo,
        "busqueda": r.busqueda,
    }


def _evento(nombre: str, datos: Any) -> str:
    return f"event: {nombre}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n"


def _resolver_municipio(valor: str | None) -> str | None:
    if not valor:
        return None
    from vut_andalucia.servicio import resolver_municipio

    try:
        return resolver_municipio(valor)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/preguntar")
async def api_preguntar(p: Pregunta, request: Request) -> StreamingResponse:
    if not limite.permitir(ip_cliente(request)):
        raise HTTPException(
            429, "Has llegado al límite de preguntas de la demo. Prueba en una hora."
        )
    municipio = _resolver_municipio(p.municipio)
    b = buscador()
    try:
        amplio = contexto_amplio(llm())
    except (LLMError, ValueError):
        amplio = False  # el error se comunica después, en el flujo
    prep: Preparacion = await asyncio.to_thread(
        preparar, p.pregunta.strip(), b, p.fecha, None, municipio, amplio
    )

    async def flujo() -> AsyncIterator[str]:
        yield _evento(
            "fuentes",
            {
                "fragmentos": [_fragmento_json(f, b.corpus.normas) for f in prep.fragmentos],
                "avisos": prep.avisos,
                "busqueda": prep.busqueda,
            },
        )
        previa = abstencion_previa(prep)
        if previa is not None:
            # La barrera de ámbito ya respondió: ni modelo ni cupo diario.
            yield _evento("respuesta", _respuesta_json(previa, b.corpus.normas))
            return
        try:
            modelo = llm()
        except (LLMError, ValueError) as e:
            log.warning("No se pudo crear el modelo: %s", e)
            yield _evento("error", {"mensaje": "El modelo de lenguaje no está disponible."})
            return
        if modelo is None or not cupo.consumir():
            yield _evento("solo_busqueda", {"mensaje": MENSAJE_SOLO_BUSQUEDA})
            return
        try:
            r = await asyncio.to_thread(completar, prep, modelo, REINTENTAR)
        except LLMError as e:
            log.warning("Fallo del modelo: %s", e)
            mensaje = "El modelo no pudo responder; inténtalo de nuevo." if PUBLICO else str(e)
            yield _evento("error", {"mensaje": mensaje})
            return
        except Exception:
            log.exception("Error inesperado al responder")
            yield _evento("error", {"mensaje": "Error inesperado al preparar la respuesta."})
            return
        yield _evento("respuesta", _respuesta_json(r, b.corpus.normas))

    return StreamingResponse(
        flujo(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@app.get("/api/buscar")
def api_buscar(
    request: Request,
    q: str = Query(min_length=2, max_length=MAX_CHARS_PREGUNTA),
    fecha: dt.date | None = None,
    municipio: str | None = Query(None, max_length=80),
    k: int = Query(8, ge=1, le=30),
) -> dict[str, Any]:
    if not limite_busqueda.permitir(ip_cliente(request)):
        raise HTTPException(429, "Demasiadas búsquedas; prueba en un rato.")
    b = buscador()
    res, modo = b.buscar_con_modo(
        q, fecha or dt.date.today(), k, municipio=_resolver_municipio(municipio)
    )
    return {
        "busqueda": modo,
        "resultados": [
            {"puntuacion": r.puntuacion, **_fragmento_json(r.fragmento, b.corpus.normas)}
            for r in res
        ],
    }


@app.get("/api/fragmento/{fragmento_id:path}")
def api_fragmento(fragmento_id: str) -> dict[str, Any]:
    """Un fragmento con la línea temporal de todas las versiones de ese precepto."""
    b = buscador()
    try:
        f = b.corpus.por_id(fragmento_id)
    except KeyError as e:
        raise HTTPException(404, f"No existe el fragmento {fragmento_id}") from e
    versiones = sorted(
        (g for g in b.corpus.fragmentos if g.base_id == f.base_id), key=lambda g: g.vigente_desde
    )
    return {
        **_fragmento_json(f, b.corpus.normas),
        "versiones": [_fragmento_json(g, b.corpus.normas) for g in versiones],
    }


@app.get("/api/fuentes")
def api_fuentes() -> dict[str, Any]:
    b = buscador()
    cuenta: dict[str, int] = {}
    for f in b.corpus.fragmentos:
        cuenta[f.norma_id] = cuenta.get(f.norma_id, 0) + 1
    return {
        "fuentes": [
            {**(_norma_json(n) or {}), "fragmentos": cuenta.get(n.id, 0)}
            for n in b.corpus.normas.values()
        ],
        "fragmentos": len(b.corpus.fragmentos),
    }


@app.get("/api/municipios")
def api_municipios() -> list[dict[str, Any]]:
    con_normas = {n.municipio for n in buscador().corpus.normas.values() if n.municipio}
    return [
        {
            "slug": m.slug,
            "nombre": m.nombre,
            "provincia": m.provincia,
            "con_normas": m.slug in con_normas,
        }
        for m in municipios.todos()
    ]


@app.get("/api/estado")
def api_estado() -> dict[str, Any]:
    b = buscador()
    return {
        "fuentes": len(b.corpus.normas),
        "fragmentos": len(b.corpus.fragmentos),
        "semantica": b.denso is not None,
        "modelo": modelo.nombre if (modelo := llm()) else "ninguno (solo búsqueda)",
        "hoy": dt.date.today().isoformat(),
    }


def _con_version(html: str, recurso: str) -> str:
    # La huella del contenido en la URL obliga al navegador a pedir el recurso nuevo tras cada
    # cambio, en vez de seguir usando la copia de su caché.
    huella = hashlib.sha256((WEB / recurso).read_bytes()).hexdigest()[:10]
    return html.replace(f"/static/{recurso}", f"/static/{recurso}?v={huella}")


@app.get("/", include_in_schema=False)
def inicio() -> HTMLResponse:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for recurso in ("app.css", "app.js"):
        html = _con_version(html, recurso)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=WEB), name="static")
