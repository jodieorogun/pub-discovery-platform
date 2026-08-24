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

Set `DATABASE_URL` in `services/api/.env`. In `apps/mobile/.env`, replace `192.168.1.100` with this computer's local network IP when using Expo Go on a phone. The phone and computer must be on the same Wi-Fi network. For an iOS simulator, use `http://127.0.0.1:3000` instead.

To find the computer's local IP on macOS, run `ipconfig getifaddr en0` (or `ipconfig getifaddr en1` if Wi-Fi is using that interface). After changing `.env`, restart Expo with its cache cleared:

```bash
cd apps/mobile
npm start -- --clear
```

Quick checks from the computer are `http://127.0.0.1:3000/health` for the API and `http://127.0.0.1:8000/health` for the recommendation engine.

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
