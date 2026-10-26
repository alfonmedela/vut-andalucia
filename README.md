<div align="center">

# vut-andalucia

**Pregunta lo que quieras sobre la ley de pisos turísticos en Andalucía.
Cada respuesta va con la cita literal del BOE, y si no puede citarlo, no lo dice.**

[![CI](https://github.com/alfonmedela/vut-andalucia/actions/workflows/ci.yml/badge.svg)](https://github.com/alfonmedela/vut-andalucia/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![LLM local](https://img.shields.io/badge/LLM-100%25%20local%20con%20Ollama-black)
![Licencia MIT](https://img.shields.io/badge/licencia-MIT-green)

[English](README.en.md) · [Cómo funciona](#cómo-funciona) · [Evaluación](#evaluación) · [Fuentes](#fuentes-y-versiones)

</div>

```console
$ vut preguntar "¿Necesito permiso de los vecinos para poner mi piso en Airbnb?"

Normativa vigente a 2026-10-26

• Sí, necesita permiso de la comunidad de propietarios para poner su piso en Airbnb. [1]
• El permiso requiere el voto favorable de las tres quintas partes de los propietarios. [2]

[1] art. 7.3 LPH · versión vigente 2025-04-03 → hoy
    «El propietario de cada vivienda que quiera realizar el ejercicio de la actividad a que se
    refiere la letra e) del artículo 5 de la Ley 29/1994, de 24 de noviembre, de Arrendamientos
    Urbanos, en los términos establecidos en la normativa sectorial turística, deberá obtener
    previamente la aprobación expresa de la comunidad de propietarios.»
    https://www.boe.es/buscar/act.php?id=BOE-A-1960-10906#aseptimo
[2] art. 17.12 LPH · versión vigente 2025-04-03 → hoy
    «requerirá el voto favorable de las tres quintas partes del total de los propietarios que,
    a su vez, representen las tres quintas partes de las cuotas de participación.»
    https://www.boe.es/buscar/act.php?id=BOE-A-1960-10906#adiecisiete

Modelo: ollama:qwen3:8b · Información sobre la normativa, no asesoramiento jurídico.
```

La misma pregunta, en otra fecha, responde con la ley de entonces:

```console
$ vut preguntar "¿Qué mayoría hacía falta en la junta para limitar los pisos turísticos?" --fecha 2024-06-01

• Para limitar los pisos turísticos se necesitaba la mayoría de tres quintas partes de los propietarios. [1]
• Esta mayoría también era necesaria para establecer cuotas especiales de gastos. [1]

[1] art. 17.12 LPH · versión vigente 2019-03-06 → 2025-04-03
    «requerirá el voto favorable de las tres quintas partes del total de los propietarios»
    «esta misma mayoría se requerirá para el acuerdo por el que se establezcan cuotas especiales de gastos»
```

<sub>Salidas reales de `qwen3:8b` en local; las citas largas se han partido en líneas para leerlas.</sub>

## Por qué existe

La normativa de las viviendas de uso turístico (VUT) en Andalucía está repartida entre leyes
autonómicas, estatales y europeas, y cambia cada pocos meses. La Ley de Propiedad Horizontal
se reformó en abril de 2025, la LAU tuvo tres versiones del art. 5 en octubre de 2026 y el
Tribunal Supremo anuló en mayo de 2026 el Registro Único estatal. Los chatbots genéricos
responden con seguridad y sin fuente. Este proyecto hace lo contrario: **solo afirma lo que
puede citar, y comprueba la cita**.

## Qué lo hace distinto

| | |
|---|---|
| **Citas verificadas al carácter** | El modelo propone afirmaciones con una cita literal. Un verificador determinista, sin LLM, comprueba que la cita aparece tal cual en el texto oficial. Las que no aparecen se descartan, y si no queda ninguna, el asistente se abstiene. |
| **Respuestas en el tiempo** | Cada apartado guarda su rango de vigencia. `--fecha 2024-06-01` responde con la ley de ese día. Por ejemplo, el art. 17.12 LPH existió del 19/12/2018 al 24/01/2019, desapareció al decaer el RDL 21/2018, volvió el 06/03/2019 con el RDL 7/2019 y se reformó el 03/04/2025. |
| **Solo fuentes oficiales** | Se descarga de la API de datos abiertos del BOE, con un manifiesto que guarda el ELI, la fecha y el sha256 de cada texto. Nada de blogs. |
| **Habla como la gente, busca como el legislador** | Nadie pregunta por «la actividad de la letra e) del artículo 5 de la Ley 29/1994». Un glosario traduce «piso turístico» o «Airbnb» al término legal y marca los preceptos que regulan la figura por remisión. |
| **100 % local** | Funciona con Ollama y un modelo de 8B en un portátil. Las preguntas no salen de tu máquina. |
| **Evaluación pública** | Cada pregunta del set lleva su cita de referencia, y un test comprueba que esa cita sigue siendo literal en el BOE. Si cambia la ley, el CI falla. |

## Empezar

```bash
# 1. Ollama y el modelo (una vez)
ollama pull qwen3:8b

# 2. El proyecto
git clone https://github.com/alfonmedela/vut-andalucia && cd vut-andalucia
uv sync

# 3. Preguntar
uv run vut preguntar "¿Puede la comunidad prohibir los pisos turísticos?"
uv run vut preguntar "¿Qué mayoría hacía falta para limitar los pisos turísticos?" --fecha 2024-06-01
uv run vut buscar "multa por alquiler turístico sin registro"   # solo recuperación, sin LLM
```

El corpus viene incluido en el paquete. `uv run vut sync` lo vuelve a descargar del BOE.

## Cómo funciona

```
API del BOE ──► parser por artículo y apartado ──► corpus JSONL
                (versiones + vigencia + sha256)         │
                                                        ▼
pregunta ──► glosario ──► BM25 (español, Snowball) filtrado por fecha ──► top-k fragmentos
                                                        │
                                                        ▼
                     LLM (Ollama, salida JSON con esquema): afirmaciones + cita literal
                                                        │
                                                        ▼
                  verificador: ¿la cita está, tal cual, en el fragmento? ──► respuesta | abstención
```

**Decisiones de diseño**

- **No hay base de datos vectorial.** El derecho vigente sobre VUT son unos cientos de
  apartados, y un BM25 en memoria se construye en milisegundos. Menos piezas significa menos
  fallos. Los embeddings se añadirán como señal de fusión, no como sustituto.
- **La unidad es el apartado.** Una reforma reescribe el artículo entero en el BOE aunque
  toque una línea. Al fusionar versiones idénticas, cada apartado conserva la fecha en que su
  texto entró de verdad en vigor.
- **La cita la verifica el código, no un LLM.** Usar un LLM como juez de citas añade otra
  fuente de error. La comprobación es binaria y auditable: la cita debe ser un tramo continuo
  de palabras completas (6 como mínimo) del fragmento que dice citar. No admite elipsis
  interiores, porque bastaría con saltarse un «no» o un «salvo» para darle la vuelta a la
  norma. Lo que **no** garantiza es que la paráfrasis interprete bien la cita, y por eso la
  cita se muestra siempre al lado.
- **Abstenerse no es lo mismo que fallar.** Hay tres motivos de abstención distintos, cada uno
  con su mensaje: nada vigente coincide, los artículos no responden, o el modelo respondió sin
  citas literales. Un error de Ollama o una salida fuera de esquema es un error, no un
  «la ley no dice nada».
- **Los fragmentos son datos.** El prompt los delimita y ordena ignorar cualquier instrucción
  que contengan. El set de evaluación incluye un intento de inyección.

## Evaluación

```bash
uv run vut eval                      # recuperación, sin LLM (corre en CI)
uv run vut eval --proveedor ollama   # generación con citas
```

Resultados con el set actual (15 preguntas: 12 en ámbito, 3 fuera), `qwen3:8b` en un MacBook
con 24 GB, 26/10/2026 ([informe completo](eval/resultados/f1-qwen3-8b.json)):

| Métrica | Resultado |
|---|---|
| Recuperación: acierto@5 / MRR | 12/12 · 0,85 |
| Exactitud en ámbito | 11/12 (91,7 %) |
| Abstención fuera de ámbito (IVA, cultura general, inyección) | 3/3 |
| Citas propuestas que eran literales | 96,7 % (el resto se descartó) |
| Citas mostradas que no eran literales | **0 de 29** |
| Tiempo medio por respuesta | 11 s |

El fallo es honesto: en «¿qué es actividad clandestina?» el modelo cita bien el art. 30.4,
pero resume «sin cumplir con obligaciones legales» y no dice que lo que falta es la
declaración responsable. La métrica lo marca porque mira lo que afirma el modelo, no la cita.

Las métricas:

- **acierto@5**: la fuente correcta está entre los 5 primeros fragmentos.
- **exactitud**: la respuesta cita la fuente correcta, vigente en la fecha pedida, y lo que
  afirma (no la cita) contiene los hechos clave, por palabras completas.
- **citas literales**: el porcentaje de citas propuestas por el modelo que superaron el
  verificador. Las demás se descartaron antes de mostrarse. Un segundo control, más estricto
  e independiente (igualdad carácter a carácter salvo espacios), revisa las que sí se muestran.

El set está en [`eval/preguntas.yaml`](eval/preguntas.yaml) e incluye preguntas por fecha y
preguntas fuera de ámbito (IVA, cultura general y una inyección de prompt).

## Fuentes y versiones

| Norma | Identificador | Para qué |
|---|---|---|
| Ley 13/2011, del Turismo de Andalucía | [BOE-A-2012-876](https://www.boe.es/buscar/act.php?id=BOE-A-2012-876) | Registro, actividad clandestina, infracciones y multas |
| Ley 49/1960, de Propiedad Horizontal | [BOE-A-1960-10906](https://www.boe.es/buscar/act.php?id=BOE-A-1960-10906) | Acuerdos de la comunidad (arts. 7.3 y 17.12, disp. adic. 2.ª) |
| Ley 29/1994, de Arrendamientos Urbanos | [BOE-A-1994-26003](https://www.boe.es/buscar/act.php?id=BOE-A-1994-26003) | Exclusión de la cesión turística (art. 5.e) |

Fechas exactas y hashes en [`manifest.json`](src/vut_andalucia/data/corpus/manifest.json).

## Hoja de ruta

- [x] Ingesta versionada del BOE, BM25 por fecha, respuestas con citas verificadas y evaluación
- [ ] Decreto 28/2016 de viviendas de uso turístico (BOJA, consolidado de 2025) y Decreto-ley 1/2025
- [ ] RD 1312/2024 (con la anulación parcial de la STS 620/2026), RD 933/2021 y Reglamento (UE) 2024/1028
- [ ] Búsqueda híbrida (BM25 + bge-m3 con RRF) y expansión de remisiones entre artículos
- [ ] Claude como proveedor opcional
- [ ] API (FastAPI) y web mínima con panel de citas
- [ ] Servidor MCP para usarlo desde Claude Desktop o Claude Code
- [ ] Vigía semanal de cambios en el BOE y el BOJA

## Límites

- **No es asesoramiento jurídico.** Explica qué dice la norma, no qué debes hacer en tu caso.
- Por ahora **no incluye** el Decreto 28/2016 ni las ordenanzas municipales, la fiscalidad o el
  turismo rural. Ante esas preguntas, el asistente se abstiene.
- Cuando el BOE no publica la fecha de vigencia de una versión, se usa la de publicación y la
  respuesta lo advierte.

## Licencia

El código tiene licencia [MIT](LICENSE). Los textos legales proceden del BOE y se reutilizan
conforme a sus [condiciones de reutilización](https://www.boe.es/informacion/aviso_legal/index.php#reutilizacion),
citando la fuente.

---

Lo hace [Alfonso Medela](https://alfonsomedela.com), físico y desarrollador de IA. Antes,
Chief AI Officer de un producto sanitario con marcado CE: allí aprendí que una IA que acierta
casi siempre no basta. Tiene que poder demostrarlo.
