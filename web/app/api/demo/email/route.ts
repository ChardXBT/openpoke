import { forwardToPython } from '@/lib/pyProxy';

export async function POST(request: Request) {
  return forwardToPython('/api/v1/demo/email', { method: 'POST', body: await request.text() });
}
