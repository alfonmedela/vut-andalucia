"""Piezas comunes a todos los ingestores: apartados, citas y fusión de versiones."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterable, Iterator

from vut_andalucia.models import Fragmento, Norma

# Un artículo largo se trocea por apartados numerados para que BM25 no diluya el apartado relevante.
MAX_CHARS_SIN_TROCEAR = 1200
_APARTADO = re.compile(r"^(\d{1,2})\.\s")
_LETRA = re.compile(r"^[a-zñ]\)\s")
_PALABRAS = "uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince"
_APARTADO_PALABRA = re.compile(rf"^({'|'.join(p.capitalize() for p in _PALABRAS.split())})\.\s")
_ORDINAL = re.compile(r"^(\d{1,2})\.º\s")
# Un apartado aún muy largo (p. ej. el art. 20.Uno de la Ley del IVA, con 29 exenciones) se
# vuelve a trocear por sus números ordinales («23.º»).
MAX_CHARS_APARTADO = 3000
SALTO_MAX_ORDINAL = 3


def apartados(parrafos: list[str]) -> list[tuple[str | None, list[str]]]:
    """Agrupa párrafos por apartado numerado ('1.', '2.'...). Lo previo al primero va con None.

    El BOE no marca de forma fiable el nivel de cada párrafo, así que se infiere: un '1.' que
    aparece con un apartado ya abierto empieza una lista anidada, que continúa mientras los
    números sigan su secuencia y termina en la siguiente letra ('b)') o párrafo sin número.
    """
    grupos: list[tuple[str | None, list[str]]] = [(None, [])]
    actual = 0
    anidado = 0  # último número de la lista anidada en curso; 0 si no hay
    for p in parrafos:
        m = _APARTADO.match(p)
        n = int(m.group(1)) if m else None
        if n is None:
            anidado = 0
        elif anidado and n == anidado + 1:
            anidado = n
        elif n == actual + 1:
            actual, anidado = n, 0
            grupos.append((str(n), [p]))
            continue
        elif n == 1 and actual >= 1:
            anidado = 1
        if _LETRA.match(p):
            anidado = 0
        grupos[-1][1].append(p)
    return [g for g in grupos if g[1]]


def _cita(base: str, apartado: str | None, corta: str) -> str:
    if apartado is None:
        return f"{base} {corta}"
    sep = "." if base.startswith("art.") else ", apdo. "
    return f"{base}{sep}{apartado} {corta}"


def fusionar(fragmentos: Iterable[Fragmento]) -> list[Fragmento]:
    """Une versiones consecutivas de un apartado cuyo texto no cambió.

    Una reforma reescribe el artículo entero en el BOE aunque solo toque un apartado; así cada
    apartado conserva la fecha en que su texto entró realmente en vigor.
    """
    ultimos: dict[str, int] = {}  # base_id -> posición en `salida`
    salida: list[Fragmento] = []
    for f in fragmentos:
        i = ultimos.get(f.base_id)
        previo = salida[i] if i is not None else None
        if (
            i is not None
            and previo is not None
            and previo.texto == f.texto
            and previo.nota == f.nota
            and previo.vigente_hasta == f.vigente_desde
        ):
            salida[i] = previo.model_copy(
                update={
                    "vigente_hasta": f.vigente_hasta,
                    "vigencia_incierta": previo.vigencia_incierta or f.vigencia_incierta,
                }
            )
            continue
        ultimos[f.base_id] = len(salida)
        salida.append(f)
    return salida


def _por_patron(
    parrafos: list[str], patron: re.Pattern[str], etiqueta: Callable[[re.Match[str]], str]
) -> list[tuple[str | None, list[str]]]:
    grupos: list[tuple[str | None, list[str]]] = [(None, [])]
    for p in parrafos:
        m = patron.match(p)
        if m:
            grupos.append((etiqueta(m), [p]))
        else:
            grupos[-1][1].append(p)
    return [g for g in grupos if g[1]]


def _ordinales(pieza: tuple[str | None, list[str]]) -> list[tuple[str | None, list[str]]]:
    apartado, parrafos = pieza
    if len("\n".join(parrafos)) <= MAX_CHARS_APARTADO:
        return [pieza]
    grupos: list[tuple[str | None, list[str]]] = [(None, [])]
    ultimo = 0
    for p in parrafos:
        m = _ORDINAL.match(p)
        # Abre grupo el ordinal que avanza la secuencia (se toleran huecos de ordinales
        # suprimidos); un número que vuelve atrás es una lista anidada en el grupo en curso.
        if m and ultimo < int(m.group(1)) <= ultimo + SALTO_MAX_ORDINAL:
            ultimo = int(m.group(1))
            grupos.append((m.group(1), [p]))
        else:
            grupos[-1][1].append(p)
    grupos = [g for g in grupos if g[1]]
    if sum(1 for a, _ in grupos if a) < 2:
        return [pieza]
    base = apartado or "0"
    return [(f"{base}.{a}" if a else base, ps) for a, ps in grupos]


def _numerados(pieza: tuple[str | None, list[str]]) -> list[tuple[str | None, list[str]]]:
    """Trocea un apartado «Uno.» largo por sus números («1.», «2.»): «uno.2»."""
    apartado, parrafos = pieza
    if apartado is None or len("\n".join(parrafos)) <= MAX_CHARS_APARTADO:
        return [pieza]
    # El primer párrafo («Uno. Se aplicará el tipo…») es la cabecera del apartado.
    # Basta un número: en algunas versiones del art. 91.Dos solo hay un «1.» con sus
    # ordinales, que deben quedar como Dos.1.1.º y no como Dos.1.º.
    grupos = apartados(parrafos[1:])
    if not any(a for a, _ in grupos):
        return [pieza]
    cabecera = [parrafos[0]]
    salida: list[tuple[str | None, list[str]]] = []
    for a, ps in grupos:
        if a is None:
            cabecera += ps
        else:
            salida.append((f"{apartado}.{a}", ps))
    return [(apartado, cabecera), *salida]


def cita(base: str, apartado: str | None, corta: str, ordinal: bool | None = None) -> str:
    """«art. 17.12 LPH», «art. 20.Uno.23.º Ley 37/1992», «disp. adic. 2.ª, apdo. 1 …».

    `ordinal`: el último nivel es un ordinal («23.º»). Si no se indica, se supone que lo es
    cuando el apartado está anidado."""
    if apartado is None:
        return f"{base} {corta}"
    partes = [p.capitalize() if p.isalpha() else p for p in apartado.split(".")]
    if ordinal is None:
        ordinal = len(partes) > 1
    if ordinal and partes[-1].isdigit():
        partes[-1] += ".º"
    sep = "." if base.startswith("art.") else ", apdo. "
    return f"{base}{sep}{'.'.join(partes)} {corta}"


def fragmentos_de_version(
    *,
    norma: Norma,
    bloque_id: str,
    base_cita: str,
    encabezado: str,
    parrafos: list[str],
    ruta: list[str],
    desde: dt.date,
    hasta: dt.date | None,
    url: str,
    vigencia_incierta: bool = False,
    modificado_por: str | None = None,
    nota: str | None = None,
) -> Iterator[Fragmento]:
    """Convierte el texto de una versión de un precepto en uno o varios fragmentos."""
    cuerpo = "\n".join(parrafos)
    largo = len(cuerpo) > MAX_CHARS_SIN_TROCEAR
    # Leyes tributarias: «Uno.», «Dos.»… por encima de «1.», «2.» (art. 91 de la Ley del IVA:
    # Uno.2.2.º). Si los hay, mandan; dentro de cada uno se trocea por números.
    grupos = _por_patron(parrafos, _APARTADO_PALABRA, lambda m: m.group(1).lower())
    trocear = largo and sum(1 for a, _ in grupos if a) >= 2
    if trocear:
        grupos = [sub for g in grupos for sub in _numerados(g)]
    else:
        grupos = apartados(parrafos)
        trocear = largo and sum(1 for a, _ in grupos if a) >= 2
    piezas = grupos if trocear else [(None, parrafos)]
    ordinales: set[str] = set()
    piezas_finales: list[tuple[str | None, list[str]]] = []
    for pieza in piezas:
        subpiezas = _ordinales(pieza)
        if len(subpiezas) > 1:
            ordinales.update(a for a, _ in subpiezas if a and a != (pieza[0] or "0"))
        piezas_finales += subpiezas
    for apartado, ps in piezas_finales:
        sufijo = f".{apartado}" if apartado else ""
        if trocear and apartado is None:
            sufijo = ".0"
        yield Fragmento(
            id=f"{norma.id}#{bloque_id}{sufijo}@{desde.isoformat()}",
            norma_id=norma.id,
            bloque_id=bloque_id,
            apartado=apartado,
            cita=cita(base_cita, apartado, norma.corta, ordinal=apartado in ordinales),
            encabezado=encabezado,
            ruta=ruta,
            texto="\n".join(ps),
            vigente_desde=desde,
            vigente_hasta=hasta,
            vigencia_incierta=vigencia_incierta,
            modificado_por=modificado_por,
            nota=nota,
            url=url,
        )
