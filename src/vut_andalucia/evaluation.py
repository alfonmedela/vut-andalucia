"""Evaluación reproducible: recuperación (sin LLM) y generación con citas verificadas."""

from __future__ import annotations

import datetime as dt
import re
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from vut_andalucia.answer import cita_exacta, responder
from vut_andalucia.llm import LLM
from vut_andalucia.retrieval import Buscador
from vut_andalucia.text import normaliza_cita


class Caso(BaseModel):
    id: str
    pregunta: str
    fecha: dt.date
    ambito: bool = True
    fuentes: list[str] = Field(default_factory=list)
    version: dt.date | None = None
    cita: str | None = None
    hechos: list[str] = Field(default_factory=list)
    no_debe: list[str] = Field(
        default_factory=list, description="Afirmaciones erróneas que no deben aparecer"
    )


def cargar_preguntas(ruta: Path) -> list[Caso]:
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    casos = [Caso.model_validate(d) for d in datos]
    ids = [c.id for c in casos]
    if len(ids) != len(set(ids)):
        raise ValueError(f"IDs de caso repetidos en {ruta}")
    for c in casos:
        if c.ambito and not (c.fuentes and c.cita and c.hechos):
            raise ValueError(f"{c.id}: un caso en ámbito necesita fuentes, cita y hechos")
    return casos


def evaluar_recuperacion(casos: list[Caso], buscador: Buscador, k: int = 5) -> dict[str, Any]:
    en_ambito = [c for c in casos if c.ambito]
    aciertos, rr, fallos = 0, 0.0, []
    for c in en_ambito:
        ids = [r.fragmento.base_id for r in buscador.buscar(c.pregunta, c.fecha, k)]
        posiciones = [ids.index(f) + 1 for f in c.fuentes if f in ids]
        if posiciones:
            aciertos += 1
            rr += 1 / min(posiciones)
        else:
            fallos.append(c.id)
    n = max(len(en_ambito), 1)
    return {
        "casos": len(en_ambito),
        f"acierto@{k}": round(aciertos / n, 3),
        "mrr": round(rr / n, 3),
        "fallos": fallos,
    }


def _aparece(frase: str, texto: str) -> bool:
    """Coincidencia por palabras completas («20» no casa con «2020»); prefijo con «*» final."""
    f = normaliza_cita(frase)
    patron = re.escape(f[:-1]) + r"\w*" if f.endswith("*") else re.escape(f)
    return re.search(rf"(?<!\w){patron}(?!\w)", normaliza_cita(texto)) is not None


def _hechos_cubiertos(caso: Caso, afirmado: str) -> bool:
    """Se mira solo lo que afirma el modelo, no la cita (que siempre es texto legal).

    Cada hecho debe aparecer ('a|b' admite alternativas) y ningún `no_debe` puede aparecer.
    """
    presentes = all(any(_aparece(alt, afirmado) for alt in h.split("|")) for h in caso.hechos)
    return presentes and not any(_aparece(x, afirmado) for x in caso.no_debe)


def evaluar_generacion(casos: list[Caso], buscador: Buscador, llm: LLM) -> dict[str, Any]:
    resultados: list[dict[str, Any]] = []
    for c in casos:
        inicio = time.perf_counter()
        r = responder(c.pregunta, buscador, llm, c.fecha)
        segundos = time.perf_counter() - inicio
        texto = " ".join(a.texto for a in r.afirmaciones)
        citadas = {a.fragmento.base_id for a in r.afirmaciones}
        # Control independiente del verificador: igualdad estricta salvo espacios.
        no_exactas = sum(not cita_exacta(a.cita_literal, a.fragmento.texto) for a in r.afirmaciones)
        versiones_ok = all(a.fragmento.vigente_en(c.fecha) for a in r.afirmaciones)
        if c.ambito:
            ok = (
                not r.abstiene
                and bool(citadas & set(c.fuentes))
                and _hechos_cubiertos(c, texto)
                and versiones_ok
            )
        else:
            ok = r.abstiene
        resultados.append(
            {
                "id": c.id,
                "ok": ok,
                "abstiene": r.abstiene,
                "motivo_abstencion": r.motivo_abstencion,
                "fuente_correcta": bool(citadas & set(c.fuentes)),
                "hechos": _hechos_cubiertos(c, texto) if c.ambito else None,
                "afirmaciones": len(r.afirmaciones),
                "descartadas": r.descartadas,
                "citas_no_exactas": no_exactas,
                "segundos": round(segundos, 1),
            }
        )
    propuestas = sum(x["afirmaciones"] + x["descartadas"] for x in resultados)
    verificadas = sum(x["afirmaciones"] for x in resultados)
    en = [x for x in resultados if x["id"] in {c.id for c in casos if c.ambito}]
    fuera = [x for x in resultados if x not in en]
    return {
        "modelo": llm.nombre,
        "exactitud_en_ambito": round(sum(x["ok"] for x in en) / max(len(en), 1), 3),
        "abstencion_fuera_de_ambito": round(
            sum(x["abstiene"] for x in fuera) / max(len(fuera), 1), 3
        ),
        "citas_verificadas": round(verificadas / max(propuestas, 1), 3),
        "citas_mostradas": verificadas,
        "citas_mostradas_no_exactas": sum(x["citas_no_exactas"] for x in resultados),
        "casos": resultados,
    }
