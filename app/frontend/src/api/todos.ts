import type { Todo, TodoInput } from '../types';

export const TODOS_ENDPOINT = '/api/todos';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** Error raised for non-2xx responses; carries the HTTP status and the backend message when available. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let message = `Request failed with status ${response.status}`;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && 'message' in body && typeof body.message === 'string') {
      message = body.message;
    }
  } catch {
    // body was not JSON; keep the generic message
  }
  return new ApiError(response.status, message);
}

async function ensureOk(response: Response): Promise<Response> {
  if (!response.ok) {
    throw await toApiError(response);
  }
  return response;
}

export async function listTodos(): Promise<Todo[]> {
  const response = await ensureOk(await fetch(TODOS_ENDPOINT));
  return (await response.json()) as Todo[];
}

export async function createTodo(input: TodoInput): Promise<Todo> {
  const response = await ensureOk(
    await fetch(TODOS_ENDPOINT, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(input) }),
  );
  return (await response.json()) as Todo;
}

export async function updateTodo(id: number, input: TodoInput): Promise<Todo> {
  const response = await ensureOk(
    await fetch(`${TODOS_ENDPOINT}/${id}`, { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify(input) }),
  );
  return (await response.json()) as Todo;
}

export async function deleteTodo(id: number): Promise<void> {
  await ensureOk(await fetch(`${TODOS_ENDPOINT}/${id}`, { method: 'DELETE' }));
}

/** Turns any thrown value into a user-facing message. */
export function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return 'Unexpected error';
}
