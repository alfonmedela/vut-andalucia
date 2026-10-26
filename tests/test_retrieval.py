from __future__ import annotations

import datetime as dt

from vut_andalucia.glossary import alias_indexacion, expandir_consulta
from vut_andalucia.retrieval import BM25, Buscador

HOY = dt.date(2026, 10, 26)


def test_bm25_prefiere_documento_con_termino_raro() -> None:
    bm25 = BM25([["casa", "turismo"], ["casa", "casa"], ["perro"]])
    p = bm25.puntuar(["turismo"])
    assert p[0] > 0 and p[1] == 0 and p[2] == 0


def test_glosario_traduce_lenguaje_coloquial() -> None:
    assert "vivienda de uso turístico" in expandir_consulta("¿Puedo poner mi piso en Airbnb?")
    assert "comunidad de propietarios" in expandir_consulta("¿y los vecinos?")
    assert expandir_consulta("capital de Francia") == "capital de Francia"


def test_alias_de_remision_a_la_lau() -> None:
    assert alias_indexacion(
        "la actividad a que se refiere la letra e) del artículo 5 de la Ley 29/1994"
    )
    assert alias_indexacion("obras en elementos comunes") == ""


def test_coloquial_encuentra_el_17_12(buscador: Buscador) -> None:
    citas = [
        r.fragmento.cita for r in buscador.buscar("¿Pueden los vecinos prohibir mi Airbnb?", HOY, 5)
    ]
    assert "art. 17.12 LPH" in citas


def test_busqueda_respeta_la_fecha(buscador: Buscador) -> None:
    def version_17_12(fecha: dt.date) -> dt.date | None:
        for r in buscador.buscar("mayoría junta limitar pisos turísticos", fecha, 10):
            if r.fragmento.base_id.endswith("adiecisiete.12"):
                return r.fragmento.vigente_desde
        return None

    assert version_17_12(HOY) == dt.date(2025, 4, 3)
    assert version_17_12(dt.date(2024, 6, 1)) == dt.date(2019, 3, 6)
    assert version_17_12(dt.date(2017, 1, 1)) is None  # el apartado 12 aún no existía
