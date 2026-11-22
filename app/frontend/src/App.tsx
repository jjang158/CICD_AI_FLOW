import { useEffect, useState } from 'react';
import { createTodo, deleteTodo, errorMessage, listTodos, updateTodo } from './api/todos';
import { TodoForm } from './components/TodoForm';
import { TodoList } from './components/TodoList';
import type { Todo } from './types';

type LoadStatus = 'loading' | 'ready' | 'error';

export default function App() {
  const [todos, setTodos] = useState<Todo[]>([]);
  const [status, setStatus] = useState<LoadStatus>('loading');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listTodos()
      .then((data) => {
        if (!cancelled) {
          setTodos(data);
          setStatus('ready');
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(errorMessage(cause));
          setStatus('error');
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleAdd = async (title: string): Promise<boolean> => {
    setError(null);
    try {
      const created = await createTodo({ title, completed: false });
      setTodos((previous) => [...previous, created]);
      return true;
    } catch (cause) {
      setError(errorMessage(cause));
      return false;
    }
  };

  const handleToggle = async (todo: Todo) => {
    setError(null);
    try {
      const updated = await updateTodo(todo.id, { title: todo.title, completed: !todo.completed });
      setTodos((previous) => previous.map((item) => (item.id === updated.id ? updated : item)));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  };

  const handleDelete = async (id: number) => {
    setError(null);
    try {
      await deleteTodo(id);
      setTodos((previous) => previous.filter((item) => item.id !== id));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  };

  const remaining = todos.filter((todo) => !todo.completed).length;

  return (
    <main className="app">
      <header>
        <h1>cicd-ai-flow Todos</h1>
        <p className="muted">React + Vite frontend talking to the Kotlin + Spring Boot backend</p>
      </header>

      <TodoForm onAdd={handleAdd} disabled={status === 'loading'} />

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {status === 'loading' && <p className="muted">Loading todos…</p>}
      {status === 'ready' && <TodoList todos={todos} onToggle={handleToggle} onDelete={handleDelete} />}

      {status === 'ready' && (
        <footer className="muted">
          {remaining} of {todos.length} remaining
        </footer>
      )}

      cicd 테스트용 v1
    </main>
  );
}
