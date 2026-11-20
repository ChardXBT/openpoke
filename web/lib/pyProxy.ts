// Server-side proxy to the Python API (the browser never talks to it directly).
const serverBase = (process.env.PY_SERVER_URL || 'http://localhost:8001').replace(/\/$/, '');

export async function forwardToPython(path: string, init: RequestInit = {}): Promise<Response> {
  try {
    const res = await fetch(`${serverBase}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}) },
      cache: 'no-store',
    });
    const bodyText = await res.text();
    return new Response(bodyText || '{}', {
      status: res.status,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  } catch (error: any) {
    return new Response(JSON.stringify({ error: error?.message || 'Failed to reach Python server' }), {
      status: 502,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  }
}
