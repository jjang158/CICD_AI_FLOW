/** Todo as returned by the backend API. */
export interface Todo {
  id: number;
  title: string;
  completed: boolean;
  createdAt: string;
}

/** Payload accepted by POST /api/todos and PUT /api/todos/{id}. */
export interface TodoInput {
  title: string;
  completed: boolean;
}
