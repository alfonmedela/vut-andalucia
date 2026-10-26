<div align="center">

# vut-andalucia

**Ask anything about holiday-rental law in Andalusia, Spain.
Every answer comes with a verbatim quote from the official gazette — and if it can't quote it, it won't say it.**

[![CI](https://github.com/alfonmedela/vut-andalucia/actions/workflows/ci.yml/badge.svg)](https://github.com/alfonmedela/vut-andalucia/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Local LLM](https://img.shields.io/badge/LLM-100%25%20local%20via%20Ollama-black)
![MIT](https://img.shields.io/badge/license-MIT-green)

[Español](README.md) · [How it works](#how-it-works) · [Evaluation](#evaluation) · [Sources](#sources)

</div>

> The assistant answers in Spanish, because the law is in Spanish. This page explains the
> engineering for an international audience.

```console
$ vut preguntar "¿Necesito permiso de los vecinos para poner mi piso en Airbnb?"
  # "Do I need my neighbours' permission to list my flat on Airbnb?"

• Sí, necesita permiso de la comunidad de propietarios para poner su piso en Airbnb. [1]
• El permiso requiere el voto favorable de las tres quintas partes de los propietarios. [2]

[1] art. 7.3 LPH · versión vigente 2025-04-03 → hoy
    «…deberá obtener previamente la aprobación expresa de la comunidad de propietarios.»
[2] art. 17.12 LPH · versión vigente 2025-04-03 → hoy
    «requerirá el voto favorable de las tres quintas partes del total de los propietarios…»
```

## Why

Spanish short-term-rental law ("viviendas de uso turístico", VUT) is spread across regional,
national and EU rules, and it keeps moving. The Horizontal Property Act changed in April 2025.
The Urban Leases Act's article 5 had three versions in October 2026. The Supreme Court struck
down the national rental registry in May 2026. Generic chatbots answer confidently and without
sources. This project does the opposite: **it only states what it can quote, and it checks the
quote.**

## What makes it different

- **Character-level verified citations.** The LLM proposes claims, each with a verbatim quote.
  Deterministic code (no LLM) checks that the quote is a contiguous run of whole words, at
  least 6, in the fragment it cites. Interior ellipses are rejected: skipping a single "no"
  could invert the law. Unverified claims are dropped. If none survive, the assistant abstains.
- **Point-in-time answers.** Every paragraph keeps its validity range, so `--fecha 2024-06-01`
  answers with the law as it stood that day. The ingester reconstructs real legislative
  history. For example, art. 17.12 existed from 19 Dec 2018, vanished on 24 Jan 2019 when an
  emergency decree lapsed, came back on 6 Mar 2019, and was amended on 3 Apr 2025.
- **Official sources only.** Texts come from the BOE open-data API, with a manifest that
  records the ELI, version dates and sha256 of every text.
- **Speaks like people, searches like lawmakers.** A small glossary maps "Airbnb" or "piso
  turístico" to the statutory wording, which only ever refers to "the activity in letter e)
  of article 5 of Law 29/1994".
- **Runs 100% locally** with Ollama and an 8B model on a laptop.
- **Honest abstentions.** "Nothing in force matches", "the retrieved articles don't answer"
  and "the model answered without literal quotes" are three different outcomes. An LLM error
  is an error, never a silent "the law says nothing".

## Quickstart

```bash
ollama pull qwen3:8b
git clone https://github.com/alfonmedela/vut-andalucia && cd vut-andalucia
uv sync
uv run vut preguntar "¿Puede la comunidad prohibir los pisos turísticos?"
uv run vut preguntar "¿Qué mayoría hacía falta para limitar los pisos turísticos?" --fecha 2024-06-01
uv run vut buscar "multa por alquiler turístico sin registro"   # retrieval only, no LLM
```

## How it works

```
BOE API ──► per-article/paragraph parser ──► versioned JSONL corpus (+ sha256 manifest)
question ──► glossary ──► Spanish BM25 filtered by date ──► top-k fragments
         ──► LLM (Ollama, JSON-schema output): claims + verbatim quotes
         ──► verifier: is the quote, as is, in the cited fragment? ──► answer | abstain
```

Design decisions:

- **No vector database.** The VUT corpus is a few hundred paragraphs, and an in-memory BM25
  builds in milliseconds. Embeddings will come in as a fusion signal, not a replacement.
- **The paragraph is the unit.** The BOE republishes the whole article on every amendment.
  Merging identical consecutive versions gives each paragraph the date its own wording took
  effect.
- **Code verifies quotes, not an LLM judge.** The check is binary and auditable. What it does
  **not** guarantee is that the paraphrase reads the quote correctly, which is why the quote
  is always shown next to it.

## Evaluation

| Metric (`qwen3:8b`, 15 questions, 26 Oct 2026) | Result |
|---|---|
| Retrieval hit@5 / MRR | 12/12 · 0.85 |
| In-scope accuracy (right source, valid on the date, key facts in the claim) | 11/12 |
| Abstention on out-of-scope (VAT, trivia, prompt injection) | 3/3 |
| Proposed quotes that were literal | 96.7% (the rest were dropped) |
| Displayed quotes that were not literal | **0 of 29** |

Each reference quote in [`eval/preguntas.yaml`](eval/preguntas.yaml) is itself tested against
the current BOE text, so CI fails when the law changes.

## Sources

| Law | ID |
|---|---|
| Ley 13/2011, Andalusian Tourism Act | [BOE-A-2012-876](https://www.boe.es/buscar/act.php?id=BOE-A-2012-876) |
| Ley 49/1960, Horizontal Property Act | [BOE-A-1960-10906](https://www.boe.es/buscar/act.php?id=BOE-A-1960-10906) |
| Ley 29/1994, Urban Leases Act | [BOE-A-1994-26003](https://www.boe.es/buscar/act.php?id=BOE-A-1994-26003) |

Roadmap: Andalusian Decreto 28/2016 (BOJA), RD 1312/2024, RD 933/2021 and EU Regulation
2024/1028; hybrid BM25 + bge-m3 retrieval; Claude as an optional provider; an API and a web
UI; an MCP server; a weekly watcher for changes in the law.

**Not legal advice.** Code under the MIT licence. Legal texts are reused from the BOE under
its reuse terms.

---

Built by [Alfonso Medela](https://alfonsomedela.com), physicist and AI engineer. I used to be
Chief AI Officer of a CE-marked medical device, which taught me that an AI that is right most
of the time is not enough: it has to be able to prove it.
