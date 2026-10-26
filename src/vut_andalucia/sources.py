"""Catálogo de normas del corpus. Solo fuentes oficiales."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fuente:
    id: str
    corta: str
    fuente: str  # boe | boja | eurlex
    motivo: str


CATALOGO: tuple[Fuente, ...] = (
    Fuente(
        "BOE-A-2012-876",
        "Ley 13/2011",
        "boe",
        "Ley del Turismo de Andalucía: registro, deberes y régimen sancionador.",
    ),
    Fuente(
        "BOE-A-1960-10906",
        "LPH",
        "boe",
        "Propiedad horizontal: acuerdos de la comunidad sobre pisos turísticos (art. 17.12).",
    ),
    Fuente(
        "BOE-A-1994-26003",
        "LAU",
        "boe",
        "Arrendamientos urbanos: exclusión de la cesión turística (art. 5.e).",
    ),
)


def por_id(norma_id: str) -> Fuente:
    for f in CATALOGO:
        if f.id == norma_id:
            return f
    raise KeyError(f"Norma no catalogada: {norma_id}")
