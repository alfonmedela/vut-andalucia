"""Los 785 municipios de Andalucía (INE, 1 de enero de 2026) y su detección en una pregunta.

Las limitaciones a las viviendas turísticas son, cada vez más, municipales: el mismo piso puede
inscribirse en un municipio y estar suspendido en el de al lado. Por eso el asistente necesita
saber de qué municipio se habla, y si no lo sabe y la respuesta depende de ello, lo pregunta.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from vut_andalucia.text import sin_tildes

RUTA = Path(__file__).parent / "data" / "municipios.json"
# Nombres que también son palabras corrientes: solo cuentan escritos con mayúscula inicial.
_AMBIGUOS = {
    "rota", "ojen", "lucena", "cuevas", "huelma", "pinos", "ronda", "marmolejo", "pizarra",
    "arenas", "alameda", "algarrobo", "canillas", "molvizar", "gines", "alhama",
}  # fmt: skip
# «María» (Almería) no es «Santa María» ni «Virgen María».
_PRECEDIDO_EXCLUYE = re.compile(r"(santa|virgen|santa maria de la|calle|avenida|plaza)\s+$")
# Una pregunta depende del municipio si trata de abrir, limitar o suspender, o de urbanismo.
_DEPENDE_DEL_MUNICIPIO = re.compile(
    r"\b(ayuntamiento|municipi\w*|ciudad|barrio|zona\w*|centro historico|casco|pgou|pgom|"
    r"ordenanza\w*|planeamiento|urbanistic\w*|licencia\w*|suspen\w*|limita\w*|prohib\w*|"
    r"moratoria|saturad\w*|tensionad\w*|puedo (poner|abrir|dar de alta|registrar|alquilar)|"
    r"se puede (poner|abrir|dar de alta)|abrir un\w*|montar un\w*|comprar un piso para)\b"
)


@dataclass(frozen=True)
class Municipio:
    slug: str
    nombre: str
    provincia: str
    ine: str


@cache
def todos() -> tuple[Municipio, ...]:
    return tuple(Municipio(**m) for m in json.loads(RUTA.read_text(encoding="utf-8")))


@cache
def por_slug() -> dict[str, Municipio]:
    return {m.slug: m for m in todos()}


@cache
def _patrones() -> list[tuple[re.Pattern[str], Municipio, bool]]:
    salida = []
    # Los nombres largos primero: «El Puerto de Santa María» antes que «Santa María».
    for m in sorted(todos(), key=lambda m: -len(m.nombre)):
        nombres = {m.nombre}
        sin_articulo = re.sub(r"^(El|La|Los|Las) ", "", m.nombre)
        if sin_articulo != m.nombre and len(sin_articulo) > 6:
            nombres.add(sin_articulo)
        for n in nombres:
            plano = sin_tildes(n.lower())
            exige_mayuscula = plano in _AMBIGUOS
            patron = re.compile(
                rf"(?<!\w){re.escape(sin_tildes(n) if exige_mayuscula else plano)}(?!\w)"
            )
            salida.append((patron, m, exige_mayuscula))
    return salida


def detectar(texto: str) -> list[Municipio]:
    """Municipios andaluces nombrados en el texto, sin repetir y en orden de aparición."""
    plano = sin_tildes(texto.lower())
    original = sin_tildes(texto)
    encontrados: list[tuple[int, Municipio]] = []
    ocupado: list[tuple[int, int]] = []
    for patron, m, exige_mayuscula in _patrones():
        for coincidencia in patron.finditer(original if exige_mayuscula else plano):
            ini, fin = coincidencia.span()
            if any(ini < b and a < fin for a, b in ocupado):
                continue  # parte de un nombre más largo ya detectado
            if _PRECEDIDO_EXCLUYE.search(plano[max(0, ini - 25) : ini]):
                continue
            ocupado.append((ini, fin))
            encontrados.append((ini, m))
    vistos: set[str] = set()
    salida = []
    for _, m in sorted(encontrados, key=lambda x: x[0]):
        if m.slug not in vistos:
            vistos.add(m.slug)
            salida.append(m)
    return salida


def depende_del_municipio(pregunta: str) -> bool:
    return _DEPENDE_DEL_MUNICIPIO.search(sin_tildes(pregunta.lower())) is not None
