import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import type { Mock } from 'vitest';
import App from './App';
import { emptyResponse, installFetchMock, jsonResponse, requestBody } from './test/fetchMock';

const todos = [
  { id: 1, title: 'Run pipeline', completed: false, createdAt: '2025-01-01T00:00:00Z' },
  { id: 2, title: 'Review quality gate', completed: true, createdAt: '2025-01-01T00:00:00Z' },
];

describe('App', () => {
  let fetchMock: Mock<typeof fetch>;

  beforeEach(() => {
    fetchMock = installFetchMock();
  });

  it('renders todos loaded from the API', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(todos));
    render(<App />);

    expect(screen.getByText('Loading todos…')).toBeInTheDocument();
    expect(await screen.findByText('Run pipeline')).toBeInTheDocument();
    expect(screen.getByText('Review quality gate')).toBeInTheDocument();
    expect(screen.getByText('1 of 2 remaining')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/todos');
  });

  it('adds a todo through the form', async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ id: 3, title: 'Write docs', completed: false, createdAt: 'x' }, 201));
    render(<App />);
    await screen.findByText(/No todos yet/);

    await user.type(screen.getByLabelText('New todo title'), 'Write docs');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByText('Write docs')).toBeInTheDocument();
    expect(requestBody(fetchMock, 1)).toEqual({ title: 'Write docs', completed: false });
    expect(screen.getByText('1 of 1 remaining')).toBeInTheDocument();
  });

  it('toggles and deletes todos', async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(todos))
      .mockResolvedValueOnce(jsonResponse({ ...todos[0], completed: true }))
      .mockResolvedValueOnce(emptyResponse());
    render(<App />);
    await screen.findByText('Run pipeline');

    await user.click(screen.getByRole('checkbox', { name: 'Toggle Run pipeline' }));
    await waitFor(() => expect(screen.getByText('0 of 2 remaining')).toBeInTheDocument());
    expect(requestBody(fetchMock, 1)).toEqual({ title: 'Run pipeline', completed: true });

    await user.click(screen.getByRole('button', { name: 'Delete Review quality gate' }));
    await waitFor(() => expect(screen.queryByText('Review quality gate')).not.toBeInTheDocument());
    expect(fetchMock).toHaveBeenLastCalledWith('/api/todos/2', { method: 'DELETE' });
    expect(screen.getByText('0 of 1 remaining')).toBeInTheDocument();
  });

  it('shows an error when the initial load fails', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ message: 'Backend unavailable' }, 503));
    render(<App />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Backend unavailable');
    expect(screen.queryByText('Loading todos…')).not.toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  it('shows an error when adding fails and keeps the typed text', async () => {
    const user = userEvent.setup();
    fetchMock
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ message: 'Validation failed' }, 400));
    render(<App />);
    await screen.findByText(/No todos yet/);

    await user.type(screen.getByLabelText('New todo title'), 'Bad todo{Enter}');

    expect(await screen.findByRole('alert')).toHaveTextContent('Validation failed');
    expect(screen.getByLabelText('New todo title')).toHaveValue('Bad todo');
  });

  it('shows an error when a network failure occurs on delete', async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValueOnce(jsonResponse(todos)).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    render(<App />);
    await screen.findByText('Run pipeline');

    await user.click(screen.getByRole('button', { name: 'Delete Run pipeline' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to fetch');
    expect(screen.getByText('Run pipeline')).toBeInTheDocument();
  });
});
