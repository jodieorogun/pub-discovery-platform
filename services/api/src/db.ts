import 'dotenv/config';
import pg from 'pg';

const { Pool } = pg;

if (!process.env.DATABASE_URL) {
  throw new Error('DATABASE_URL is required');
}

const useSsl = process.env.DATABASE_SSL === 'true' || process.env.NODE_ENV === 'production';

export const db = new Pool({
  connectionString: process.env.DATABASE_URL,
  connectionTimeoutMillis: 3000,
  ssl: useSsl ? { rejectUnauthorized: false } : undefined,
});
