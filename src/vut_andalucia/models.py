"""Modelos de datos del corpus legal y de las respuestas."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field


class Norma(BaseModel):
    """Una norma del corpus (ley, decreto, reglamento) y su procedencia."""

    id: str = Field(description="Identificador oficial, p. ej. BOE-A-1960-10906")
    titulo: str
    corta: str = Field(description="Abreviatura para citas, p. ej. LPH")
    rango: str
    ambito: str
    url: str
    eli: str | None = None
    fuente: str = Field(description="boe | boja | eurlex")


class Fragmento(BaseModel):
    """Unidad recuperable: un artículo (o un apartado) en una versión concreta."""

    id: str = Field(description="norma#bloque[.apartado]@AAAA-MM-DD")
    norma_id: str
    bloque_id: str
    apartado: str | None = None
    cita: str = Field(description="Forma humana de citar, p. ej. art. 17.12 LPH")
    encabezado: str
    ruta: list[str] = Field(default_factory=list, description="Título › Capítulo › Sección")
    texto: str
    vigente_desde: dt.date
    vigente_hasta: dt.date | None = None
    vigencia_incierta: bool = Field(
        default=False, description="El BOE no publica fecha de vigencia; se usa la de publicación"
    )
    modificado_por: str | None = None
    url: str

    @property
    def base_id(self) -> str:
        """Identificador sin versión, estable entre reformas."""
        return self.id.split("@", 1)[0]

    def vigente_en(self, fecha: dt.date) -> bool:
        if fecha < self.vigente_desde:
            return False
        return self.vigente_hasta is None or fecha < self.vigente_hasta


class Afirmacion(BaseModel):
    texto: str
    fragmento: Fragmento
    cita_literal: str


class MotivoAbstencion(StrEnum):
    SIN_FRAGMENTOS = "sin_fragmentos"  # nada vigente en el corpus coincide con la pregunta
    FUERA_DE_AMBITO = "fuera_de_ambito"  # el modelo dice que los fragmentos no responden
    CITAS_NO_VERIFICADAS = "citas_no_verificadas"  # el modelo respondió, pero sin citas literales


class Respuesta(BaseModel):
    pregunta: str
    fecha: dt.date
    afirmaciones: list[Afirmacion]
    descartadas: int = Field(
        default=0, description="Afirmaciones del modelo cuya cita no se pudo verificar"
    )
    motivo_abstencion: MotivoAbstencion | None = None
    modelo: str

    @property
    def abstiene(self) -> bool:
        return self.motivo_abstencion is not None
