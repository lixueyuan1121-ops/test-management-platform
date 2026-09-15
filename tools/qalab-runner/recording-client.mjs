import { randomUUID } from 'node:crypto';
// A process owns its recording lease; polling and every upload must use the same identity.
export function createRecordingClient(api, runner) {
  const consumerId = randomUUID();
  const query = `runner=${encodeURIComponent(runner)}`;
  return {
    consumerId,
    fetchRecords: () => api('GET', `/api/record/pending?${query}&consumer_id=${encodeURIComponent(consumerId)}`),
    reportRecordEvents: (id, body) => api('POST', `/api/record/${id}/events?${query}`, { ...body, consumer_id: consumerId }),
  };
}
