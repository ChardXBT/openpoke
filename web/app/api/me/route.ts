import { forwardToPython } from '@/lib/pyProxy';

export async function GET() {
  return forwardToPython('/api/v1/me');
}

// DELETE /api/me?subject=dentist forgets one subject, history included.
export async function DELETE(request: Request) {
  const subject = new URL(request.url).searchParams.get('subject') || '';
  if (!subject.trim()) {
    return new Response(JSON.stringify({ error: 'subject is required' }), { status: 400 });
  }
  return forwardToPython(`/api/v1/me/${encodeURIComponent(subject)}`, { method: 'DELETE' });
}
