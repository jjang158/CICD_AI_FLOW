package com.cicdaiflow.todo.service

import com.cicdaiflow.todo.domain.Todo
import com.cicdaiflow.todo.dto.TodoRequest
import com.cicdaiflow.todo.dto.TodoResponse
import com.cicdaiflow.todo.exception.TodoNotFoundException
import com.cicdaiflow.todo.repository.TodoRepository
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

@Service
@Transactional
class TodoService(private val repository: TodoRepository) {

    @Transactional(readOnly = true)
    fun findAll(): List<TodoResponse> = repository.findAllByOrderByIdAsc().map(TodoResponse::from)

    @Transactional(readOnly = true)
    fun findById(id: Long): TodoResponse = TodoResponse.from(getOrThrow(id))

    fun create(request: TodoRequest): TodoResponse {
        val todo = Todo(title = request.title.trim(), completed = request.completed)
        return TodoResponse.from(repository.save(todo))
    }

    fun update(id: Long, request: TodoRequest): TodoResponse {
        val todo = getOrThrow(id)
        todo.title = request.title.trim()
        todo.completed = request.completed
        return TodoResponse.from(repository.save(todo))
    }

    fun delete(id: Long) {
        repository.delete(getOrThrow(id))
    }

    private fun getOrThrow(id: Long): Todo = repository.findById(id).orElseThrow { TodoNotFoundException(id) }
}
