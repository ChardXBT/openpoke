import { forwardToPython } from '@/lib/pyProxy';

export async function GET() {
  return forwardToPython('/api/v1/demo/mailbox');
}
