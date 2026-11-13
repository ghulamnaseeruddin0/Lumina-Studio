# Build the browser client so the Python server can safely serve Vite's output.
FROM node:22-alpine AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ARG VITE_API_URL
ENV VITE_SUPABASE_URL=$VITE_SUPABASE_URL \
    VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY \
    VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-noto-core postgresql-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY run.py .
COPY lumina/ ./lumina/
COPY scripts/ ./scripts/
COPY supabase/ ./supabase/
COPY assets/ ./assets/
COPY --from=frontend /web/dist/ /app/web/
ENV LUMINA_DATA=/data
EXPOSE 8787
# Always set a secret token when exposing the server:  docker run -e LUMINA_TOKEN=long-random-text ...
CMD ["python", "run.py", "serve", "--host", "0.0.0.0", "--no-browser"]
