import fs from 'node:fs';
import path from 'node:path';

function readEnvFileToken(filePath: string): string {
  try {
    const text = fs.readFileSync(filePath, 'utf8');
    for (const line of text.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
      const index = trimmed.indexOf('=');
      const key = trimmed.slice(0, index).trim();
      if (key !== 'OPENPOKE_OWNER_TOKEN') continue;
      return trimmed.slice(index + 1).trim().replace(/^['"]|['"]$/g, '');
    }
  } catch {
    // The Python server creates the token on first startup; retry on the next request.
  }
  return '';
}

/** Server-side only: the owner token must never be rendered into a browser bundle. */
export function ownerToken(): string {
  const configured = (process.env.OPENPOKE_OWNER_TOKEN || '').trim();
  if (configured) return configured;
  const cwd = process.cwd();
  for (const candidate of [path.join(cwd, '.env'), path.join(cwd, '..', '.env')]) {
    const token = readEnvFileToken(candidate);
    if (token) return token;
  }
  return '';
}

export function pythonAuthHeaders(): Record<string, string> {
  const token = ownerToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
