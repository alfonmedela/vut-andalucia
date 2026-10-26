"""Catálogo de fuentes del corpus. Solo fuentes oficiales.

El catálogo vive en `data/fuentes.yaml` para que añadir una norma, una sentencia o la
ordenanza de un municipio sea editar un archivo, no tocar código.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from vut_andalucia.models import Ambito, Clase

RUTA_CATALOGO = Path(__file__).parent / "data" / "fuentes.yaml"
TIPOS = ("boe", "boe_diario", "boja", "eurlex", "documento")


@dataclass(frozen=True)
class Fuente:
    id: str
    corta: str
    fuente: str  # ver TIPOS
    motivo: str
    territorio: Ambito = Ambito.ESTATAL
    clase: Clase = Clase.NORMA
    municipio: str | None = None
    # Solo estos bloques (p. ej. el art. 20 de la Ley del IVA). Vacío = toda la norma.
    bloques: tuple[str, ...] = ()
    estado: str | None = None
    origen: str | None = None  # id del BOJA, CELEX o URL del documento
    titulo: str | None = None
    rango: str | None = None
    cita: str | None = None  # base de la cita para documentos sin artículos («fallo»)
    desde: dt.date | None = None  # vigencia cuando la fuente no la publica
    hasta: dt.date | None = None
    # Cómo la nombra la gente («ley de arrendamientos urbanos», «iva»): si la pregunta la
    # menciona, sus fragmentos suben en la búsqueda.
    alias: tuple[str, ...] = ()
    # Cómo trocear un documento: por artículos (defecto), por preguntas (FAQ oficiales) o
    # por fundamentos de derecho (sentencias).
    troceo: str = "articulos"
    # En un boletín con muchos anuncios, el texto del anuncio que interesa: empieza en
    # `extracto` y acaba en `extracto_fin` (o en la raya que separa anuncios en los BOP).
    extracto: str | None = None
    extracto_fin: str | None = None
    # La fecha de efectos no consta con certeza en la fuente: la respuesta lo advierte.
    incierta: bool = False


class CatalogoInvalido(ValueError):
    pass


_CAMPOS = set(Fuente.__dataclass_fields__)


def _fuente(datos: dict[str, Any]) -> Fuente:
    ident = datos.get("id", "?")
    faltan = {"id", "corta", "fuente", "motivo"} - datos.keys()
    if faltan:
        raise CatalogoInvalido(f"{ident}: faltan {sorted(faltan)}")
    desconocidos = set(datos) - _CAMPOS
    if desconocidos:
        raise CatalogoInvalido(f"{ident}: campos desconocidos {sorted(desconocidos)}")
    if datos["fuente"] not in TIPOS:
        raise CatalogoInvalido(f"{ident}: fuente desconocida {datos['fuente']!r}")
    valores = dict(datos)
    try:
        valores["territorio"] = Ambito(valores.get("territorio", "estatal"))
        valores["clase"] = Clase(valores.get("clase", "norma"))
    except ValueError as e:
        raise CatalogoInvalido(f"{ident}: {e}") from e
    valores["bloques"] = tuple(valores.get("bloques", ()))
    valores["alias"] = tuple(str(a).lower() for a in valores.get("alias", ()))
    for clave in ("desde", "hasta"):
        if isinstance(valores.get(clave), str):
            valores[clave] = dt.date.fromisoformat(valores[clave])
    if valores["territorio"] == Ambito.MUNICIPAL and not valores.get("municipio"):
        raise CatalogoInvalido(f"{ident}: una fuente municipal necesita `municipio`")
    if datos["fuente"] in ("boja", "eurlex", "documento") and not valores.get("origen"):
        raise CatalogoInvalido(f"{ident}: la fuente {datos['fuente']} necesita `origen`")
    if valores.get("troceo", "articulos") not in ("articulos", "preguntas", "fundamentos"):
        raise CatalogoInvalido(f"{ident}: troceo desconocido {valores['troceo']!r}")
    if datos["fuente"] in ("eurlex", "documento", "boe_diario") and not valores.get("desde"):
        raise CatalogoInvalido(f"{ident}: indica `desde` (la fuente no publica la vigencia)")
    return Fuente(**valores)


def cargar_catalogo(ruta: Path = RUTA_CATALOGO) -> tuple[Fuente, ...]:
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or []
    fuentes = tuple(_fuente(d) for d in datos)
    vistos: set[str] = set()
    for f in fuentes:
        if f.id in vistos:
            raise CatalogoInvalido(f"Fuente repetida en el catálogo: {f.id}")
        vistos.add(f.id)
    return fuentes


CATALOGO: tuple[Fuente, ...] = cargar_catalogo()


def por_id(norma_id: str) -> Fuente:
    for f in CATALOGO:
        if f.id == norma_id:
            return f
    raise KeyError(f"Norma no catalogada: {norma_id}")
