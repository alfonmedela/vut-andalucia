"""Línea de comandos: `vut sync`, `vut buscar`, `vut preguntar`, `vut eval`."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Annotated

import typer

from vut_andalucia import corpus as corpus_mod
from vut_andalucia.answer import responder
from vut_andalucia.llm import LLMError, crear
from vut_andalucia.models import MotivoAbstencion, Respuesta
from vut_andalucia.retrieval import Buscador

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Normativa de viviendas de uso turístico en Andalucía, con citas verificadas.",
)

AVISO = (
    "Información sobre la normativa, no asesoramiento jurídico. Comprueba siempre el texto oficial."
)

Fecha = Annotated[
    str | None,
    typer.Option("--fecha", "-f", help="Fecha de referencia AAAA-MM-DD (por defecto, hoy)"),
]


def _fecha(valor: str | None) -> dt.date:
    if valor is None:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(valor)
    except ValueError as e:
        raise typer.BadParameter(f"Fecha inválida: {valor!r}. Usa AAAA-MM-DD.") from e


def _buscador() -> Buscador:
    try:
        return Buscador(corpus_mod.cargar())
    except (FileNotFoundError, corpus_mod.CorpusCorrupto) as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1) from e


MENSAJES_ABSTENCION = {
    MotivoAbstencion.SIN_FRAGMENTOS: (
        "Ninguna norma del corpus vigente en esa fecha trata de lo que preguntas."
    ),
    MotivoAbstencion.FUERA_DE_AMBITO: (
        "Los artículos encontrados no responden a la pregunta. Puede quedar fuera del corpus "
        "(ordenanzas municipales, fiscalidad, Decreto 28/2016, aún no incluido)."
    ),
    MotivoAbstencion.CITAS_NO_VERIFICADAS: (
        "El modelo propuso una respuesta, pero ninguna de sus citas aparece literalmente en el "
        "texto oficial, así que no se muestra."
    ),
}


def render(r: Respuesta) -> str:
    lineas = [f"Pregunta: {r.pregunta}", f"Normativa vigente a {r.fecha.isoformat()}", ""]
    if r.motivo_abstencion is not None:
        lineas.append(MENSAJES_ABSTENCION[r.motivo_abstencion])
    numeros: dict[str, int] = {}
    citas: dict[str, list[str]] = {}
    for a in r.afirmaciones:
        n = numeros.setdefault(a.fragmento.id, len(numeros) + 1)
        if a.cita_literal not in citas.setdefault(a.fragmento.id, []):
            citas[a.fragmento.id].append(a.cita_literal)
        lineas.append(f"• {a.texto} [{n}]")
    if numeros:
        lineas.append("")
        fragmentos = {a.fragmento.id: a.fragmento for a in r.afirmaciones}
        for fid, n in numeros.items():
            f = fragmentos[fid]
            hasta = f.vigente_hasta.isoformat() if f.vigente_hasta else "hoy"
            aviso = " (fecha de vigencia no publicada por el BOE)" if f.vigencia_incierta else ""
            lineas.append(
                f"[{n}] {f.cita} · versión vigente {f.vigente_desde.isoformat()} → {hasta}{aviso}"
            )
            lineas += [f"    «{c}»" for c in citas[fid]]
            lineas.append(f"    {f.url}")
    if r.descartadas:
        lineas.append(
            f"\n({r.descartadas} afirmación(es) descartada(s) por no poder verificar su cita.)"
        )
    lineas += ["", f"Modelo: {r.modelo} · {AVISO}"]
    return "\n".join(lineas)


@app.command()
def sync() -> None:
    """Descarga las normas del catálogo desde las fuentes oficiales y regenera el corpus."""
    from vut_andalucia.ingest import sincronizar

    for e in sincronizar():
        typer.echo(
            f"{e.norma.id}  {e.fragmentos:>4} fragmentos  sha256 {e.sha256[:12]}  {e.norma.titulo}"
        )


@app.command()
def buscar(consulta: str, fecha: Fecha = None, k: int = 8) -> None:
    """Muestra los fragmentos más relevantes, sin LLM."""
    for res in _buscador().buscar(consulta, _fecha(fecha), k):
        f = res.fragmento
        typer.echo(f"{res.puntuacion:6.2f}  {f.cita:<28} {f.texto[:90].replace(chr(10), ' ')}")


@app.command()
def preguntar(
    pregunta: str,
    fecha: Fecha = None,
    proveedor: Annotated[str, typer.Option(help="ollama")] = "ollama",
    modelo: Annotated[str | None, typer.Option(help="p. ej. qwen3:8b")] = None,
    como_json: Annotated[bool, typer.Option("--json", help="Salida JSON")] = False,
) -> None:
    """Responde con afirmaciones respaldadas por citas literales del texto oficial."""
    try:
        r = responder(pregunta, _buscador(), crear(proveedor, modelo), _fecha(fecha))
    except LLMError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from e
    typer.echo(r.model_dump_json(indent=2) if como_json else render(r))


@app.command(name="eval")
def evaluar(
    preguntas: Annotated[Path, typer.Option(help="YAML de evaluación")] = Path(
        "eval/preguntas.yaml"
    ),
    proveedor: Annotated[
        str | None, typer.Option(help="ollama; sin él solo evalúa la recuperación")
    ] = None,
    modelo: str | None = None,
    salida: Annotated[Path | None, typer.Option(help="Guardar el informe JSON")] = None,
) -> None:
    """Evalúa recuperación (siempre) y generación con citas (si se indica proveedor)."""
    from vut_andalucia.evaluation import cargar_preguntas, evaluar_generacion, evaluar_recuperacion

    buscador = _buscador()
    casos = cargar_preguntas(preguntas)
    informe: dict[str, object] = {"recuperacion": evaluar_recuperacion(casos, buscador)}
    if proveedor:
        informe["generacion"] = evaluar_generacion(casos, buscador, crear(proveedor, modelo))
    texto = json.dumps(informe, ensure_ascii=False, indent=2, default=str)
    if salida:
        salida.write_text(texto + "\n", encoding="utf-8")
    typer.echo(texto)
