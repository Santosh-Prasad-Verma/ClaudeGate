FROM python:3.11-slim

WORKDIR /app

# Upgrade OS packages to apply security patches and install curl for healthcheck
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Upgrade pip, setuptools, and wheel to address package manager vulnerabilities
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as non-root user for container security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8082

HEALTHCHECK --interval=10s --timeout=3s --retries=3 CMD curl -f http://localhost:8082/health || exit 1

CMD ["python", "start_proxy.py"]

