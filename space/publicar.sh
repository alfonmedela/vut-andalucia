#!/bin/sh
# Publica la demo en Hugging Face Spaces: ./space/publicar.sh <usuario>/<space>
# Requiere `hf auth login` (o HF_TOKEN). El secreto ANTHROPIC_API_KEY se configura en los
# ajustes del Space, nunca en el código.
set -eu
DESTINO="${1:?Uso: space/publicar.sh usuario/nombre-del-space}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
git -C "$RAIZ" archive HEAD | tar -x -C "$TMP"
cp "$RAIZ/space/Dockerfile" "$TMP/Dockerfile"
cp "$RAIZ/space/README.md" "$TMP/README.md"
hf repo create "$DESTINO" --repo-type space --space-sdk docker --exist-ok
hf upload "$DESTINO" "$TMP" . --repo-type space --commit-message "Publicar demo"
echo "Publicado: https://huggingface.co/spaces/$DESTINO"
