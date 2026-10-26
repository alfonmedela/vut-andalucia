from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.test_eval_set import CASOS, RUTA
from vut_andalucia import cli
from vut_andalucia.evaluation import cargar_preguntas, evaluar_generacion
from vut_andalucia.llm import Falso, LLMError
from vut_andalucia.retrieval import Buscador
from vut_andalucia.text import normaliza_cita

runner = CliRunner()


def test_buscar_sin_llm() -> None:
    r = runner.invoke(cli.app, ["buscar", "mayoría junta pisos turísticos", "-f", "2026-10-26"])
    assert r.exit_code == 0 and "art. 17.12 LPH" in r.output


def test_fecha_invalida() -> None:
    r = runner.invoke(cli.app, ["buscar", "x", "--fecha", "26/10/2026"])
    assert r.exit_code != 0 and "AAAA-MM-DD" in r.output


def test_eval_solo_recuperacion(tmp_path: Path) -> None:
    salida = tmp_path / "informe.json"
    r = runner.invoke(cli.app, ["eval", "--preguntas", str(RUTA), "--salida", str(salida)])
    assert r.exit_code == 0
    assert json.loads(salida.read_text())["recuperacion"]["acierto@5"] >= 0.9


def test_preguntar_sin_ollama_sale_con_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class SinServidor:
        nombre = "x"

        def json(self, *_: Any) -> dict[str, Any]:
            raise LLMError("No se pudo conectar con Ollama")

    monkeypatch.setattr(cli, "crear", lambda *_: SinServidor())
    r = runner.invoke(cli.app, ["preguntar", "¿Necesito permiso de los vecinos para mi Airbnb?"])
    assert r.exit_code == 2 and "Ollama" in r.output


def test_evaluar_generacion_con_llm_que_cita_la_referencia(buscador: Buscador) -> None:
    """Un LLM «oráculo» que copia la cita de referencia debe sacar 100 %: valida la métrica."""
    por_pregunta = {c.pregunta: c for c in CASOS}

    def oraculo(_s: str, usuario: str) -> dict[str, Any]:
        caso = por_pregunta[usuario.rsplit("<pregunta>", 1)[1].removesuffix("</pregunta>")]
        if not caso.ambito:
            return {"afirmaciones": [], "fuera_de_ambito": True}
        assert caso.cita is not None
        bloques = re.findall(r'<fragmento etiqueta="(F\d+)"[^>]*>(.*?)</fragmento>', usuario, re.S)
        etiqueta = next(e for e, t in bloques if normaliza_cita(caso.cita) in normaliza_cita(t))
        hechos = " ".join(h.split("|")[0].rstrip("*") for h in caso.hechos)
        return {
            "fuera_de_ambito": False,
            "afirmaciones": [{"texto": hechos, "fuente": etiqueta, "cita_literal": caso.cita}],
        }

    informe = evaluar_generacion(CASOS, buscador, Falso(oraculo))
    assert informe["exactitud_en_ambito"] == 1.0, [c for c in informe["casos"] if not c["ok"]]
    assert informe["abstencion_fuera_de_ambito"] == 1.0
    assert informe["citas_mostradas_no_exactas"] == 0


def test_caso_en_ambito_sin_cita_es_invalido(tmp_path: Path) -> None:
    p = tmp_path / "p.yaml"
    p.write_text(
        "- {id: x, pregunta: '¿?', fecha: 2026-01-01, fuentes: [a], cita: c}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="necesita fuentes, cita y hechos"):
        cargar_preguntas(p)


def test_hechos_se_miran_en_lo_afirmado_y_por_palabras() -> None:
    from vut_andalucia.evaluation import Caso, _hechos_cubiertos

    caso = Caso(id="x", pregunta="?", fecha="2026-01-01", hechos=["20"], no_debe=["sí son"])
    assert _hechos_cubiertos(caso, "un incremento máximo del 20 %")
    assert not _hechos_cubiertos(caso, "desde 2020")
    assert not _hechos_cubiertos(caso, "sí son 20")
