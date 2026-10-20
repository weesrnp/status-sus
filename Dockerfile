# Dockerfile para Status SUS
FROM python:3.11-alpine

LABEL maintainer="Status SUS Contributors"
LABEL description="Painel de Observabilidade e Disponibilidade de Barramentos de Saúde Pública (DATASUS / MS / Gov.br)"

WORKDIR /app

# Copia arquivos do projeto
COPY server.py config.json ./
COPY static/ ./static/

# Configurações de ambiente
ENV PYTHONUNBUFFERED=1 \
    STATUS_SUS_PORT=8765

EXPOSE 8765

# Executa o servidor nativo com usuário não-root para máxima segurança
RUN adduser -D -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["python", "server.py"]
