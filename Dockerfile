FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
COPY public ./public
COPY scripts ./scripts
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
COPY --from=frontend /app/dist/client ./dist/client
ENV PYTHONUNBUFFERED=1 RAMOS_HOST=0.0.0.0 RAMOS_DATA_DIR=/data PORT=8765
EXPOSE 8765
CMD ["python","backend/app.py"]
