import type { Todo } from '../types';

interface TodoItemProps {
  todo: Todo;
  onToggle: (todo: Todo) => void;
  onDelete: (id: number) => void;
}

export function TodoItem({ todo, onToggle, onDelete }: TodoItemProps) {
  return (
    <li className={todo.completed ? 'todo-item completed' : 'todo-item'} data-testid={`todo-${todo.id}`}>
      <label className="todo-label">
        <input
          type="checkbox"
          checked={todo.completed}
          onChange={() => onToggle(todo)}
          aria-label={`Toggle ${todo.title}`}
        />
        <span className="todo-title">{todo.title}</span>
      </label>
      <button type="button" className="delete" onClick={() => onDelete(todo.id)} aria-label={`Delete ${todo.title}`}>
        Delete
      </button>
    </li>
  );
}
