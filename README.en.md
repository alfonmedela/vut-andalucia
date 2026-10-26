<div align="center">

# VUT Andalucía

### Ask the law.

**Everything about holiday rentals in Andalusia, Spain, with the exact quote from the official gazette or the town hall.<br>
If it can't quote it, it won't say it.**

[![CI](https://github.com/alfonmedela/vut-andalucia/actions/workflows/ci.yml/badge.svg)](https://github.com/alfonmedela/vut-andalucia/actions/workflows/ci.yml)
![Official sources](https://img.shields.io/badge/official_sources-40-7A1E2A)
![Municipalities](https://img.shields.io/badge/municipalities-785-7A1E2A)
![LLM](https://img.shields.io/badge/LLM-100%25%20local%20or%20Claude-1C1714)
![MIT](https://img.shields.io/badge/license-MIT-1C1714)

[Español](README.md) · [How it works](#how-it-works) · [Coverage](#coverage) · [Evaluation](#evaluation) · [Install](#install)

<img src="docs/img/demo.gif" alt="Asking which majority a community of owners needs to ban tourist flats; the answer cites art. 17.12 of the Horizontal Property Act, highlights the exact sentence and shows the article's history since 2018" width="900">

</div>

> The assistant answers in Spanish, because the law is in Spanish. This page explains it for an
> international audience.

## Who it is for

Owners, property managers, building administrators and lawyers who need to know what the rules
actually say about *viviendas de uso turístico* (VUT, the Airbnb-style flats regulated in
Andalusia). You ask as you would ask a colleague, and get a structured answer (**the rule ·
exceptions · what to do · risks**) where every sentence carries a quote. Click it to see the
official text with the sentence highlighted, the date it took effect and **how that article has
changed over time**.

<p align="center"><img src="docs/img/respuesta.png" alt="Answer with art. 7.3 LPH highlighted in the citation panel" width="900"></p>

## What makes it different

- **Quotes checked by code, not by another AI.** The model proposes each claim with a verbatim quote. A deterministic verifier checks that the quote is a contiguous run of whole words in the cited official text, with no ellipsis that could skip a "not". Claims that fail are dropped; if none survive, the assistant abstains.
- **Point-in-time law.** Every article keeps all its versions, so you can ask about the law in force today or in June 2024.
- **It tells you what you can't see.** Supreme Court annulments (the 2026 rulings on the national rental registry), emergency decrees still awaiting parliamentary confirmation, and deferred effects (Spain's new VAT on stays of 30 nights or less applies from 1 December 2026 even though the consolidated BOE dates it October). Each warning sits next to the quote it affects.
- **Your town.** It detects the municipality in the question (or you pick one of Andalusia's 785) and adds its local rules. If the answer depends on the town and you didn't say which, it asks. If the corpus has no rules for your town, it says so.
- **Law vs. case law vs. guidance.** Statutes and ordinances bind; court rulings interpret; the regional government's FAQ only guides. The answer labels each.
- **Private and free.** Runs entirely on your laptop with Ollama, or with Claude for higher-quality answers.
- **Knows when to stay quiet.** Before the model is called, a code gate catches questions about other Spanish regions, about the rules of a town the corpus doesn't have, or that try to give the assistant instructions. Afterwards a second review checks that each quote really proves what the claim says.

## How it works

<p align="center"><img src="docs/img/como-funciona.svg" alt="Official sources, every article with its history, search by date and municipality, the model proposes, the verifier checks" width="900"></p>

1. **Official sources:** the BOE open-data API (which also consolidates Andalusian laws), the Junta de Andalucía's consolidated-text portal (BOJA, via a headless browser, stored for offline rebuilds), the EU Publications Office (Cellar) and provincial gazettes. Every text is fingerprinted with sha256.
2. **Every article with its history:** versions and validity dates per paragraph, plus the official notes that matter ("annulled insofar as…", "with effect from 1 December 2026…").
3. **The law of that day and town:** hybrid search, Spanish BM25 with a colloquial glossary plus bge-m3 multilingual embeddings, fused with RRF, restricted to what was in force on that date and in that municipality.
4. **The model proposes:** qwen3 (local) or Claude writes short claims, each with a source and a verbatim quote, and lists the facts it would need to refine the answer.
5. **The verifier decides:** only verified claims are shown.

## Coverage

**40 official sources, 2,245 versioned fragments** (as of 26 October 2026): Andalusia's Tourism Act and decrees on holiday flats (four versions since 2016), the tourism register, tourist apartments, rural tourism, complaint forms and inspection; local rules for **Málaga, Seville, Granada, Cádiz, El Puerto de Santa María, Chiclana and Jerez**; Spain's Horizontal Property Act, Urban Leases Act, national rental registry and the **three 2026 Supreme Court rulings** that partly annulled it, guest-registration rules, VAT, income tax, property and transfer taxes; and the EU Regulation 2024/1028 on short-term rental data. See [`fuentes.yaml`](src/vut_andalucia/data/fuentes.yaml).

## Evaluation

The evaluation questions are **real**: they come from a group of property owners advised by a
specialist lawyer, and from public forums and FAQs, anonymised. Each one has its correct source and
a **reference quote that a test checks verbatim** against the version in force on its date, so CI
fails when the law changes. **105 cases**: 93 in scope (edge cases, false premises, point-in-time
and municipal questions) and 12 out of scope (other regions, towns with no rules in the corpus,
trivia and two attempts to give the assistant instructions).

| Answers (26 Oct 2026) | qwen3:8b, local | Claude Opus |
|---|---|---|
| **Correct, in scope**, according to a judge that compares with the lawyer-style reference note | — | **80%** |
| Correct according to the automatic grader (right source + literal key facts) | 28% | 62% |
| Abstains when the question is outside the corpus | 31% | **100%** |
| Quotes shown that are not verbatim in the official text | **0** | **0** ¹ |
| Claims dropped by the meaning review | — | 75 of 821 |
| Time per answer | ~80 s | ~40 s |

By question type (Claude, judge): plain 28/33, false premise 15/15, edge case 16/21,
point-in-time 9/15, municipal 6/9, out of scope 12/12. Point-in-time questions ("what was required
in 2017?") are the weakest spot.

¹ One quote differs only in a typographic quotation mark that the official text itself mixes; no
word changes.

The automatic grader is deliberately strict and rejects paraphrases; the judge is another model and
can be wrong. Both are published, with the full case-by-case report in
[`eval/resultados/f3-claude-opus.json`](eval/resultados/f3-claude-opus.json).

| Retrieval (hybrid) | 10 fragments (local model) | 25 fragments (Claude) |
|---|---|---|
| Correct source reaches the model | 81% | 90% |
| Exact reference sentence reaches the model | 66% | 77% |

With a hand-made set of 12 questions, retrieval scored 100%. Real questions are harder, and
the numbers say so.

## Install

```bash
ollama pull qwen3:8b && ollama pull bge-m3
git clone https://github.com/alfonmedela/vut-andalucia && cd vut-andalucia
uv run vut web          # then open http://127.0.0.1:8000
```

With your own Claude subscription (Claude Code installed and signed in, personal use only):
`uv run vut web --proveedor suscripcion`. With an Anthropic API key, as the public demo does:
`uv sync --extra claude` and `ANTHROPIC_API_KEY=... uv run vut web --proveedor claude`.
CLI: `uv run vut preguntar "..." --municipio Cádiz --fecha 2024-06-01`.

Adding a law or a municipality means editing [`fuentes.yaml`](src/vut_andalucia/data/fuentes.yaml)
and running `uv run vut sync && uv run vut embed`. A Hugging Face Spaces Dockerfile lives in
[`space/`](space).

**Not legal advice.** Code under the MIT licence; legal texts are reused from their official
sources with attribution.

---

<p align="center">
Built by <a href="https://alfonsomedela.com"><b>Alfonso Medela</b></a>, physicist and AI engineer.<br>
I used to be Chief AI Officer of a CE-marked medical device, which taught me that an AI that is right most of the time is not enough: it has to be able to prove it.
</p>
