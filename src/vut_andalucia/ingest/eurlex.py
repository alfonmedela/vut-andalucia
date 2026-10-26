"""Ingesta de reglamentos de la UE desde Cellar, el repositorio de la Oficina de Publicaciones.

EUR-Lex protege su web con un desafío anti-bots; Cellar sirve el mismo XHTML oficial por
negociación de contenido (`/resource/celex/<CELEX>` con `Accept: application/xhtml+xml`).
"""

from __future__ import annotations

import datetime as dt
import re
from html.parser import HTMLParser

import httpx

from vut_andalucia.ingest.comun import fragmentos_de_version
from vut_andalucia.models import Fragmento, Norma

CELLAR = "https://publications.europa.eu/resource/celex/{celex}"
EURLEX = "https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:{celex}"
_MARCA = re.compile(r"^(\(?\d{1,2}\)|\(?[a-z]\)|[ivx]+\))$")


class EurLexError(RuntimeError):
    pass


def descargar(celex: str, client: httpx.Client) -> bytes:
    r = client.get(
        CELLAR.format(celex=celex),
        headers={"Accept": "application/xhtml+xml", "Accept-Language": "spa"},
        follow_redirects=True,
    )
    if r.status_code != 200 or b"eli-subdivision" not in r.content:
        raise EurLexError(f"Cellar no devolvió el XHTML de {celex} ({r.status_code})")
    return r.content


class _Lector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.articulos: list[tuple[str, str, list[str]]] = []  # (id, encabezado, párrafos)
        self._pila: list[tuple[str, str]] = []  # (etiqueta, id de artículo si lo abre)
        self._art: tuple[str, list[str], list[str]] | None = None  # id, títulos, párrafos
        self._p: list[str] | None = None
        self._clase_p = ""
        self._nota = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag in ("br", "img", "col", "meta", "link", "hr"):
            return
        abre = ""
        if tag == "div" and re.fullmatch(r"art_\d+", a.get("id", "")) and self._art is None:
            self._art, abre = (a["id"], [], []), a["id"]
        self._pila.append((tag, abre))
        if tag == "a" and a.get("href", "").startswith("#ntr"):
            self._nota += 1  # llamada a nota al pie: no forma parte del texto citable
        elif tag == "p" and self._art is not None:
            self._p, self._clase_p = [], a.get("class", "")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("br", "img", "col", "meta", "link", "hr") or not self._pila:
            return
        etiqueta, abre = self._pila.pop()
        if etiqueta == "a" and self._nota:
            self._nota -= 1
        elif etiqueta == "p" and self._p is not None and self._art is not None:
            texto = re.sub(r"\s+", " ", "".join(self._p)).strip()
            if texto:
                _ident, titulos, parrafos = self._art
                es_titulo = self._clase_p in ("oj-ti-art", "oj-sti-art")
                (titulos if es_titulo else parrafos).append(texto)
            self._p = None
        elif abre and self._art is not None:
            ident, titulos, parrafos = self._art
            self.articulos.append((ident, " ".join(titulos), _une_marcas(parrafos)))
            self._art = None

    def handle_data(self, data: str) -> None:
        if self._p is not None and not self._nota:
            self._p.append(data)


def _une_marcas(parrafos: list[str]) -> list[str]:
    """En el DOUE, «1)» o «a)» van en su propia celda: se pegan al párrafo siguiente."""
    salida: list[str] = []
    pendiente = ""
    for p in parrafos:
        if _MARCA.match(p):
            pendiente = f"{pendiente}{p} "
            continue
        salida.append(pendiente + p)
        pendiente = ""
    return salida


def parsear(xhtml: bytes, norma: Norma, celex: str, desde: dt.date) -> list[Fragmento]:
    lector = _Lector()
    lector.feed(xhtml.decode("utf-8"))
    lector.close()
    if not lector.articulos:
        raise EurLexError(f"{celex}: no se encontraron artículos")
    fragmentos: list[Fragmento] = []
    for ident, encabezado, parrafos in lector.articulos:
        numero = ident.removeprefix("art_")
        fragmentos.extend(
            fragmentos_de_version(
                norma=norma,
                bloque_id=ident,
                base_cita=f"art. {numero}",
                encabezado=encabezado,
                parrafos=parrafos,
                ruta=[],
                desde=desde,
                hasta=None,
                url=f"{EURLEX.format(celex=celex)}#{ident}",
            )
        )
    return fragmentos
