from __future__ import annotations

import pytest

from vut_andalucia.text import cita_de_bloque, normaliza_cita, numero_en_palabras, tokens


@pytest.mark.parametrize(
    ("palabras", "esperado"),
    [
        ("diecisiete", 17),
        ("48", 48),
        ("primero", 1),
        ("veintitrés", 23),
        ("treinta y dos", 32),
        ("cuarenta", 40),
        ("treinta y patata", None),
    ],
)
def test_numero_en_palabras(palabras: str, esperado: int | None) -> None:
    assert numero_en_palabras(palabras) == esperado


@pytest.mark.parametrize(
    ("titulo", "cita"),
    [
        ("Artículo diecisiete", "art. 17"),
        ("Artículo 48", "art. 48"),
        ("Artículo 4 bis", "art. 4 bis"),
        ("Disposición adicional segunda", "disp. adic. 2.ª"),
        ("Disposición transitoria única", "disp. trans. única"),
        ("Disposición final", "disp. final única"),
    ],
)
def test_cita_de_bloque(titulo: str, cita: str) -> None:
    assert cita_de_bloque(titulo) == cita


def test_tokens_ignora_tildes_mayusculas_y_vacias() -> None:
    assert tokens("Las VIVIENDAS turísticas") == tokens("vivienda turistica")


def test_normaliza_cita() -> None:
    assert normaliza_cita("«Estos  acuerdos\nno tendrán»") == normaliza_cita(
        "estos acuerdos no tendrán."
    )
