"""Puente entre cómo pregunta la gente y cómo escribe el legislador.

Nadie pregunta por «la actividad a que se refiere la letra e) del artículo 5 de la Ley 29/1994»;
pregunta por «pisos turísticos». El glosario expande la consulta con el término legal y marca
en el índice los preceptos que aluden a la figura por remisión.
"""

from __future__ import annotations

import re

from vut_andalucia.text import sin_tildes

_VUT_LEGAL = "vivienda de uso turístico viviendas de uso turístico actividad turística"

# (patrón coloquial, términos legales añadidos a la consulta)
CONSULTA: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), t)
    for p, t in (
        (
            r"\b(pisos?|viviendas?|casas?) (turistic\w*|vacacional\w*|de alquiler turistic\w*)\b",
            _VUT_LEGAL,
        ),
        (
            r"\b(alquiler|alquilar|arrendar|arrendamiento)\w* "
            r"(turistic\w*|vacacional\w*|por dias)\b",
            _VUT_LEGAL,
        ),
        (r"\b(airbnb|booking|vrbo|vut|vft)\b", _VUT_LEGAL + " canales de oferta turística"),
        (r"\b(vecinos|comunidad de vecinos)\b", "comunidad de propietarios junta de propietarios"),
        (r"\b(multas?|sancion\w*|me pueden multar)\b", "sanción infracción multa"),
        (r"\blicencias?\b", "inscripción Registro de Turismo de Andalucía declaración responsable"),
    )
)

# Remisiones legales que, de hecho, regulan las viviendas de uso turístico.
_REMISIONES_VUT = re.compile(
    r"letra e\) del articulo 5 de la ley 29/1994|vivienda[s]? (de uso|con fines) turistic"
)


def expandir_consulta(consulta: str) -> str:
    plano = sin_tildes(consulta.lower())
    extra = [t for patron, t in CONSULTA if patron.search(plano)]
    return " ".join([consulta, *dict.fromkeys(extra)])


def alias_indexacion(texto: str) -> str:
    """Términos que se añaden al texto indexado (nunca al texto citable)."""
    return (
        _VUT_LEGAL + " pisos turísticos alquiler turístico"
        if _REMISIONES_VUT.search(sin_tildes(texto.lower()))
        else ""
    )
