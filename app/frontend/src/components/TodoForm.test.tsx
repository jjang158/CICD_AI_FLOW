import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TodoForm } from './TodoForm';

describe('TodoForm', () => {
  it('submits the trimmed title and clears the input on success', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn().mockResolvedValue(true);
    render(<TodoForm onAdd={onAdd} />);

    const input = screen.getByLabelText('New todo title');
    await user.type(input, '  Ship it  ');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(onAdd).toHaveBeenCalledWith('Ship it');
    expect(input).toHaveValue('');
  });

  it('keeps the text when adding fails', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn().mockResolvedValue(false);
    render(<TodoForm onAdd={onAdd} />);

    const input = screen.getByLabelText('New todo title');
    await user.type(input, 'Retry me{Enter}');

    expect(onAdd).toHaveBeenCalledWith('Retry me');
    expect(input).toHaveValue('Retry me');
  });

  it('does not submit blank titles', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn().mockResolvedValue(true);
    render(<TodoForm onAdd={onAdd} />);

    const input = screen.getByLabelText('New todo title');
    await user.type(input, '   {Enter}');

    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled();
  });

  it('is disabled while the parent is loading', () => {
    render(<TodoForm onAdd={vi.fn()} disabled />);

    expect(screen.getByLabelText('New todo title')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled();
  });
});
