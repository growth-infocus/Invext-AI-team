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
    git \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project (shared/ + services/)
COPY . .

# Create sandbox, reports, and workspace mount point
RUN mkdir -p /app/sandbox /app/reports /workspace

# Create non-root user; give ownership of app + workspace mount point
RUN useradd -m -u 1000 agentuser \
    && chown -R agentuser:agentuser /app /workspace

# Configure git identity for commits made by agents
RUN git config --system user.name "Invext AI Agent" \
    && git config --system user.email "agents@invext.ai" \
    && git config --system safe.directory '*'

USER agentuser

# PYTHONPATH so `from shared.core import ...` resolves
ENV PYTHONPATH=/app

# Default port (overridden per service in docker-compose)
EXPOSE 8000

# Default CMD — overridden per-service
CMD ["uvicorn", "services.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
