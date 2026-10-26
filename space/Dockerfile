# Demo pública de vut-andalucia en Hugging Face Spaces (SDK docker, puerto 7860).
# Ollama solo sirve los embeddings (bge-m3, CPU); las respuestas las redacta Claude si el Space
# tiene el secreto ANTHROPIC_API_KEY. Sin él, la demo funciona en modo «solo búsqueda».
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates zstd \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://ollama.com/install.sh | sh

ENV OLLAMA_MODELS=/opt/ollama/models \
    OLLAMA_HOST=127.0.0.1:11434 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
RUN mkdir -p $OLLAMA_MODELS && (ollama serve & sleep 6 && ollama pull bge-m3) \
    && chmod -R a+rX /opt/ollama

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir uv==0.12.5 \
    && uv sync --frozen --no-dev --extra claude \
    && chmod +x /app/space/arrancar.sh

# Demo pública: límites de uso, sin /docs, cabeceras de seguridad, IP real tras el proxy de HF.
ENV VUT_PUBLICO=1 \
    VUT_PROXY=1 \
    VUT_PREGUNTAS_POR_HORA=8 \
    VUT_BUSQUEDAS_POR_HORA=60 \
    VUT_PREGUNTAS_POR_DIA=300 \
    VUT_REINTENTAR=0

RUN useradd -m -u 1000 vut && chown -R vut /app
USER vut
EXPOSE 7860
CMD ["/app/space/arrancar.sh"]
