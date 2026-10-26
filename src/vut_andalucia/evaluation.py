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
from vut_andalucia.llm import LLM, LLMError
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
    municipio: str | None = None
    tipo: str = "normal"  # normal | premisa_falsa | caso_limite | temporal | municipal
    origen: str | None = Field(default=None, description="chat:<id> | online:<id> | propia")
    nota_juridica: str | None = None


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


def evaluar_recuperacion(
    casos: list[Caso], buscador: Buscador, k: int = 5, k_contexto: int = 10
) -> dict[str, Any]:
    """acierto@k: alguna fuente de referencia está entre las k primeras. cita@k_contexto: el
    fragmento que contiene la cita de referencia llega al contexto que ve el modelo, que es
    la métrica estricta (una norma correcta con el apartado equivocado no basta)."""
    en_ambito = [c for c in casos if c.ambito]
    aciertos, rr, citas, fallos = 0, 0.0, 0, []
    for c in en_ambito:
        resultados = buscador.buscar(c.pregunta, c.fecha, max(k, k_contexto), municipio=c.municipio)
        ids = [r.fragmento.base_id for r in resultados[:k]]
        posiciones = [ids.index(f) + 1 for f in c.fuentes if f in ids]
        if posiciones:
            aciertos += 1
            rr += 1 / min(posiciones)
        else:
            fallos.append(c.id)
        cita = normaliza_cita(c.cita or "")
        citas += any(cita in normaliza_cita(r.fragmento.texto) for r in resultados[:k_contexto])
    n = max(len(en_ambito), 1)
    return {
        "casos": len(en_ambito),
        f"acierto@{k}": round(aciertos / n, 3),
        "mrr": round(rr / n, 3),
        f"cita@{k_contexto}": round(citas / n, 3),
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
    return presentes and not any(_afirma(x, afirmado) for x in caso.no_debe)


_NEGACION = re.compile(r"(?<!\w)(no|ni|nunca|tampoco|falso|incorrecto|sin que)(?!\w)[^.;]{0,40}$")


def _afirma(frase: str, texto: str) -> bool:
    """Como `_aparece`, pero no cuenta la frase si va negada («la venta no extingue…»).

    Una respuesta que desmonta una premisa falsa suele repetir la frase errónea negándola."""
    plano = normaliza_cita(texto)
    f = normaliza_cita(frase)
    patron = re.escape(f[:-1]) + r"\w*" if f.endswith("*") else re.escape(f)
    for m in re.finditer(rf"(?<!\w){patron}(?!\w)", plano):
        previo = plano[max(0, m.start() - 60) : m.start()]
        if not _NEGACION.search(previo) and not _NEGACION.search(m.group(0)[:20]):
            return True
    return False


def evaluar_generacion(casos: list[Caso], buscador: Buscador, llm: LLM) -> dict[str, Any]:
    resultados: list[dict[str, Any]] = []
    for c in casos:
        inicio = time.perf_counter()
        try:
            r = responder(c.pregunta, buscador, llm, c.fecha, municipio=c.municipio)
        except LLMError as e:
            # Un fallo del modelo cuenta como caso fallido, no tumba la evaluación entera.
            resultados.append(
                {"id": c.id, "tipo": c.tipo if c.ambito else "fuera_de_ambito", "ok": False,
                 "abstiene": False, "error": str(e), "afirmaciones": 0, "descartadas": 0,
                 "citas_no_exactas": 0, "segundos": round(time.perf_counter() - inicio, 1)}
            )  # fmt: skip
            continue
        segundos = time.perf_counter() - inicio
        texto = " ".join(a.texto for a in r.afirmaciones)
        citadas = {a.fragmento.base_id for a in r.afirmaciones}
        # Una norma que reproduce literalmente la cita de referencia es igual de válida
        # (el RD 1312/2024 copia definiciones del Reglamento UE; el Decreto 143/2014, la Ley
        # 13/2011).
        if c.cita:
            ref = normaliza_cita(c.cita)
            citadas |= {
                b
                for a in r.afirmaciones
                if ref in normaliza_cita(a.fragmento.texto)
                for b in c.fuentes[:1]
            }
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
                "tipo": c.tipo if c.ambito else "fuera_de_ambito",
                "ok": ok,
                "abstiene": r.abstiene,
                "motivo_abstencion": r.motivo_abstencion,
                "fuente_correcta": bool(citadas & set(c.fuentes)),
                "hechos": _hechos_cubiertos(c, texto) if c.ambito else None,
                "afirmaciones": len(r.afirmaciones),
                "descartadas": r.descartadas,
                "citas_no_exactas": no_exactas,
                "segundos": round(segundos, 1),
                "respuesta": [f"{a.texto} [{a.fragmento.cita}]" for a in r.afirmaciones],
            }
        )
    propuestas = sum(x["afirmaciones"] + x["descartadas"] for x in resultados)
    verificadas = sum(x["afirmaciones"] for x in resultados)
    ids_en = {c.id for c in casos if c.ambito}
    en = [x for x in resultados if x["id"] in ids_en]
    fuera = [x for x in resultados if x["id"] not in ids_en]
    por_tipo: dict[str, dict[str, float]] = {}
    for x in resultados:
        t = por_tipo.setdefault(x["tipo"], {"casos": 0, "ok": 0})
        t["casos"] += 1
        t["ok"] += int(x["ok"])
    return {
        "modelo": llm.nombre,
        "por_tipo": por_tipo,
        "exactitud_en_ambito": round(sum(x["ok"] for x in en) / max(len(en), 1), 3),
        "abstencion_fuera_de_ambito": round(
            sum(x["abstiene"] for x in fuera) / max(len(fuera), 1), 3
        ),
        "citas_verificadas": round(verificadas / max(propuestas, 1), 3),
        "citas_mostradas": verificadas,
        "citas_mostradas_no_exactas": sum(x["citas_no_exactas"] for x in resultados),
        "casos": resultados,
    }
