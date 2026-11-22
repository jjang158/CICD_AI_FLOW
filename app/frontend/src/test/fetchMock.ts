import { vi, type Mock } from 'vitest';

/** Builds a minimal Response-like object; enough for the API client without relying on undici in jsdom. */
export function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/** A 204 No Content response. */
export function emptyResponse(status = 204): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.reject(new Error('no body')),
  } as unknown as Response;
}

/** Installs a vi.fn() as global fetch and returns it for per-call stubbing. */
export function installFetchMock(): Mock<typeof fetch> {
  const mock = vi.fn<typeof fetch>();
  vi.stubGlobal('fetch', mock);
  return mock;
}

/** Returns the parsed JSON body of the n-th fetch call. */
export function requestBody(mock: Mock<typeof fetch>, callIndex: number): unknown {
  const init = mock.mock.calls[callIndex]?.[1];
  return typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
}
