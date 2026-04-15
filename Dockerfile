# ============================================================
# Shared base image for all AI Agent microservices
# Each service overrides CMD in docker-compose.yml
# ============================================================

FROM python:3.11-slim

# System packages needed for psycopg2, lxml, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project (shared/ + services/)
COPY . .

# Create sandbox and reports directories
RUN mkdir -p /app/sandbox /app/reports

# Create non-root user for security
RUN useradd -m -u 1000 agentuser && chown -R agentuser:agentuser /app
USER agentuser

# PYTHONPATH so `from shared.core import ...` resolves
ENV PYTHONPATH=/app

# Default port (overridden per service in docker-compose)
EXPOSE 8000

# Default CMD — overridden per-service
CMD ["uvicorn", "services.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
