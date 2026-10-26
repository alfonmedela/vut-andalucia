"""Modelos de datos del corpus legal y de las respuestas."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field


class Clase(StrEnum):
    NORMA = "norma"  # ley, decreto, reglamento, ordenanza: obliga
    JURISPRUDENCIA = "jurisprudencia"  # sentencia: interpreta, no es norma
    DOCTRINA = "doctrina"  # criterio administrativo (DGT, Junta): orienta, no obliga


class Ambito(StrEnum):
    UE = "ue"
    ESTATAL = "estatal"
    ANDALUCIA = "andalucia"
    MUNICIPAL = "municipal"


class Norma(BaseModel):
    """Un documento del corpus (norma, sentencia o criterio) y su procedencia."""

    id: str = Field(description="Identificador oficial, p. ej. BOE-A-1960-10906")
    titulo: str
    corta: str = Field(description="Abreviatura para citas, p. ej. LPH")
    rango: str
    ambito: str = Field(description="Ámbito tal como lo publica la fuente (Estatal, Autonómico…)")
    url: str
    eli: str | None = None
    fuente: str = Field(description="boe | boja | eurlex | documento")
    clase: Clase = Clase.NORMA
    territorio: Ambito = Ambito.ESTATAL
    municipio: str | None = Field(default=None, description="Slug del municipio si es municipal")
    estado: str | None = Field(
        default=None, description="Aviso sobre su vigencia, p. ej. anulación parcial por el TS"
    )


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
    nota: str | None = Field(
        default=None,
        description="Advertencia oficial que acompaña al texto (anulación parcial, aplicación…)",
    )
    url: str

    @property
    def base_id(self) -> str:
        """Identificador sin versión, estable entre reformas."""
        return self.id.split("@", 1)[0]

    def vigente_en(self, fecha: dt.date) -> bool:
        if fecha < self.vigente_desde:
            return False
        return self.vigente_hasta is None or fecha < self.vigente_hasta


class TipoAfirmacion(StrEnum):
    REGLA = "regla"  # lo que dice la norma
    EXCEPCION = "excepcion"  # salvedades, régimen transitorio, supuestos excluidos
    PASO = "paso"  # qué hacer: trámite, plazo, órgano
    RIESGO = "riesgo"  # sanción, cancelación, nulidad


class Afirmacion(BaseModel):
    texto: str
    fragmento: Fragmento
    cita_literal: str
    tipo: TipoAfirmacion = TipoAfirmacion.REGLA


class MotivoAbstencion(StrEnum):
    SIN_FRAGMENTOS = "sin_fragmentos"  # nada vigente en el corpus coincide con la pregunta
    FUERA_DE_AMBITO = "fuera_de_ambito"  # el modelo dice que los fragmentos no responden
    CITAS_NO_VERIFICADAS = "citas_no_verificadas"  # el modelo respondió, pero sin citas literales
    NO_RESPALDADAS = "no_respaldadas"  # las citas existen, pero la revisión no ve que lo prueben
    INSTRUCCIONES = "instrucciones"  # la pregunta intenta dar órdenes al asistente
    OTRO_TERRITORIO = "otro_territorio"  # trata de otra comunidad autónoma
    MUNICIPIO_SIN_NORMAS = "municipio_sin_normas"  # pregunta por normas de un municipio sin corpus


class Respuesta(BaseModel):
    pregunta: str
    fecha: dt.date
    afirmaciones: list[Afirmacion]
    descartadas: int = Field(
        default=0, description="Afirmaciones del modelo cuya cita no se pudo verificar"
    )
    no_respaldadas: int = Field(
        default=0,
        description="Afirmaciones con cita literal que la revisión de significado descartó",
    )
    descartes_revision: list[str] = Field(
        default_factory=list, description="Por cada afirmación descartada en la revisión, el motivo"
    )
    revisada: bool = Field(
        default=False, description="Se comprobó que cada cita respalda lo que se afirma"
    )
    motivo_abstencion: MotivoAbstencion | None = None
    modelo: str
    municipio: str | None = Field(default=None, description="Slug del municipio considerado")
    municipio_detectado: bool = Field(
        default=False, description="El municipio se dedujo de la pregunta, no se indicó aparte"
    )
    preguntas_pendientes: list[str] = Field(
        default_factory=list,
        description="Datos del caso que el asistente necesita para afinar la respuesta",
    )
    avisos: list[str] = Field(
        default_factory=list, description="Advertencias que acompañan a toda la respuesta"
    )
    normas: dict[str, Norma] = Field(
        default_factory=dict, description="Normas citadas, para mostrar su rango y estado"
    )
    busqueda: str = Field(default="léxica", description="Tipo de búsqueda que se usó")

    @property
    def abstiene(self) -> bool:
        return self.motivo_abstencion is not None
