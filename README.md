# Pub Discovery Platform

One workspace for the London pub discovery app, its API, and the recommendation engine.

## Layout

- `apps/mobile` — Expo / React Native client
- `services/api` — Fastify API and PostgreSQL integration
- `services/recommendation-engine` — FastAPI recommendation service

The mobile app talks only to the Fastify API. Fastify proxies `/recommendations` to the Python service, so the mobile app needs one backend URL.

## Setup

Requirements: Node.js 20+, npm, Python 3.12+, `uv`, and PostgreSQL.

```bash
cp services/api/.env.example services/api/.env
cp apps/mobile/.env.example apps/mobile/.env
```

Set `DATABASE_URL` in `services/api/.env`. In `apps/mobile/.env`, replace the example IP with this computer's local network IP when using Expo Go on a phone.

Install dependencies:

```bash
npm run install:all
cd services/api && npm run db:migrate && npm run db:import
```

Run everything:

```bash
npm run dev
```

The API runs on port 3000, the recommendation engine on port 8000, and Expo starts its normal development server.
