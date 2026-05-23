export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

async function parseResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  if (contentType.includes('application/json')) return response.json();
  return response.text();
}

export async function getJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  return parseResponse(response);
}

export async function postJson(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseResponse(response);
}

export function fileUrl(runName, relativePath) {
  return `${API_BASE}/files/${encodeURIComponent(runName)}/${relativePath}`;
}
