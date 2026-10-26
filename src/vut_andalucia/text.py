"""Normalización de texto legal en español: tokens para BM25, comparación de citas y numeración."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import snowballstemmer  # type: ignore[import-untyped]

_STOPWORDS = frozenset(
    """a al algo algun alguna algunas alguno algunos ante antes como con contra cual cuales cuando
    de del desde donde durante e el ella ellas ellos en entre era es esa esas ese eso esos esta
    estas este esto estos fue fueron ha han hasta hay la las le les lo los mas me mi mis mucho muy
    ni no nos o os otra otras otro otros para pero poco por porque que quien se sea segun ser si
    sin sobre su sus tambien tanto te tiene tienen todo todos tu un una unas uno unos y ya yo
    """.split()
)

_UNIDADES = {
    "uno": 1,
    "primero": 1,
    "dos": 2,
    "segundo": 2,
    "tres": 3,
    "tercero": 3,
    "cuatro": 4,
    "cuarto": 4,
    "cinco": 5,
    "quinto": 5,
    "seis": 6,
    "sexto": 6,
    "siete": 7,
    "septimo": 7,
    "ocho": 8,
    "octavo": 8,
    "nueve": 9,
    "noveno": 9,
    "diez": 10,
    "decimo": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
}
_DECENAS = {
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}
_ORDINALES_DISP = {
    "primera": 1,
    "segunda": 2,
    "tercera": 3,
    "cuarta": 4,
    "quinta": 5,
    "sexta": 6,
    "septima": 7,
    "octava": 8,
    "novena": 9,
    "decima": 10,
    "undecima": 11,
    "duodecima": 12,
}


def sin_tildes(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def _stemmer() -> snowballstemmer.stemmer:
    return snowballstemmer.stemmer("spanish")


def tokens(texto: str) -> list[str]:
    """Tokens en minúscula, sin tildes ni palabras vacías, reducidos a su raíz."""
    palabras = re.findall(r"\w+", sin_tildes(texto.lower()))
    utiles = [p for p in palabras if p not in _STOPWORDS and (len(p) > 1 or p.isdigit())]
    return list(_stemmer().stemWords(utiles))


def normaliza_cita(texto: str) -> str:
    """Forma canónica para comparar citas: ignora espacios, comillas y mayúsculas."""
    texto = unicodedata.normalize("NFKC", texto).lower()
    texto = re.sub(r"[«»“”\"'‘’]", "", texto)
    texto = re.sub(r"[‐‑‒–—―]", "-", texto)
    return re.sub(r"\s+", " ", texto).strip(" .;,:")


def numero_en_palabras(palabras: str) -> int | None:
    """'diecisiete' -> 17, 'treinta y dos' -> 32, '48' -> 48."""
    p = sin_tildes(palabras.lower()).strip(" .")
    if p.isdigit():
        return int(p)
    if p in _UNIDADES:
        return _UNIDADES[p]
    m = re.fullmatch(r"(\w+)(?: y (\w+))?", p)
    if m and m.group(1) in _DECENAS:
        resto = _UNIDADES.get(m.group(2) or "", 0) if m.group(2) else 0
        if m.group(2) and not resto:
            return None
        return _DECENAS[m.group(1)] + resto
    return None


def cita_de_bloque(titulo: str) -> str:
    """Convierte el título de un bloque del BOE en una cita corta.

    'Artículo diecisiete' -> 'art. 17'; 'Artículo 48 bis' -> 'art. 48 bis';
    'Disposición adicional segunda' -> 'disp. adic. 2.ª'.
    """
    t = titulo.strip().rstrip(".")
    m = re.match(r"(?i)art[íi]culo\s+(.+)", t)
    if m:
        resto = m.group(1)
        sufijo = ""
        ms = re.match(r"(.+?)\s+(bis|ter|quater|quinquies)$", resto, re.I)
        if ms:
            resto, sufijo = ms.group(1), " " + ms.group(2).lower()
        n = numero_en_palabras(resto)
        return f"art. {n if n is not None else resto}{sufijo}"
    m = re.match(r"(?i)disposici[óo]n\s+(adicional|transitoria|derogatoria|final)\s*(\w*)", t)
    if m:
        tipo = {
            "adicional": "adic.",
            "transitoria": "trans.",
            "derogatoria": "derog.",
            "final": "final",
        }[sin_tildes(m.group(1).lower())]
        ordinal = _ORDINALES_DISP.get(sin_tildes(m.group(2).lower()))
        if m.group(2) and m.group(2).lower() != "única" and ordinal is None:
            return f"disp. {tipo} {m.group(2)}"
        return f"disp. {tipo} {f'{ordinal}.ª' if ordinal else 'única'}"
    return t
