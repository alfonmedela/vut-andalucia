from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest

from tests.conftest import FIXTURES
from vut_andalucia import corpus as corpus_mod
from vut_andalucia.ingest import boe, sincronizar
from vut_andalucia.models import Fragmento, Norma
from vut_andalucia.sources import por_id


def _por_base(fragmentos: list[Fragmento], base: str) -> list[Fragmento]:
    return sorted((f for f in fragmentos if f.base_id == base), key=lambda f: f.vigente_desde)


def test_norma_desde_metadatos(norma_lph: Norma) -> None:
    assert norma_lph.id == "BOE-A-1960-10906"
    assert norma_lph.rango == "Ley"
    assert norma_lph.eli and norma_lph.eli.startswith("https://www.boe.es/eli/")


def test_trocea_articulo_largo_por_apartados(fragmentos_lph: list[Fragmento]) -> None:
    hoy = dt.date(2026, 10, 26)
    vigentes = [f for f in fragmentos_lph if f.bloque_id == "adiecisiete" and f.vigente_en(hoy)]
    apartados = {f.apartado for f in vigentes}
    assert {"1", "12"} <= apartados
    doce = next(f for f in vigentes if f.apartado == "12")
    assert doce.cita == "art. 17.12 LPH"
    assert doce.texto.startswith("12. El acuerdo expreso")


def test_historial_de_versiones_del_17_12(fragmentos_lph: list[Fragmento]) -> None:
    """RDL 21/2018 decayó (24/01/2019), RDL 7/2019 lo recuperó y la LO 1/2025 lo reformó."""
    versiones = _por_base(fragmentos_lph, "BOE-A-1960-10906#adiecisiete.12")
    tramos = [(f.vigente_desde, f.vigente_hasta) for f in versiones]
    assert tramos == [
        (dt.date(2018, 12, 19), dt.date(2019, 1, 24)),
        (dt.date(2019, 3, 6), dt.date(2025, 4, 3)),
        (dt.date(2025, 4, 3), None),
    ]
    assert "limite o condicione" in versiones[1].texto
    assert "prohíba" in versiones[2].texto


def test_sin_apartado_entre_enero_y_marzo_de_2019(fragmentos_lph: list[Fragmento]) -> None:
    hueco = dt.date(2019, 2, 15)
    assert not any(
        f.base_id.endswith("adiecisiete.12") and f.vigente_en(hueco) for f in fragmentos_lph
    )


def test_versiones_con_misma_fecha_prevalece_la_ultima(fragmentos_lph: list[Fragmento]) -> None:
    trece = [f for f in fragmentos_lph if f.bloque_id == "atrece"]
    assert len({f.id for f in trece}) == len(trece)


def test_vigencia_no_publicada_se_marca(fragmentos_lph: list[Fragmento]) -> None:
    da1 = [f for f in fragmentos_lph if f.bloque_id == "da-2"]
    assert da1 and all(f.vigencia_incierta for f in da1)
    assert all(f.cita.startswith("disp. adic. 1.ª") for f in da1)


def test_disposiciones_sin_ruta_de_capitulo(fragmentos_lph: list[Fragmento]) -> None:
    da2 = next(f for f in fragmentos_lph if f.bloque_id == "da-3")
    assert da2.ruta == []
    art7 = next(f for f in fragmentos_lph if f.bloque_id == "aseptimo")
    assert art7.ruta and art7.ruta[0].startswith("CAPÍTULO II")


def test_excluye_notas_y_preambulo(fragmentos_lph: list[Fragmento]) -> None:
    assert not any(f.bloque_id == "preambulo" for f in fragmentos_lph)
    assert not any("Se modifica por" in f.texto for f in fragmentos_lph)


def test_xml_sin_texto_falla(norma_lph: Norma) -> None:
    with pytest.raises(boe.BOEError):
        boe.parsear(b"<response><data/></response>", norma_lph)


def _transport(status: int = 200) -> httpx.MockTransport:
    meta = (FIXTURES / "boe_lph_metadatos.json").read_bytes()
    xml = (FIXTURES / "boe_lph_extracto.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        if request.url.path.endswith("/metadatos"):
            return httpx.Response(200, content=meta, headers={"Content-Type": "application/json"})
        return httpx.Response(200, content=xml)

    return httpx.MockTransport(handler)


def test_sincronizar_fuente_escribe_corpus_y_manifiesto(tmp_path: Path) -> None:
    with httpx.Client(transport=_transport()) as client:
        (entrada,) = sincronizar(tmp_path, (por_id("BOE-A-1960-10906"),), client)
    assert entrada.fragmentos > 0 and len(entrada.sha256) == 64
    cargado = corpus_mod.cargar(tmp_path)
    assert len(cargado.fragmentos) == entrada.fragmentos
    assert cargado.normas["BOE-A-1960-10906"].corta == "LPH"


def test_sincronizar_fuente_propaga_error_http(tmp_path: Path) -> None:
    with (
        httpx.Client(transport=_transport(503)) as client,
        pytest.raises(boe.BOEError, match="503"),
    ):
        sincronizar(tmp_path, (por_id("BOE-A-1960-10906"),), client)
    assert not list(tmp_path.iterdir())  # nada escrito si la descarga falla


def test_corpus_vacio_da_error_claro(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="vut sync"):
        corpus_mod.cargar(tmp_path)


def test_corpus_con_recuento_distinto_al_manifiesto_falla(tmp_path: Path) -> None:
    with httpx.Client(transport=_transport()) as client:
        sincronizar(tmp_path, (por_id("BOE-A-1960-10906"),), client)
    jsonl = tmp_path / "BOE-A-1960-10906.jsonl"
    jsonl.write_text("".join(jsonl.read_text(encoding="utf-8").splitlines(True)[:-1]))
    with pytest.raises(corpus_mod.CorpusCorrupto, match="manifiesto"):
        corpus_mod.cargar(tmp_path)


def test_apartados_con_lista_anidada() -> None:
    # Estructura real del art. 42 de la Ley 13/2011: una lista 1., 2. dentro de la letra a).
    parrafos = [
        "1. Uno.",
        "2. Dos:",
        "a) letra:",
        "1. sub uno",
        "2. sub dos",
        "b) otra",
        "3. Tres.",
        "4. Cuatro.",
    ]
    assert [a for a, _ in boe._apartados(parrafos)] == ["1", "2", "3", "4"]
    # Caso ambiguo sin letra que cierre la sublista: el «2.» siguiente a la sublista es apartado.
    parrafos = ["1. Intro:", "a) algo:", "1. sub uno", "2. sub dos", "2. Segundo apartado"]
    grupos = boe._apartados(parrafos)
    assert [a for a, _ in grupos] == ["1", "2"]
    assert grupos[1][1] == ["2. Segundo apartado"]


def _frag(texto: str, desde: dt.date, hasta: dt.date | None) -> Fragmento:
    return Fragmento(
        id=f"N#a1@{desde}",
        norma_id="N",
        bloque_id="a1",
        cita="art. 1 N",
        encabezado="",
        texto=texto,
        vigente_desde=desde,
        vigente_hasta=hasta,
        url="u",
    )


def test_fusionar_une_solo_versiones_contiguas_e_iguales() -> None:
    d = [dt.date(2020, 1, 1), dt.date(2021, 1, 1), dt.date(2022, 1, 1), dt.date(2023, 1, 1)]
    fusion = boe._fusionar(
        iter(
            [
                _frag("A", d[0], d[1]),
                _frag("A", d[1], d[2]),  # iguales y contiguas: se unen
                _frag("B", d[2], d[3]),  # cambia el texto
                _frag("B", dt.date(2023, 6, 1), None),  # hueco: no se unen
            ]
        )
    )
    assert [(f.texto, f.vigente_desde, f.vigente_hasta) for f in fusion] == [
        ("A", d[0], d[2]),
        ("B", d[2], d[3]),
        ("B", dt.date(2023, 6, 1), None),
    ]


def test_vigente_en_es_semiabierto() -> None:
    f = _frag("A", dt.date(2020, 1, 1), dt.date(2021, 1, 1))
    assert f.vigente_en(dt.date(2020, 1, 1)) and not f.vigente_en(dt.date(2021, 1, 1))


_XML_VERSIONES = b"""<response><data><texto>
<bloque id="a1" tipo="precepto" titulo="Art\xc3\xadculo 1">
 <version id_norma="X" fecha_publicacion="20200101" fecha_vigencia="20200201">
  <p class="parrafo">Texto publicado primero.</p></version>
 <version id_norma="Y" fecha_publicacion="20200115" fecha_vigencia="20200201">
  <p class="parrafo">Texto publicado despu\xc3\xa9s.</p></version>
 <version id_norma="Z" fecha_publicacion="20210101" fecha_vigencia="20210101">
  <p class="parrafo">(Derogado)</p></version>
</bloque></texto></data></response>"""


def test_misma_vigencia_gana_la_ultima_publicada_y_derogado_cierra(norma_lph: Norma) -> None:
    (f,) = boe.parsear(_XML_VERSIONES, norma_lph)
    assert f.texto == "Texto publicado después." and f.modificado_por == "Y"
    assert f.vigente_hasta == dt.date(2021, 1, 1)


def test_version_sin_fechas_da_error_con_contexto(norma_lph: Norma) -> None:
    xml = (
        b'<response><data><texto><bloque id="a9" tipo="precepto" titulo="Art 9">'
        b'<version id_norma="X"><p>t</p></version></bloque></texto></data></response>'
    )
    with pytest.raises(boe.BOEError, match="a9"):
        boe.parsear(xml, norma_lph)
