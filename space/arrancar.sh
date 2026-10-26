#!/bin/sh
# Arranca Ollama (embeddings) y la web. Claude si hay clave; si no, solo búsqueda.
set -e
ollama serve >/tmp/ollama.log 2>&1 &
for _ in $(seq 1 60); do
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break
  sleep 1
done
if [ -n "$ANTHROPIC_API_KEY" ]; then
  export VUT_PROVEEDOR=claude
else
  export VUT_PROVEEDOR=ninguno
fi
exec uvicorn vut_andalucia.api:app --host 0.0.0.0 --port 7860 --proxy-headers
