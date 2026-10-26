"""Barrera de ámbito previa al modelo, determinista.

Un modelo al que se le dan fragmentos tiende a responder con ellos aunque no vengan al caso: a
una pregunta sobre Barcelona le aplica la norma andaluza. Aquí se deciden, sin LLM, tres cosas:
si la pregunta intenta dar instrucciones al asistente, si trata de otra comunidad o país y si
pregunta por normas de un municipio que el corpus no tiene.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from vut_andalucia.text import sin_tildes

# Peticiones que intentan cambiar el comportamiento del asistente. Se buscan sobre el texto en
# minúsculas y sin tildes. Deben dirigirse al asistente («tus instrucciones», «las
# instrucciones anteriores»): «el huésped ignora las normas de la comunidad» es una pregunta.
_VERBO = r"\b(ignora|olvida|omite|desobedece|saltate)\b\s+(todas\s+|todo\s+)?"
_ORDENES = r"(instrucciones|reglas|indicaciones|normas|fuentes|ordenes)"
_INSTRUCCIONES = re.compile(
    _VERBO + r"(tus|tu)\s+" + _ORDENES
    + r"|" + _VERBO + r"(las|lo|mis)\s+(" + _ORDENES + r"\s+)?(anteriores|previas|del sistema|"
    r"iniciales|que te (han dado|dieron))"
    + r"|\ba partir de ahora\b[^.?!]{0,30}\b(responde|contesta|eres|actua)\b"
    r"|\b(responde|contesta|respondeme|contestame)\b[^.?!]{0,20}\bsin citar\b"
    r"|\bmodo desarrollador\b|\bsystem prompt\b|\bprompt del sistema\b"
    r"|\bignore (all |previous |your )*(instructions|rules)\b"
)  # fmt: skip

# Otras comunidades autónomas, sus capitales y ciudades con regulación propia de pisos
# turísticos. Una pregunta que solo habla de ellas está fuera del corpus andaluz.
_OTRAS_CCAA = (
    "cataluna", "catalunya", "barcelona", "girona", "tarragona", "lleida", "sitges", "salou",
    "comunidad valenciana", "comunitat valenciana", "valencia", "alicante", "alacant", "elche",
    "torrevieja", "denia", "castellon", "benidorm", "comunidad de madrid", "madrid", "baleares",
    "illes balears", "mallorca", "palma de mallorca", "ibiza", "eivissa", "menorca", "canarias",
    "tenerife", "gran canaria", "las palmas", "lanzarote", "fuerteventura", "pais vasco",
    "euskadi", "bilbao", "san sebastian", "donostia", "vitoria", "galicia",
    "santiago de compostela", "vigo", "a coruna", "lugo", "ourense", "pontevedra", "asturias",
    "oviedo", "gijon", "cantabria", "navarra", "pamplona", "la rioja", "logrono", "aragon",
    "zaragoza", "huesca", "teruel", "castilla y leon", "valladolid", "salamanca", "leon",
    "burgos", "segovia", "avila", "soria", "zamora", "palencia", "castilla-la mancha",
    "castilla la mancha", "toledo", "cuenca", "albacete", "guadalajara", "ciudad real",
    "extremadura", "badajoz", "caceres", "region de murcia", "murcia", "cartagena", "ceuta",
    "melilla",
)  # fmt: skip
_OTROS_PAISES = (
    "francia", "portugal", "italia", "alemania", "reino unido", "inglaterra", "irlanda",
    "belgica", "paises bajos", "holanda", "suiza", "estados unidos", "eeuu", "mexico",
    "argentina", "marruecos",
)  # fmt: skip
_ANDALUCIA = re.compile(
    r"\b(andalu(c|z)\w*|boja|almeria|cadiz|cordoba|granada|huelva|jaen|malaga|sevilla)\b"
)
# El territorio cuenta si la pregunta sitúa algo en él («en Barcelona», «de Madrid»), no si
# aparece en el nombre de una calle, un banco o el lugar donde vive quien pregunta.
_TERRITORIO = re.compile(
    r"\b(en|de|del|desde)\s+(la\s+|el\s+)?(provincia\s+de\s+|comunidad\s+de\s+|isla\s+de\s+)?("
    + "|".join(map(re.escape, _OTRAS_CCAA)) + r")\b"
)  # fmt: skip
_NO_ES_LUGAR = re.compile(
    r"\b(avenida|avda|calle|c/|plaza|paseo|carretera|ronda|banco|real|atletico|universidad|"
    r"vivo|resido|soy|residente|empadronad[oa]|nacid[oa]|vengo)\b[^,.;?]{0,12}$"
)
# Comparar Andalucía con otra comunidad sigue siendo una pregunta sobre Andalucía.
_PAIS = re.compile(r"\b(" + "|".join(map(re.escape, _OTROS_PAISES)) + r")\b")
# Preguntas por lo que dice un instrumento local concreto (no por lo que puede hacer un
# ayuntamiento en general, que responde el art. 6 del Decreto-ley 1/2025).
_NORMA_MUNICIPAL = re.compile(
    r"\b(ordenanzas?|moratoria|pgou|plan general|bop|boletin (oficial )?de la provincia)\b"
    r"|\b(que|cuales)\s+(zonas|barrios|distritos)\b"
    r"|\b(zonas|barrios|distritos)\b[^.?]{0,40}\b(suspendid\w*|saturad\w*|limitad\w*)\b"
)


@dataclass(frozen=True)
class Ambito:
    """Resultado de la barrera: `motivo` es None si la pregunta puede seguir adelante."""

    motivo: str | None = None
    aviso: str | None = None
    # Instrucción extra para el modelo cuando la pregunta sigue pero con una cautela.
    cautela: str | None = None


def _plano(texto: str) -> str:
    return sin_tildes(texto.lower())


def intenta_instruir(pregunta: str) -> bool:
    return _INSTRUCCIONES.search(_plano(pregunta)) is not None


def otro_territorio(pregunta: str, municipio_andaluz: bool) -> str | None:
    """Nombre del territorio no andaluz del que trata la pregunta, si es el único."""
    plano = _plano(pregunta)
    if municipio_andaluz or _ANDALUCIA.search(plano):
        return None
    for m in _TERRITORIO.finditer(plano):
        if not _NO_ES_LUGAR.search(plano[: m.start()]):
            return m.group(4)
    return None


def otro_pais(pregunta: str) -> str | None:
    m = _PAIS.search(_plano(pregunta))
    return m.group(1) if m else None


def evaluar(pregunta: str, municipio_andaluz: bool, municipio_sin_normas: str | None) -> Ambito:
    """`municipio_andaluz`: la pregunta nombra o fija un municipio andaluz.
    `municipio_sin_normas`: nombre de ese municipio si el corpus no tiene normas suyas."""
    if intenta_instruir(pregunta):
        return Ambito(
            motivo="instrucciones",
            aviso="La pregunta contiene instrucciones para el asistente. Solo respondo preguntas "
            "sobre la normativa, siempre con citas del texto oficial.",
        )
    territorio = otro_territorio(pregunta, municipio_andaluz)
    if territorio:
        return Ambito(
            motivo="otro_territorio",
            aviso=f"La pregunta parece referirse a {territorio.title()}, fuera de Andalucía. "
            "Este asistente solo contiene la normativa andaluza, la estatal y la europea, y "
            "aplicarla a otra comunidad podría inducir a error.",
        )
    if municipio_sin_normas and _NORMA_MUNICIPAL.search(_plano(pregunta)):
        return Ambito(
            motivo="municipio_sin_normas",
            aviso=f"La pregunta trata de normas propias de {municipio_sin_normas}, y el corpus no "
            "contiene ninguna de ese municipio. Que no estén aquí no significa que no existan: "
            "consulta el planeamiento, las ordenanzas y el boletín oficial de la provincia.",
        )
    pais = otro_pais(pregunta)
    if pais:
        return Ambito(
            cautela=f"La pregunta menciona {pais.title()}. El corpus no contiene normas de otros "
            "países ni convenios de doble imposición: no afirmes nada sobre ellos y, si la "
            "respuesta depende de esa normativa, devuelve fuera_de_ambito."
        )
    return Ambito()
