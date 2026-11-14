FROM python:3.11-slim
WORKDIR /code
COPY pyproject.toml ./
COPY app ./app
COPY web ./web
RUN pip install --no-cache-dir .
RUN useradd -u 10001 -m appuser && mkdir -p /code/artifacts && chown -R appuser:appuser /code
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
