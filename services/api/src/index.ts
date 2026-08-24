import cors from '@fastify/cors';
import Fastify from 'fastify';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { db } from './db.js';
import { refreshPubOpeningHours } from './websiteHours.js';
import { refreshPubAddress } from './address.js';

const app = Fastify({ logger: true });
await app.register(cors, { origin: true });

const recommendationApiUrl = (process.env.RECOMMENDATION_API_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');

type LocalVenue = {
  venueId: string;
  name: string;
  latitude: number;
  longitude: number;
  address?: string | null;
  website?: string | null;
  phone?: string | null;
  openingHours?: string | null;
  tags?: string[];
  area?: string | null;
};

async function localVenueFallback(limit: number) {
  const filePath = path.resolve(process.cwd(), '../recommendation-engine/data/local/westminster_camden_venues_enriched.json');
  try {
    const venues = JSON.parse(await readFile(filePath, 'utf8')) as LocalVenue[];
    return venues.slice(0, limit).map((venue) => ({
      id: venue.venueId,
      name: venue.name,
      latitude: venue.latitude,
      longitude: venue.longitude,
      address: venue.address ?? venue.area ?? null,
      website: venue.website ?? null,
      phone: venue.phone ?? null,
      tags: { area: venue.area ?? '', features: (venue.tags ?? []).join(' ') },
      openingHours: venue.openingHours ?? null,
      hoursSource: venue.openingHours ? 'osm' : 'none',
      hoursLastChecked: null,
      hoursConfidence: null,
      addressSource: 'openStreetMap',
      addressLastChecked: null,
    }));
  } catch {
    return [];
  }
}

app.get('/health', async () => {
  await db.query('SELECT 1');
  return { ok: true };
});

app.post<{ Body: { query: string; limit?: number; offset?: number } }>('/recommendations', async (request, reply) => {
  try {
    const response = await fetch(`${recommendationApiUrl}/recommendations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.body),
    });

    const payload = await response.json();
    return reply.code(response.status).send(payload);
  } catch {
    return reply.code(503).send({ error: 'Recommendation service unavailable' });
  }
});

app.post<{ Body: Record<string, unknown> }>('/feedback', async (request, reply) => {
  try {
    const response = await fetch(`${recommendationApiUrl}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.body),
    });
    const payload = await response.json();
    return reply.code(response.status).send(payload);
  } catch {
    return reply.code(503).send({ error: 'Recommendation service unavailable' });
  }
});

app.get<{ Querystring: { limit?: string } }>('/pubs', async (request) => {
  const requestedLimit = Number(request.query.limit ?? 500);
  const limit = Number.isFinite(requestedLimit)
    ? Math.min(Math.max(Math.floor(requestedLimit), 1), 5000)
    : 500;

  try {
    const result = await db.query(
      `SELECT id, name, latitude, longitude, address, website, phone, tags,
              opening_hours AS "openingHours", hours_source AS "hoursSource",
              hours_last_checked AS "hoursLastChecked", hours_confidence AS "hoursConfidence",
              address_source AS "addressSource", address_last_checked AS "addressLastChecked"
       FROM pubs
       ORDER BY name
       LIMIT $1`,
      [limit],
    );
    return { pubs: result.rows, source: 'postgres' };
  } catch {
    return { pubs: await localVenueFallback(limit), source: 'local-rag-fallback' };
  }
});

app.get<{ Params: { id: string } }>('/pubs/:id', async (request, reply) => {
  const result = await db.query(
    `SELECT id, name, latitude, longitude, address, website, phone, tags,
            opening_hours AS "openingHours", hours_source AS "hoursSource",
            hours_last_checked AS "hoursLastChecked", hours_confidence AS "hoursConfidence",
            address_source AS "addressSource", address_last_checked AS "addressLastChecked"
     FROM pubs
     WHERE id = $1`,
    [request.params.id],
  );

  if (!result.rows[0]) return reply.code(404).send({ error: 'Pub not found' });
  return result.rows[0];
});

app.post<{ Params: { id: string } }>('/pubs/:id/refresh-hours', async (request, reply) => {
  try {
    return await refreshPubOpeningHours(request.params.id);
  } catch (error) {
    return reply.code(404).send({ error: error instanceof Error ? error.message : 'Unable to refresh pub hours' });
  }
});

app.post<{ Params: { id: string } }>('/pubs/:id/refresh-address', async (request, reply) => {
  try {
    return await refreshPubAddress(request.params.id);
  } catch (error) {
    return reply.code(404).send({ error: error instanceof Error ? error.message : 'Unable to refresh pub address' });
  }
});

const port = Number(process.env.PORT ?? 3000);
await app.listen({ port, host: '0.0.0.0' });

const shutdown = async () => {
  await app.close();
  await db.end();
  process.exit(0);
};

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
