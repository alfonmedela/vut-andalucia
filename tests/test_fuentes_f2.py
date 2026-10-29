"""Ingestores de F2: BOJA, EUR-Lex, documentos y avisos de vigencia del BOE."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from vut_andalucia import municipios
from vut_andalucia.ingest import boe, boja, documento, eurlex
from vut_andalucia.ingest.comun import cita, fragmentos_de_version
from vut_andalucia.models import Ambito, Clase, Norma
from vut_andalucia.sources import CatalogoInvalido, Fuente, _fuente

NORMA = Norma(
    id="X", titulo="Decreto de prueba", corta="D 1/2026", rango="Decreto", ambito="a", url="u",
    fuente="boja",
)  # fmt: skip

VERSION_BOJA = """<html><body>
<h3 class="ca">CAPÍTULO I<br><span class="titDiv">Disposiciones</span></h3>
<div id="ART6"><p class="a"><em>Artículo 6</em><i> Requisitos </i></p>
<p><b>1. </b> Las viviendas deberán cumplir:</p>
<ul><li><b>a) </b> Tener 14 m<sup>2</sup> por plaza.</li>
<li><b>b) </b> Disponer de dos baños.<p>Las cuevas se rigen por su normativa.</p></li></ul>
<div class="ccn accion"><em class="aj">Apartado d) anulado por Sentencia del TSJA.</em></div>
</div></body></html>"""


def _version(tmp: Path, fecha: str, cuerpo: str) -> None:
    cabecera = f"<p>Vigencia: {fecha}</p>"
    (tmp / f"{fecha.replace('/', '-')}.html").write_text(cabecera + cuerpo, encoding="utf-8")


def test_boja_lee_items_de_lista_y_avisos_de_anulacion(tmp_path: Path) -> None:
    _version(tmp_path, "11/5/2016", VERSION_BOJA)
    (f,) = boja.parsear(tmp_path, NORMA, "u")
    assert "a) Tener 14 m2 por plaza." in f.texto  # el texto de los <li> no se pierde
    assert "Las cuevas se rigen" in f.texto
    assert f.nota and "anulado" in f.nota  # la anulación viaja con la cita
    assert f.cita == "art. 6 D 1/2026" and f.ruta == ["CAPÍTULO I Disposiciones"]


def test_boja_fusiona_versiones_iguales_y_separa_las_distintas(tmp_path: Path) -> None:
    _version(tmp_path, "11/5/2016", VERSION_BOJA)
    _version(tmp_path, "22/2/2024", VERSION_BOJA)
    _version(tmp_path, "4/3/2025", VERSION_BOJA.replace("dos baños", "tres baños"))
    fs = boja.parsear(tmp_path, NORMA, "u")
    assert [(f.vigente_desde, f.vigente_hasta) for f in fs] == [
        (dt.date(2016, 5, 11), dt.date(2025, 3, 4)),
        (dt.date(2025, 3, 4), None),
    ]


def test_boja_sin_fecha_de_vigencia_falla() -> None:
    with pytest.raises(boja.BOJAError):
        boja.fecha_de_version("<p>sin fecha</p>")


def test_eurlex_une_marcas_de_lista_y_quita_notas_al_pie() -> None:
    xhtml = """<div class="eli-subdivision" id="art_3"><p class="oj-ti-art">Artículo 3</p>
    <p class="oj-sti-art">Definiciones</p><table><tr><td><p class="oj-normal">1)</p></td>
    <td><p class="oj-normal">«unidad»: un alojamiento <a href="#ntr21">(21)</a>amueblado;</p></td>
    </tr></table></div>""".encode()
    (f,) = eurlex.parsear(xhtml, NORMA, "32024R1028", dt.date(2024, 5, 19))
    assert f.texto == "1) «unidad»: un alojamiento amueblado;"
    assert f.encabezado == "Artículo 3 Definiciones" and f.cita == "art. 3 D 1/2026"


def test_ordinales_trocean_apartados_largos_y_citan_con_grado() -> None:
    relleno = "texto " * 300
    parrafos = [
        "Uno. Estarán exentas:",
        f"1.º Primera {relleno}",
        f"2.º Segunda {relleno}",
        f"4.º Los arrendamientos {relleno}",  # el 3.º fue suprimido: hueco tolerado
        "1.º lista anidada",
        "Dos. Otra cosa.",
    ]
    fs = list(
        fragmentos_de_version(
            norma=NORMA, bloque_id="a20", base_cita="art. 20", encabezado="", parrafos=parrafos,
            ruta=[], desde=dt.date(2020, 1, 1), hasta=None, url="u",
        )
    )  # fmt: skip
    citas = [f.cita for f in fs]
    assert "art. 20.Uno.4.º D 1/2026" in citas
    assert any("lista anidada" in f.texto for f in fs if f.cita.startswith("art. 20.Uno.4"))
    assert cita("disp. adic. 2.ª", "1", "LPH") == "disp. adic. 2.ª, apdo. 1 LPH"


def _version_boe(vigencia: str, norma: str, nota: str) -> ET.Element:
    return ET.fromstring(
        f'<version id_norma="{norma}" fecha_vigencia="{vigencia}"><p>texto</p>'
        f"<blockquote><p>{nota}</p></blockquote></version>"
    )


def test_efectos_diferidos_de_una_version_del_boe() -> None:
    v = _version_boe(
        "20261008",
        "BOE-A-2026-20823",
        "Se modifica, con efectos de 1 de diciembre de 2026, el apartado 1.23º.e') por el "
        "art. 7.1 del Real Decreto-ley 29/2026. Ref. BOE-A-2026-20823#a7",
    )
    efectos, aviso = boe.efectos_de_version(v)
    assert efectos == dt.date(2026, 12, 1) and aviso and "difiere" in aviso


def test_sin_efectos_diferidos_si_la_nota_es_de_otra_norma() -> None:
    v = _version_boe(
        "20261008",
        "BOE-A-2026-20823",
        "Se modifica, con efectos de 1 de enero de 2020, por la Ley 1/2019. Ref. BOE-A-2019-1",
    )
    assert boe.efectos_de_version(v) == (None, None)


def test_decreto_ley_sin_convalidar_lleva_aviso() -> None:
    meta = {"rango": {"texto": "Real Decreto-ley"}, "numero_oficial": "29/2026",
            "identificador": "BOE-A-2026-20823"}  # fmt: skip
    assert "convalidado" in (boe.pendiente_de_convalidacion(meta, []) or "")
    convalidado = [{"relacion": {"texto": "SE PUBLICA Acuerdo de convalidación"}}]
    assert boe.pendiente_de_convalidacion(meta, convalidado) is None
    assert boe.pendiente_de_convalidacion({"rango": {"texto": "Ley"}}, []) is None


def test_norma_derogada_sin_fecha_falla_en_vez_de_darse_por_vigente() -> None:
    with pytest.raises(boe.BOEError, match="sin fecha"):
        boe.estado_de_vigencia({"identificador": "X", "estatus_derogacion": "S"})
    aviso, fin = boe.estado_de_vigencia(
        {"identificador": "X", "estatus_derogacion": "S", "fecha_derogacion": "20250101"}
    )
    assert fin == dt.date(2025, 1, 1) and aviso and "Derogada" in aviso


def test_documento_extrae_un_anuncio_del_boletin() -> None:
    texto = (
        "AYUNTAMIENTO DE UBRIQUE\nANUNCIO otra cosa\nNº 1.234 ___________\n"
        "AYUNTAMIENTO DE CHICLANA\nANUNCIO SUSPENSIÓN DE\nLICENCIAS para VUT.\n"
        "Nº 62.274 ___________\nAYUNTAMIENTO DE OTRO"
    )
    extracto = documento.extraer(texto, "AYUNTAMIENTO DE CHICLANA ANUNCIO SUSPENSIÓN DE LICENCIAS")
    assert extracto.startswith("AYUNTAMIENTO DE CHICLANA") and "OTRO" not in extracto
    assert "Nº 62.274" not in extracto
    with pytest.raises(documento.DocumentoError):
        documento.extraer(texto, "AYUNTAMIENTO DE MARBELLA")


def test_documento_parte_textos_largos_por_parrafos_enteros() -> None:
    cuerpo = [f"Párrafo {i}. " + "palabra " * 60 for i in range(12)]
    partes = documento._partes("texto", "anuncio", cuerpo)
    assert len(partes) > 1 and partes[1][1] == "anuncio, parte 2"
    assert sum(len(p[2]) for p in partes) == len(cuerpo)


def test_documento_faq_por_preguntas() -> None:
    ps = ["Introducción", "¿Cuánto cuesta?", "Nada, es gratuito.", "¿Y la baja?", "Se comunica."]
    bloques = documento._preguntas(ps)
    assert [(b[0], b[2]) for b in bloques] == [
        ("p1", ["¿Cuánto cuesta?", "Nada, es gratuito."]),
        ("p2", ["¿Y la baja?", "Se comunica."]),
    ]


def test_catalogo_valida_fuentes_municipales_y_campos() -> None:
    with pytest.raises(CatalogoInvalido, match="municipio"):
        _fuente(
            {"id": "M", "corta": "m", "fuente": "boe", "motivo": "x", "territorio": "municipal"}
        )
    with pytest.raises(CatalogoInvalido, match="desconocidos"):
        _fuente({"id": "M", "corta": "m", "fuente": "boe", "motivo": "x", "inventado": 1})
    f = _fuente({"id": "M", "corta": "m", "fuente": "boe", "motivo": "x", "alias": ["IVA"]})
    assert isinstance(f, Fuente) and f.alias == ("iva",) and f.clase == Clase.NORMA
    assert f.territorio == Ambito.ESTATAL


@pytest.mark.parametrize(
    ("texto", "esperados"),
    [
        ("¿Puedo abrir un piso turístico en el centro de Málaga?", ["Málaga"]),
        ("Tengo VUT en El Puerto de Santa María y en Rota", ["El Puerto de Santa María", "Rota"]),
        ("la pizarra está rota", []),
        ("Vivo en Puerto Real, ¿y en Santa María?", ["Puerto Real"]),
    ],
)
def test_detecta_municipios(texto: str, esperados: list[str]) -> None:
    assert [m.nombre for m in municipios.detectar(texto)] == esperados


def test_sabe_cuando_la_pregunta_depende_del_municipio() -> None:
    assert municipios.depende_del_municipio("¿Puedo abrir un piso turístico?")
    assert not municipios.depende_del_municipio("¿Qué IVA tiene el alquiler turístico?")
    assert len(municipios.todos()) == 785


def _fuente_doc(**extra: object) -> Fuente:
    datos: dict[str, object] = {
        "id": "D", "corta": "Ordenanza", "fuente": "documento", "motivo": "x",
        "origen": "local:d.html", "desde": "2025-01-01", **extra,
    }  # fmt: skip
    return _fuente(datos)


def _norma_doc(clase: Clase = Clase.NORMA) -> Norma:
    return NORMA.model_copy(update={"fuente": "documento", "clase": clase})


def test_documento_html_por_articulos() -> None:
    html = (
        "<html><nav>menú</nav><h1>Ordenanza</h1><p>Exposición de motivos larga.</p>"
        "<p>Artículo 1. Objeto.</p><p>Regula las viviendas turísticas.</p>"
        "<p>Artículo 2. Límites.</p><p>No se admiten nuevas en el centro.</p>"
        "<p>Disposición final primera. Entrada en vigor.</p><p>Al día siguiente.</p></html>"
    ).encode()
    fs = documento.parsear(html, _norma_doc(), _fuente_doc())
    assert [f.cita for f in fs] == [
        "art. 1 D 1/2026",
        "art. 2 D 1/2026",
        "disp. final 1.ª D 1/2026",
    ]
    assert "menú" not in " ".join(f.texto for f in fs)  # navegación fuera
    assert all(f.vigente_desde == dt.date(2025, 1, 1) for f in fs)


def test_documento_sentencia_por_fundamentos_y_fallo() -> None:
    html = (
        "<p>ANTECEDENTES DE HECHO.</p><p>PRIMERO.- La comunidad acordó prohibir.</p>"
        "<p>SEGUNDO.- El art. 17.12 LPH permite limitar.</p>"
        "<p>FALLO</p><p>Desestimamos el recurso.</p>"
    ).encode()
    fs = documento.parsear(html, _norma_doc(Clase.JURISPRUDENCIA), _fuente_doc())
    assert [f.bloque_id for f in fs] == ["preambulo", "fj1", "fj2", "fallo"]
    assert fs[-1].cita == "fallo D 1/2026"


def test_documento_como_texto_corrido_con_incierta() -> None:
    html = b"<p>Anuncio de suspension.</p><p>Por tres anos.</p>"
    fuente = _fuente_doc(troceo="texto", cita="anuncio", incierta=True)
    (f,) = documento.parsear(html, _norma_doc(), fuente)
    assert f.cita == "anuncio D 1/2026" and f.vigencia_incierta


def test_documento_guarda_copia_y_la_reutiliza(tmp_path: Path) -> None:
    import httpx

    llamadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(str(request.url))
        return httpx.Response(200, content=b"<p>hola</p>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        url = "https://ayto.example/anuncio.pdf"
        assert documento.obtener(url, c, tmp_path) == b"<p>hola</p>"
        assert documento.obtener(url, c, tmp_path) == b"<p>hola</p>"
    assert len(llamadas) == 1
    with pytest.raises(documento.DocumentoError):
        documento.obtener("local:no-existe.pdf", c, tmp_path)


def test_documento_tacha_dni_y_nombre_de_particulares() -> None:
    texto = (
        "SUGERENCIA 1: Presentada por Don Fulano Ejemplo Ficticio, con DNI n.º 00000000T, y por "
        "Doña Mengana de la Prueba, con NIF 12.345.678-Z. Alegaciones formuladas por "
        "D.Zutano Inventado Pérez y D.Perengano Falso, y Empresa Ejemplo, S.L. "
        "Ponente: D. José Luis Gil."
    )
    limpio = documento.anonimizar(texto)
    for dato in ("Fulano", "Mengana", "Zutano", "Perengano", "00000000T", "12.345.678"):
        assert dato not in limpio
    assert limpio.count("[nombre omitido]") == 4 and limpio.count("[DNI omitido]") == 2
    assert "Empresa Ejemplo, S.L." in limpio
    # Un cargo público sin DNI y los identificadores de normas no se tocan.
    assert "D. José Luis Gil" in limpio
    assert documento.anonimizar("BOE-A-1992-28740, art. 20.Uno.23.º") == (
        "BOE-A-1992-28740, art. 20.Uno.23.º"
    )
