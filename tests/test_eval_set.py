"""El set de evaluación se comprueba contra el texto legal: cada cita debe ser literal."""

from __future__ import annotations

from pathlib import Path

import pytest

from vut_andalucia.corpus import Corpus
from vut_andalucia.evaluation import Caso, cargar_preguntas, evaluar_recuperacion
from vut_andalucia.retrieval import Buscador
from vut_andalucia.text import normaliza_cita

RUTA = Path(__file__).parents[1] / "eval" / "preguntas.yaml"
CASOS = cargar_preguntas(RUTA)


@pytest.mark.parametrize("caso", [c for c in CASOS if c.ambito], ids=lambda c: c.id)
def test_cita_de_referencia_es_literal_en_la_version_vigente(
    caso: Caso, corpus_real: Corpus
) -> None:
    assert caso.cita is not None
    vigentes = [f for f in corpus_real.vigentes(caso.fecha) if f.base_id in caso.fuentes]
    assert vigentes, f"{caso.id}: ninguna fuente vigente el {caso.fecha}"
    if caso.version:
        assert any(f.vigente_desde == caso.version for f in vigentes), (
            f"{caso.id}: versión inesperada"
        )
    assert any(normaliza_cita(caso.cita) in normaliza_cita(f.texto) for f in vigentes), (
        f"{caso.id}: la cita no aparece en {[f.id for f in vigentes]}"
    )


def test_set_minimo_con_casos_fuera_de_ambito() -> None:
    assert sum(c.ambito for c in CASOS) >= 10
    assert sum(not c.ambito for c in CASOS) >= 3


def test_recuperacion_umbral(buscador: Buscador) -> None:
    """Puerta de calidad de la fase: acierto@5 ≥ 0,9 sobre el set."""
    informe = evaluar_recuperacion(CASOS, buscador, k=5)
    assert informe["acierto@5"] >= 0.9, informe
