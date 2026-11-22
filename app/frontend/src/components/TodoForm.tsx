import { useState, type FormEvent } from 'react';

interface TodoFormProps {
  /** Returns true when the todo was created, so the input can be cleared. */
  onAdd: (title: string) => Promise<boolean>;
  disabled?: boolean;
}

export function TodoForm({ onAdd, disabled = false }: TodoFormProps) {
  const [title, setTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) {
      return;
    }
    setSubmitting(true);
    try {
      const created = await onAdd(trimmed);
      if (created) {
        setTitle('');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const busy = disabled || submitting;

  return (
    <form className="todo-form" onSubmit={handleSubmit}>
      <input
        type="text"
        aria-label="New todo title"
        placeholder="What needs to be done?"
        maxLength={200}
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        disabled={busy}
      />
      <button type="submit" disabled={busy || title.trim() === ''}>
        Add
      </button>
    </form>
  );
}
