"""Documentos oficiales sueltos: ordenanzas y acuerdos municipales (BOP), sentencias, criterios.

No tienen API. El documento (PDF o HTML) se guarda en `data/fuentes/documentos/` para que el
corpus sea reproducible aunque la web original cambie o desaparezca, y su sha256 queda en el
manifiesto. El texto se trocea por artículos si los tiene, por fundamentos de derecho si es
una sentencia, o por párrafos en otro caso.
"""

from __future__ import annotations

import hashlib
import io
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

import httpx

from vut_andalucia.ingest.comun import fragmentos_de_version
from vut_andalucia.models import Fragmento, Norma
from vut_andalucia.text import cita_de_bloque

if TYPE_CHECKING:
    from vut_andalucia.sources import Fuente

# «Artículo 3.», «Artículo 3 bis.», «Art. 3.-», «Disposición transitoria primera.»
_ENCABEZADO = re.compile(
    r"^(?P<cab>(?:Art(?:ículo|\.)\s+\d+(?:\s+(?:bis|ter|quater))?|"
    r"Disposici[óo]n\s+(?:adicional|transitoria|final|derogatoria)\s+\w+))"
    r"\s*[.\-–:]*\s*(?P<resto>.*)$",
    re.I,
)
# «PRIMERO.-», «SEGUNDO.» en fundamentos de derecho; «FALLO» / «FALLAMOS».
_FUNDAMENTO = re.compile(
    r"^(?P<cab>(?:PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|S[ÉE]PTIMO|OCTAVO|NOVENO|"
    r"D[ÉE]CIMO\w*|UND[ÉE]CIMO|DUOD[ÉE]CIMO)|FALLO|FALLAMOS)\b\s*[.\-–:]*\s*(?P<resto>.*)$"
)
_ORDINALES_FJ = {
    "PRIMERO": 1, "SEGUNDO": 2, "TERCERO": 3, "CUARTO": 4, "QUINTO": 5, "SEXTO": 6,
    "SEPTIMO": 7, "SÉPTIMO": 7, "OCTAVO": 8, "NOVENO": 9, "DECIMO": 10, "DÉCIMO": 10,
    "UNDECIMO": 11, "UNDÉCIMO": 11, "DUODECIMO": 12, "DUODÉCIMO": 12,
}  # fmt: skip


MAX_BYTES_COPIA = 2_000_000
MAX_BYTES_DOCUMENTO = 40_000_000
NAVEGADOR = (
    "Mozilla/5.0 (compatible; vut-andalucia/0.1; +https://github.com/alfonmedela/vut-andalucia)"
)


class DocumentoError(RuntimeError):
    pass


def _local(origen: str, dir_fuentes: Path) -> Path:
    """Ruta donde se guarda (o se espera) la copia del documento, siempre dentro de la carpeta."""
    base = (dir_fuentes / "documentos").resolve()
    if origen.startswith("local:"):
        ruta = (base / origen.removeprefix("local:")).resolve()
        if not ruta.is_relative_to(base):
            raise DocumentoError(f"Ruta local fuera de la carpeta de documentos: {origen}")
        return ruta
    nombre = Path(urlparse(origen).path).name or "documento"
    huella = hashlib.sha256(origen.encode()).hexdigest()[:10]
    return dir_fuentes / "documentos" / f"{huella}-{nombre}"


def url_publica(origen: str) -> str:
    return "" if origen.startswith("local:") else origen


def obtener(origen: str, client: httpx.Client, dir_fuentes: Path) -> bytes:
    """La copia guardada si existe; si no, se descarga y se guarda."""
    ruta = _local(origen, dir_fuentes)
    if ruta.exists():
        return ruta.read_bytes()
    if origen.startswith("local:"):
        raise DocumentoError(f"No existe la copia local {ruta}")
    if not origen.startswith("https://"):
        raise DocumentoError(f"Solo se descargan documentos por https: {origen}")
    # Algunas sedes oficiales rechazan clientes que no se identifican como navegador.
    r = client.get(origen, follow_redirects=True, headers={"User-Agent": NAVEGADOR})
    if r.url.scheme != "https":
        raise DocumentoError(f"{origen} redirigió a una dirección no segura: {r.url}")
    if r.status_code != 200 or not r.content:
        raise DocumentoError(f"{origen} respondió {r.status_code}")
    if len(r.content) > MAX_BYTES_DOCUMENTO:
        raise DocumentoError(f"{origen} supera {MAX_BYTES_DOCUMENTO // 1_000_000} MB")
    # Se guarda copia de lo que cabe en el repositorio; de un boletín entero de varios MB solo
    # importa un anuncio, y su huella queda en el manifiesto.
    if len(r.content) <= MAX_BYTES_COPIA:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(r.content)
    return r.content


class _Texto(HTMLParser):
    _BLOQUES: ClassVar[set[str]] = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "tr", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.trozos: list[str] = []
        self._omitir = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "nav", "header", "footer"):
            self._omitir += 1
        if tag in self._BLOQUES:
            self.trozos.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "header", "footer") and self._omitir:
            self._omitir -= 1
        if tag in self._BLOQUES:
            self.trozos.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._omitir:
            self.trozos.append(data)


def texto_plano(bruto: bytes) -> str:
    if bruto[:5] == b"%PDF-":
        from pypdf import PdfReader

        lector = PdfReader(io.BytesIO(bruto))
        paginas = [p.extract_text() or "" for p in lector.pages]
        texto = "\n".join(paginas)
        if len(texto.strip()) < 200:
            raise DocumentoError("El PDF no tiene capa de texto (¿escaneado?); hace falta OCR")
        return texto
    lector_html = _Texto()
    lector_html.feed(bruto.decode("utf-8", errors="replace"))
    return "".join(lector_html.trozos)


def parrafos(texto: str) -> list[str]:
    """Une las líneas cortadas del PDF en párrafos y quita cabeceras de página repetidas."""
    lineas = [re.sub(r"\s+", " ", ln).strip() for ln in texto.splitlines()]
    frecuencia: dict[str, int] = {}
    for ln in lineas:
        if ln:
            frecuencia[ln] = frecuencia.get(ln, 0) + 1
    salida: list[str] = []
    actual = ""
    for ln in lineas:
        es_ruido = not ln or (frecuencia[ln] >= 3 and len(ln) < 120) or re.fullmatch(r"\d+", ln)
        if es_ruido:
            if actual and not ln:
                salida.append(actual)
                actual = ""
            continue
        nuevo = bool(
            _ENCABEZADO.match(ln) or _FUNDAMENTO.match(ln) or re.match(r"^\d{1,2}\.\s", ln)
        )
        if actual and (nuevo or actual.endswith((".", ":", ";"))):
            salida.append(actual)
            actual = ln
        else:
            actual = f"{actual} {ln}".strip()
            actual = re.sub(r"(\w)- (\w)", r"\1\2", actual)  # guiones de fin de línea
    if actual:
        salida.append(actual)
    return salida


def _preguntas(ps: list[str]) -> list[tuple[str, str, list[str]]]:
    """FAQ oficial: cada pregunta (párrafo que termina en «?») con su respuesta."""
    bloques: list[tuple[str, str, list[str]]] = [("intro", "introducción", [])]
    for p in ps:
        if p.endswith("?") and len(p) < 400:
            n = len(bloques)
            bloques.append((f"p{n}", f"pregunta {n}", [p]))
        else:
            bloques[-1][2].append(p)
    return [b for b in bloques if b[0] != "intro" and len(b[2]) > 1]


def _bloques(ps: list[str], sentencia: bool) -> list[tuple[str, str, list[str]]]:
    """(id, cita base, párrafos) por artículo o fundamento de derecho."""
    patron = _FUNDAMENTO if sentencia else _ENCABEZADO
    bloques: list[tuple[str, str, list[str]]] = [("preambulo", "preámbulo", [])]
    for p in ps:
        m = patron.match(p)
        if m:
            cab = m.group("cab")
            if sentencia:
                clave = cab.upper()
                n = _ORDINALES_FJ.get(clave.replace("É", "E"), _ORDINALES_FJ.get(clave))
                ident, base = (f"fj{n}", f"FJ {n}.º") if n else ("fallo", "fallo")
            else:
                base = cita_de_bloque(re.sub(r"^Art\.", "Artículo", cab, flags=re.I))
                ident = re.sub(r"\W+", "-", base.lower()).strip("-")
            bloques.append((ident, base, [p]))
        else:
            bloques[-1][2].append(p)
    vistos: dict[str, int] = {}
    unicos = []
    for ident, base, cuerpo in bloques:
        if not cuerpo:
            continue
        vistos[ident] = vistos.get(ident, 0) + 1
        sufijo = f"-{vistos[ident]}" if vistos[ident] > 1 else ""
        unicos.append((ident + sufijo, base, cuerpo))
    return unicos


# Fallos sistemáticos de codificación de algunos PDF de boletines (fuentes mal incrustadas).
_REPARACIONES = str.maketrans({"œ": "ú", "Æ": "á", "Ø": "é", "ı": "í", "˝": "Í"})
_SEPARADOR_BOP = re.compile(r"N[º°]\s?\d{1,3}(?:\.\d{3})*\s*_{5,}|_{12,}")


def _patron_flexible(literal: str) -> re.Pattern[str]:
    """El literal del catálogo, tolerando saltos de línea y espacios del PDF entre palabras."""
    return re.compile(r"\s+".join(re.escape(w) for w in literal.split()), re.I)


def extraer(texto: str, inicio: str, fin: str | None = None) -> str:
    m = _patron_flexible(inicio).search(texto)
    if not m:
        raise DocumentoError(f"No se encontró el inicio del extracto: {inicio[:60]!r}")
    resto = texto[m.start() :]
    if fin:
        final = _patron_flexible(fin).search(resto, len(m.group(0)))
        if not final:
            raise DocumentoError(f"No se encontró el final del extracto: {fin[:60]!r}")
        return resto[: final.end()]
    separador = _SEPARADOR_BOP.search(resto, len(m.group(0)))
    return resto[: separador.start()] if separador else resto


MAX_CHARS_BLOQUE = 2500
CHARS_PARTE = 1500


def _partes(ident: str, base: str, cuerpo: list[str]) -> list[tuple[str, str, list[str]]]:
    """Un bloque sin artículos muy largo (un anuncio de BOP) se parte por párrafos enteros."""
    if len("\n".join(cuerpo)) <= MAX_CHARS_BLOQUE:
        return [(ident, base, cuerpo)]
    partes: list[list[str]] = [[]]
    for p in cuerpo:
        if partes[-1] and len("\n".join([*partes[-1], p])) > CHARS_PARTE:
            partes.append([])
        partes[-1].append(p)
    return [(f"{ident}-p{i}", f"{base}, parte {i}", ps) for i, ps in enumerate(partes, 1)]


def parsear(bruto: bytes, norma: Norma, fuente: Fuente) -> list[Fragmento]:
    if fuente.desde is None:  # pragma: no cover - lo exige el catálogo
        raise DocumentoError(f"{norma.id}: falta `desde`")
    texto = texto_plano(bruto)
    if bruto[:5] == b"%PDF-" and any(c in texto for c in "œÆØ˝"):
        texto = texto.translate(_REPARACIONES)
    if fuente.extracto:
        texto = extraer(texto, fuente.extracto, fuente.extracto_fin)
    ps = parrafos(texto)
    sentencia = fuente.troceo == "fundamentos" or norma.clase.value == "jurisprudencia"
    bloques = _preguntas(ps) if fuente.troceo == "preguntas" else _bloques(ps, sentencia)
    if fuente.troceo == "texto" or (len(bloques) <= 1 and fuente.cita):
        bloques = [("texto", fuente.cita or "texto", ps)]
    bloques = [parte for b in bloques for parte in _partes(*b)]
    fragmentos: list[Fragmento] = []
    for ident, base, cuerpo in bloques:
        if ident == "preambulo" and len(bloques) > 1 and not sentencia:
            continue  # exposición de motivos: no es parte dispositiva
        fragmentos.extend(
            fragmentos_de_version(
                norma=norma,
                bloque_id=ident,
                base_cita=base,
                # En un texto corrido, la primera frase de la parte no es un título.
                encabezado=norma.titulo if fuente.troceo == "texto" else cuerpo[0][:160],
                parrafos=cuerpo,
                ruta=[],
                desde=fuente.desde,
                hasta=fuente.hasta,
                url=norma.url,
                vigencia_incierta=fuente.incierta,
            )
        )
    if not fragmentos:
        raise DocumentoError(f"{norma.id}: el documento no tiene texto aprovechable")
    return fragmentos
