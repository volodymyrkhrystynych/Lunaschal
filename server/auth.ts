import { Context, MiddlewareHandler } from 'hono';
import { getCookie, setCookie, deleteCookie } from 'hono/cookie';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { db, schema } from './db/index.js';
import { eq } from 'drizzle-orm';

const JWT_SECRET = process.env.JWT_SECRET || 'lunaschal-dev-secret-change-in-production';
const COOKIE_NAME = 'lunaschal_token';
const TOKEN_EXPIRY = '7d';

export interface AuthUser {
  authenticated: true;
}

export async function getSettings() {
  const [settings] = await db.select().from(schema.settings).limit(1);
  return settings;
}

export async function isSetupComplete(): Promise<boolean> {
  const settings = await getSettings();
  return !!settings?.passwordHash;
}

export async function setupPassword(password: string): Promise<void> {
  const hash = await bcrypt.hash(password, 12);
  const now = new Date();

  const existing = await getSettings();
  if (existing) {
    await db
      .update(schema.settings)
      .set({ passwordHash: hash, updatedAt: now })
      .where(eq(schema.settings.id, 1));
  } else {
    await db.insert(schema.settings).values({
      id: 1,
      passwordHash: hash,
      createdAt: now,
      updatedAt: now,
    });
  }
}

export async function verifyPassword(password: string): Promise<boolean> {
  const settings = await getSettings();
  if (!settings?.passwordHash) return false;
  return bcrypt.compare(password, settings.passwordHash);
}

export function generateToken(): string {
  return jwt.sign({ authenticated: true }, JWT_SECRET, { expiresIn: TOKEN_EXPIRY });
}

export function verifyToken(token: string): AuthUser | null {
  try {
    const decoded = jwt.verify(token, JWT_SECRET) as AuthUser;
    return decoded;
  } catch {
    return null;
  }
}

export function setAuthCookie(c: Context, token: string) {
  setCookie(c, COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'Lax',
    maxAge: 60 * 60 * 24 * 7, // 7 days
    path: '/',
  });
}

export function clearAuthCookie(c: Context) {
  deleteCookie(c, COOKIE_NAME, { path: '/' });
}

export function getAuthFromContext(c: Context): AuthUser | null {
  const token = getCookie(c, COOKIE_NAME);
  if (!token) return null;
  return verifyToken(token);
}

// Check if request is from localhost (skip auth for local development)
function isLocalhost(c: Context): boolean {
  const host = c.req.header('host') || '';
  return host.startsWith('localhost') || host.startsWith('127.0.0.1');
}

// Middleware that requires authentication
export const requireAuth: MiddlewareHandler = async (c, next) => {
  // Skip auth for localhost in development
  if (process.env.NODE_ENV !== 'production' && isLocalhost(c)) {
    return next();
  }

  const auth = getAuthFromContext(c);
  if (!auth) {
    return c.json({ error: 'Unauthorized' }, 401);
  }

  return next();
};

// Middleware that checks if setup is needed
export const checkSetup: MiddlewareHandler = async (c, next) => {
  const setupComplete = await isSetupComplete();
  c.set('setupComplete', setupComplete);
  return next();
};
