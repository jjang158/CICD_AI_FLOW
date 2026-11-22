import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TodoItem } from './TodoItem';
import { TodoList } from './TodoList';

const todo = { id: 5, title: 'Deploy', completed: false, createdAt: '2025-01-01T00:00:00Z' };

describe('TodoItem', () => {
  it('renders the title and reflects completion state', () => {
    render(<TodoItem todo={{ ...todo, completed: true }} onToggle={vi.fn()} onDelete={vi.fn()} />);

    expect(screen.getByText('Deploy')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Toggle Deploy' })).toBeChecked();
    expect(screen.getByTestId('todo-5')).toHaveClass('completed');
  });

  it('calls onToggle and onDelete', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    const onDelete = vi.fn();
    render(<TodoItem todo={todo} onToggle={onToggle} onDelete={onDelete} />);

    await user.click(screen.getByRole('checkbox', { name: 'Toggle Deploy' }));
    await user.click(screen.getByRole('button', { name: 'Delete Deploy' }));

    expect(onToggle).toHaveBeenCalledWith(todo);
    expect(onDelete).toHaveBeenCalledWith(5);
  });
});

describe('TodoList', () => {
  it('shows an empty state', () => {
    render(<TodoList todos={[]} onToggle={vi.fn()} onDelete={vi.fn()} />);

    expect(screen.getByText(/No todos yet/)).toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  it('renders one item per todo', () => {
    render(
      <TodoList todos={[todo, { ...todo, id: 6, title: 'Smoke test' }]} onToggle={vi.fn()} onDelete={vi.fn()} />,
    );

    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('Smoke test')).toBeInTheDocument();
  });
});
