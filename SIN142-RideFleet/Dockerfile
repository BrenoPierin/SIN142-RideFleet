FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 1 worker por container: a exposição Prometheus assume processo único.
# A escala/distribuição de carga é feita por múltiplos containers + LB,
# não por múltiplos workers no mesmo container.
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
