package com.cicdaiflow.todo.repository

import com.cicdaiflow.todo.domain.Todo
import org.springframework.data.jpa.repository.JpaRepository

interface TodoRepository : JpaRepository<Todo, Long> {
    fun findAllByOrderByIdAsc(): List<Todo>
}
