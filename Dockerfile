FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
COPY public ./public
COPY scripts ./scripts
RUN npm run build

FROM dart:3.14.0-sdk AS login_transport
WORKDIR /transport
COPY backend/dart_transport/pubspec.yaml backend/dart_transport/pubspec.lock ./
RUN dart pub get --enforce-lockfile
COPY backend/dart_transport/bin ./bin
COPY backend/dart_transport/lib ./lib
RUN dart compile exe bin/bridge.dart -o /ramos-h2

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
COPY --from=frontend /app/dist/client ./dist/client
COPY --from=login_transport /ramos-h2 /app/bin/ramos-h2
ENV PYTHONUNBUFFERED=1 RAMOS_HOST=0.0.0.0 RAMOS_DATA_DIR=/data PORT=8765
ENV RAMOS_DART_TRANSPORT=/app/bin/ramos-h2
EXPOSE 8765
CMD ["python","backend/app.py"]
