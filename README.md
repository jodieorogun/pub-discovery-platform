# Pub Discovery Platform

One workspace for the London pub discovery app, its API, and the recommendation engine.

## Layout

- `apps/web` — primary browser client
- `apps/mobile` — paused Expo / React Native client for a later mobile phase
- `services/api` — Fastify API and PostgreSQL integration
- `services/recommendation-engine` — FastAPI recommendation service

The web app talks only to the Fastify API. Fastify proxies `/recommendations` to the Python service, so the client needs one backend URL.

The recommendation engine's full 408-venue RAG dataset lives in the local ignored `services/recommendation-engine/data/local` directory. It is intentionally not committed to GitHub; copy or restore that directory when setting up another machine.

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

Run the browser product and its services:

```bash
npm run dev
```

The API runs on port 3000, the recommendation engine on port 8000, and the web app runs at `http://127.0.0.1:5173`. To run the paused Expo client separately, use `npm run dev:mobile`.
