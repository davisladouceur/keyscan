FROM python:3.11-slim

# Install system dependencies required by OpenCV
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create directory for uploaded images
RUN mkdir -p /app/uploads /app/static

EXPOSE 8000

# Shell form (not exec form) so Railway's $PORT environment variable is expanded
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
