package com.cicdaiflow.todo.dto

import com.cicdaiflow.todo.domain.Todo
import jakarta.validation.constraints.NotBlank
import jakarta.validation.constraints.Size
import java.time.Instant

/** Request body for creating or fully updating a todo. */
data class TodoRequest(
    @field:NotBlank(message = "title must not be blank")
    @field:Size(max = Todo.MAX_TITLE_LENGTH, message = "title must be at most ${Todo.MAX_TITLE_LENGTH} characters")
    val title: String = "",
    val completed: Boolean = false,
)

/** Response representation of a todo. */
data class TodoResponse(
    val id: Long,
    val title: String,
    val completed: Boolean,
    val createdAt: Instant,
) {
    companion object {
        fun from(todo: Todo): TodoResponse =
            TodoResponse(
                id = checkNotNull(todo.id) { "persisted todo must have an id" },
                title = todo.title,
                completed = todo.completed,
                createdAt = todo.createdAt,
            )
    }
}

/** Uniform error body returned by the global exception handler. */
data class ApiError(
    val status: Int,
    val error: String,
    val message: String,
    val details: List<String> = emptyList(),
    val timestamp: Instant = Instant.now(),
)
