"""Ingesta de los textos consolidados del BOJA (sede electrónica de la Junta de Andalucía).

La Junta publica cada versión consolidada de una norma como HTML, pero solo a través de un
portal JSF que exige navegador. Por eso la ingesta va en dos pasos:

1. `descargar_versiones` (necesita Playwright, extra opcional `boja`) guarda el HTML de cada
   versión en `data/fuentes/boja/<id>/<AAAA-MM-DD>.html`, junto al repositorio.
2. `parsear` lee esos HTML guardados, sin red, y reconstruye cada precepto con su vigencia.

Así el corpus se puede regenerar y auditar en cualquier máquina, y el CI no necesita navegador.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from vut_andalucia.ingest.comun import fragmentos_de_version, fusionar
from vut_andalucia.models import Fragmento, Norma
from vut_andalucia.text import cita_de_bloque

FICHA = (
    "https://ws040.juntadeandalucia.es/sedeboja/es/web/textos-consolidados/resumen-ficha"
    "?p_p_id=resumenrecursolegal_WAR_sedebojatextoconsolidadoportlet&p_p_lifecycle=0"
    "&_resumenrecursolegal_WAR_sedebojatextoconsolidadoportlet_recursoLegalAbstractoId={id}"
)
_BLOQUE = re.compile(r"^(ART|DADIC|DTRANS|DFIN|DDER|ANEXO)\w*$")
_MESES = {
    m: i
    for i, m in enumerate(
        "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre "
        "diciembre".split(),
        1,
    )
}

log = logging.getLogger(__name__)


class BOJAError(RuntimeError):
    pass


class _Bloque:
    def __init__(self, ident: str, ruta: list[str]) -> None:
        self.id = ident
        self.ruta = ruta
        self.titulo = ""
        self.parrafos: list[str] = []
        self.notas: list[str] = []


class _Lector(HTMLParser):
    """Recorre el HTML y separa preceptos, rúbricas, párrafos y notas de redacción."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bloques: list[_Bloque] = []
        self.ruta: dict[str, str] = {}
        self._pila: list[str] = []  # etiquetas abiertas, con marcas de contexto
        self._actual: _Bloque | None = None
        self._prof_bloque = 0
        self._prof_nota = 0
        self._buf: list[str] | None = None
        self._destino = ""
        self._cabecera: list[str] | None = None
        self._nivel = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "br" and self._buf is not None:
            self._buf.append(" ")
            return
        if tag in ("br", "img", "input", "meta", "link", "hr"):
            return
        self._pila.append(tag)
        if tag == "div":
            if self._actual is None and _BLOQUE.match(a.get("id", "")):
                self._actual = _Bloque(a["id"], list(self.ruta.values()))
                self._prof_bloque = len(self._pila)
            elif self._actual is not None and "ccn" in a.get("class", "").split():
                self._cierra_parrafo()
                self._prof_nota = len(self._pila)
                self._buf, self._destino = [], "nota"
        elif tag in ("h3", "h4") and self._actual is None:
            self._cabecera, self._nivel = [], a.get("class", "") or tag
        elif tag in ("p", "li") and self._actual is not None and not self._prof_nota:
            # Un <p> dentro de un <li> cierra el texto que el <li> llevaba hasta ahí.
            self._cierra_parrafo()
            self._buf = []
            self._destino = "titulo" if a.get("class") == "a" else "parrafo"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("br", "img", "input", "meta", "link", "hr") or not self._pila:
            return
        profundidad = len(self._pila)
        self._pila.pop()
        if tag in ("p", "li") and self._buf is not None and self._destino in ("titulo", "parrafo"):
            self._cierra_parrafo()
        elif tag == "div" and self._prof_nota == profundidad:
            if self._buf is not None and self._actual is not None:
                self._actual.notas.append(_limpia("".join(self._buf)))
            self._prof_nota, self._buf = 0, None
        elif tag == "div" and self._actual is not None and self._prof_bloque == profundidad:
            self.bloques.append(self._actual)
            self._actual = None
        elif tag in ("h3", "h4") and self._cabecera is not None:
            texto = _limpia("".join(self._cabecera))
            if self._nivel == "ca" and texto.upper().startswith(("CAPÍTULO", "TÍTULO")):
                clave = "titulo" if texto.upper().startswith("TÍTULO") else "capitulo"
                self.ruta[clave] = texto
                if clave == "titulo":
                    self.ruta.pop("capitulo", None)
            else:
                self.ruta.clear()  # «Disposiciones adicionales» y similares: fuera de capítulos
            self._cabecera = None

    def _cierra_parrafo(self) -> None:
        if self._buf is None or self._destino not in ("titulo", "parrafo"):
            return
        texto = _limpia("".join(self._buf))
        if texto and self._actual is not None:
            if self._destino == "titulo" and not self._actual.titulo:
                self._actual.titulo = texto
            else:
                self._actual.parrafos.append(texto)
        self._buf = None

    def handle_data(self, data: str) -> None:
        if self._buf is not None:
            self._buf.append(data)
        elif self._cabecera is not None:
            self._cabecera.append(data + " ")


def _limpia(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.replace("\xa0", " ")).strip()


def fecha_de_version(html: str) -> dt.date:
    """La fecha de vigencia que la Junta imprime en la cabecera de la versión."""
    plano = _limpia(re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"Vigencia:\s*(\d{1,2})/(\d{1,2})/(\d{4})", plano)
    if not m:
        raise BOJAError("La versión no indica su fecha de vigencia")
    d, mes, a = (int(x) for x in m.groups())
    return dt.date(a, mes, d)


def _fecha_texto(texto: str) -> dt.date | None:
    m = re.search(r"(\d{1,2}) (\w+) (\d{4})", texto)
    if not m or m.group(2).lower() not in _MESES:
        return None
    return dt.date(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1)))


def leer_version(html: str) -> list[_Bloque]:
    lector = _Lector()
    lector.feed(html)
    lector.close()
    if not lector.bloques:
        raise BOJAError("No se encontró ningún precepto en el HTML")
    return lector.bloques


_AVISO = re.compile(
    r"anulad|nulidad|suspendid|suspensi[oó]n|derogad|t[ée]ngase en cuenta|v[ée]ase", re.I
)


def _avisos(notas: list[str]) -> str | None:
    """Notas que advierten de una anulación o suspensión: se muestran con la cita."""
    vigentes = [n for n in notas if _AVISO.search(n) and not n.startswith("A partir de")]
    return " ".join(vigentes) or None


def _redaccion(notas: list[str]) -> str | None:
    """La nota que explica la redacción vigente de esta versión.

    Las notas «A partir de: …» anuncian una redacción futura y no describen la versión actual.
    """
    actuales = [n for n in notas if not n.startswith("A partir de") and not _AVISO.search(n)]
    return actuales[0] if actuales else None


def _base_cita(bloque: _Bloque) -> tuple[str, str]:
    """(cita base, encabezado) a partir de «Artículo 9 Inscripción» o «Disposición…»."""
    m = re.match(
        r"^((?:Artículo|Disposición (?:adicional|transitoria|final|derogatoria)) \S+)\s*(.*)$",
        bloque.titulo,
    )
    if not m:
        return cita_de_bloque(bloque.titulo or bloque.id), bloque.titulo
    return cita_de_bloque(m.group(1)), bloque.titulo


def parsear(directorio: Path, norma: Norma, url: str) -> list[Fragmento]:
    """Reconstruye la historia de cada precepto a partir de las versiones guardadas."""
    versiones = sorted(
        ((fecha_de_version(p.read_text(encoding="utf-8")), p) for p in directorio.glob("*.html")),
        key=lambda x: x[0],
    )
    if not versiones:
        raise BOJAError(f"No hay versiones guardadas en {directorio}")
    fechas = [f for f, _ in versiones]
    if len(set(fechas)) != len(fechas):
        raise BOJAError(f"{norma.id}: dos versiones con la misma vigencia: {fechas}")
    crudos: list[Fragmento] = []
    for i, (desde, ruta_html) in enumerate(versiones):
        hasta = fechas[i + 1] if i + 1 < len(fechas) else None
        for b in leer_version(ruta_html.read_text(encoding="utf-8")):
            if not b.parrafos:
                continue
            if len(b.parrafos) == 1 and re.fullmatch(
                r"\(?\s*(derogad[oa]|anulad[oa]|suprimid[oa]|sin contenido)\s*\)?\.?",
                b.parrafos[0],
                re.I,
            ):
                continue
            base, encabezado = _base_cita(b)
            es_disposicion = not b.id.startswith("ART")
            crudos.extend(
                fragmentos_de_version(
                    norma=norma,
                    bloque_id=b.id.lower(),
                    base_cita=base,
                    encabezado=encabezado,
                    parrafos=b.parrafos,
                    ruta=[] if es_disposicion else b.ruta,
                    desde=desde,
                    hasta=hasta,
                    url=url,
                    modificado_por=_redaccion(b.notas),
                    nota=_avisos(b.notas),
                )
            )
    # El orden por bloque y fecha es el que espera `fusionar`.
    crudos.sort(key=lambda f: (f.base_id, f.vigente_desde))
    fragmentos = fusionar(crudos)
    repetidos = [i for i, n in Counter(f.id for f in fragmentos).items() if n > 1]
    if repetidos:
        raise BOJAError(f"{norma.id}: identificadores repetidos: {sorted(repetidos)[:5]}")
    return fragmentos


def descargar_versiones(recurso_id: str, destino: Path) -> list[Path]:  # pragma: no cover
    """Guarda el HTML de todas las versiones consolidadas. Requiere `uv sync --extra boja`."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BOJAError(
            "Para descargar del BOJA hace falta Playwright: `uv sync --extra boja` y "
            "`uv run playwright install chromium`"
        ) from e
    selector = "select[id$=':frm:version_input']"
    url = FICHA.format(id=recurso_id)
    destino.mkdir(parents=True, exist_ok=True)
    guardadas: list[Path] = []
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        ctx = navegador.new_context()
        pagina = ctx.new_page()
        pagina.goto(url, wait_until="networkidle", timeout=90_000)
        n = len(pagina.query_selector_all(selector + " option"))
        if n == 0:
            raise BOJAError(f"El BOJA no muestra versiones para el recurso {recurso_id}")
        for i in range(n):
            pagina.goto(url, wait_until="networkidle", timeout=90_000)
            if i:
                pagina.evaluate(
                    """([sel, i]) => { const s = document.querySelector(sel); s.selectedIndex = i;
                    s.dispatchEvent(new Event('change', {bubbles: true})); }""",
                    [selector, i],
                )
                pagina.wait_for_load_state("networkidle")
                pagina.wait_for_timeout(1500)
            with ctx.expect_page(timeout=60_000) as nueva:
                pagina.get_by_text("HTML", exact=True).first.click()
            version = nueva.value
            version.wait_for_load_state("networkidle")
            html = version.content()
            fecha = fecha_de_version(html)
            ruta = destino / f"{fecha.isoformat()}.html"
            ruta.write_text(html, encoding="utf-8")
            guardadas.append(ruta)
            version.close()
        navegador.close()
    log.info("BOJA %s: %d versiones guardadas en %s", recurso_id, len(guardadas), destino)
    return guardadas
