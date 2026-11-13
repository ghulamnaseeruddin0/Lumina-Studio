# Run Lumina Studio on any Docker host (a cloud VM, a home server, etc.)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-noto-core postgresql-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV LUMINA_DATA=/data
VOLUME /data
EXPOSE 8787
# Always set a secret token when exposing the server:  docker run -e LUMINA_TOKEN=long-random-text ...
CMD ["python", "run.py", "serve", "--host", "0.0.0.0", "--no-browser"]
