FROM python:3.12-slim

# Install dependencies needed by the Audiveris .deb package
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        libfreetype6 \
        libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# Download and install Audiveris .deb (includes bundled JRE)
ARG AUDIVERIS_VERSION=5.10.1
ARG AUDIVERIS_DEB=Audiveris-${AUDIVERIS_VERSION}-ubuntu22.04-x86_64.deb
RUN curl -L -o /tmp/audiveris.deb \
    "https://github.com/Audiveris/audiveris/releases/download/${AUDIVERIS_VERSION}/${AUDIVERIS_DEB}" && \
    dpkg -i /tmp/audiveris.deb || apt-get install -f -y && \
    rm /tmp/audiveris.deb

# Set Audiveris on PATH (adjust if the .deb installs elsewhere)
ENV PATH="/opt/audiveris/bin:${PATH}"

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app.py auth.py db.py ./
COPY static/ static/
COPY templates/ templates/

# Create runtime directories
RUN mkdir -p uploads outputs data

ENV MELODRA_DB_PATH=/app/data/melodra.db
ENV FLASK_DEBUG=0

EXPOSE 5000

CMD ["python", "app.py"]
