import { beforeEach, describe, expect, it } from 'vitest';
import type { Mock } from 'vitest';
import { ApiError, createTodo, deleteTodo, errorMessage, listTodos, updateTodo } from './todos';
import { emptyResponse, installFetchMock, jsonResponse, requestBody } from '../test/fetchMock';

const sample = { id: 1, title: 'Write tests', completed: false, createdAt: '2025-01-01T00:00:00Z' };

describe('todo API client', () => {
  let fetchMock: Mock<typeof fetch>;

  beforeEach(() => {
    fetchMock = installFetchMock();
  });

  it('lists todos', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([sample]));

    const todos = await listTodos();

    expect(todos).toEqual([sample]);
    expect(fetchMock).toHaveBeenCalledWith('/api/todos');
  });

  it('creates a todo with a JSON body', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(sample, 201));

    const created = await createTodo({ title: 'Write tests', completed: false });

    expect(created).toEqual(sample);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/todos');
    expect(init?.method).toBe('POST');
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(requestBody(fetchMock, 0)).toEqual({ title: 'Write tests', completed: false });
  });

  it('updates a todo via PUT', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...sample, completed: true }));

    const updated = await updateTodo(1, { title: 'Write tests', completed: true });

    expect(updated.completed).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/todos/1');
    expect(init?.method).toBe('PUT');
  });

  it('deletes a todo and resolves on 204', async () => {
    fetchMock.mockResolvedValueOnce(emptyResponse());

    await expect(deleteTodo(1)).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith('/api/todos/1', { method: 'DELETE' });
  });

  it('throws an ApiError carrying the backend message', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 404, message: 'Todo 9 not found' }, 404));

    const error = await listTodos().catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).message).toBe('Todo 9 not found');
  });

  it('falls back to a generic message when the error body is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(emptyResponse(502));

    await expect(deleteTodo(3)).rejects.toThrow('Request failed with status 502');
  });

  it('falls back to a generic message when the error body has no message', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: 'Bad Request' }, 400));

    await expect(createTodo({ title: '', completed: false })).rejects.toThrow('Request failed with status 400');
  });

  it('formats unknown errors', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom');
    expect(errorMessage('not an error')).toBe('Unexpected error');
  });
});
