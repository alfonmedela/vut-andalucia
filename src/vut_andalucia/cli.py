"""Línea de comandos: `vut sync`, `vut buscar`, `vut preguntar`, `vut eval`."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Annotated

import typer

from vut_andalucia import corpus as corpus_mod
from vut_andalucia import municipios
from vut_andalucia.answer import responder
from vut_andalucia.embeddings import ClienteOllama, EmbeddingsError
from vut_andalucia.embeddings import construir as construir_embeddings
from vut_andalucia.llm import LLMError, crear
from vut_andalucia.models import MotivoAbstencion, Respuesta, TipoAfirmacion
from vut_andalucia.retrieval import Buscador
from vut_andalucia.servicio import crear_buscador, resolver_municipio

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


def _buscador(semantico: bool = True) -> Buscador:
    try:
        return crear_buscador(semantico=semantico)
    except (FileNotFoundError, corpus_mod.CorpusCorrupto, EmbeddingsError) as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1) from e


def _municipio(valor: str | None) -> str | None:
    if valor is None:
        return None
    try:
        return resolver_municipio(valor)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


Municipio = Annotated[
    str | None,
    typer.Option("--municipio", "-m", help="Municipio de la vivienda (p. ej. Málaga)"),
]
Lexico = Annotated[
    bool, typer.Option("--lexico", help="Solo búsqueda léxica (sin embeddings ni Ollama)")
]


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


ROTULOS = {
    TipoAfirmacion.REGLA: "Lo que dice la norma",
    TipoAfirmacion.EXCEPCION: "Excepciones y matices",
    TipoAfirmacion.PASO: "Qué hacer",
    TipoAfirmacion.RIESGO: "Riesgos",
}


def render(r: Respuesta) -> str:
    cabecera = f"Normativa vigente a {r.fecha.isoformat()}"
    if r.municipio:
        m = municipios.por_slug()[r.municipio]
        cabecera += f" · {m.nombre} ({m.provincia})" + (
            " [deducido]" if r.municipio_detectado else ""
        )
    lineas = [f"Pregunta: {r.pregunta}", cabecera, ""]
    if r.motivo_abstencion is not None:
        lineas.append(MENSAJES_ABSTENCION[r.motivo_abstencion])
    numeros: dict[str, int] = {}
    citas: dict[str, list[str]] = {}
    for a in r.afirmaciones:
        numeros.setdefault(a.fragmento.id, len(numeros) + 1)
        if a.cita_literal not in citas.setdefault(a.fragmento.id, []):
            citas[a.fragmento.id].append(a.cita_literal)
    for tipo, rotulo in ROTULOS.items():
        grupo = [a for a in r.afirmaciones if a.tipo == tipo]
        if not grupo:
            continue
        if any(a.tipo != TipoAfirmacion.REGLA for a in r.afirmaciones):
            lineas.append(rotulo.upper())
        lineas += [f"• {a.texto} [{numeros[a.fragmento.id]}]" for a in grupo]
        lineas.append("")
    if r.preguntas_pendientes:
        lineas.append("PARA AFINAR LA RESPUESTA")
        lineas += [f"? {p}" for p in r.preguntas_pendientes]
        lineas.append("")
    if r.avisos:
        lineas += [f"⚠ {a}" for a in r.avisos]
        lineas.append("")
    if numeros:
        fragmentos = {a.fragmento.id: a.fragmento for a in r.afirmaciones}
        for fid, n in numeros.items():
            f = fragmentos[fid]
            norma = r.normas.get(f.norma_id)
            hasta = f.vigente_hasta.isoformat() if f.vigente_hasta else "hoy"
            aviso = " (fecha de vigencia no publicada por el BOE)" if f.vigencia_incierta else ""
            clase = f" · {norma.clase.value}" if norma and norma.clase.value != "norma" else ""
            lineas.append(
                f"[{n}] {f.cita}{clase} · versión vigente {f.vigente_desde.isoformat()} → "
                f"{hasta}{aviso}"
            )
            lineas += [f"    «{c}»" for c in citas[fid]]
            lineas.append(f"    {f.url}")
    if r.descartadas:
        lineas.append(
            f"\n({r.descartadas} afirmación(es) descartada(s) por no poder verificar su cita.)"
        )
    lineas += ["", f"Modelo: {r.modelo} · Búsqueda: {r.busqueda} · {AVISO}"]
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
def embed() -> None:
    """Calcula los embeddings bge-m3 del corpus (necesita Ollama con `ollama pull bge-m3`)."""
    corpus = corpus_mod.cargar()
    try:
        n = construir_embeddings(corpus, ClienteOllama(timeout=600))
    except EmbeddingsError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from e
    typer.echo(f"{n} vectores calculados; {len(corpus.fragmentos) - n} reutilizados.")


@app.command()
def buscar(
    consulta: str,
    fecha: Fecha = None,
    k: int = 8,
    municipio: Municipio = None,
    lexico: Lexico = False,
) -> None:
    """Muestra los fragmentos más relevantes, sin LLM."""
    b = _buscador(semantico=not lexico)
    for res in b.buscar(consulta, _fecha(fecha), k, municipio=_municipio(municipio)):
        f = res.fragmento
        typer.echo(f"{res.puntuacion:6.3f}  {f.cita:<34} {f.texto[:80].replace(chr(10), ' ')}")
    typer.echo(f"(búsqueda {b.ultima_busqueda})")


@app.command()
def preguntar(
    pregunta: str,
    fecha: Fecha = None,
    proveedor: Annotated[str, typer.Option(help="ollama | claude")] = "ollama",
    modelo: Annotated[str | None, typer.Option(help="p. ej. qwen3:8b")] = None,
    como_json: Annotated[bool, typer.Option("--json", help="Salida JSON")] = False,
    municipio: Municipio = None,
    lexico: Lexico = False,
) -> None:
    """Responde con afirmaciones respaldadas por citas literales del texto oficial."""
    if proveedor not in ("ollama", "claude"):
        raise typer.BadParameter(f"Proveedor desconocido: {proveedor} (ollama | claude)")
    try:
        r = responder(
            pregunta,
            _buscador(semantico=not lexico),
            crear(proveedor, modelo),
            _fecha(fecha),
            municipio=_municipio(municipio),
        )
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
    lexico: Lexico = False,
) -> None:
    """Evalúa recuperación (siempre) y generación con citas (si se indica proveedor)."""
    from vut_andalucia.evaluation import cargar_preguntas, evaluar_generacion, evaluar_recuperacion

    buscador = _buscador(semantico=not lexico)
    casos = cargar_preguntas(preguntas)
    informe: dict[str, object] = {"recuperacion": evaluar_recuperacion(casos, buscador)}
    if proveedor:
        informe["generacion"] = evaluar_generacion(casos, buscador, crear(proveedor, modelo))
    texto = json.dumps(informe, ensure_ascii=False, indent=2, default=str)
    if salida:
        salida.write_text(texto + "\n", encoding="utf-8")
    typer.echo(texto)


@app.command()
def web(
    host: Annotated[str, typer.Option(help="Interfaz de red")] = "127.0.0.1",
    puerto: Annotated[int, typer.Option(help="Puerto")] = 8000,
    proveedor: Annotated[str, typer.Option(help="ollama | claude")] = "ollama",
    modelo: Annotated[str | None, typer.Option(help="p. ej. qwen3:8b")] = None,
) -> None:
    """Arranca la interfaz web y la API en http://127.0.0.1:8000."""
    import os

    import uvicorn

    os.environ["VUT_PROVEEDOR"] = proveedor
    if modelo:
        os.environ["VUT_MODELO"] = modelo
    uvicorn.run("vut_andalucia.api:app", host=host, port=puerto, log_level="info")
