package com.cicdaiflow.todo.service

import com.cicdaiflow.todo.domain.Todo
import com.cicdaiflow.todo.dto.TodoRequest
import com.cicdaiflow.todo.exception.TodoNotFoundException
import com.cicdaiflow.todo.repository.TodoRepository
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.util.Optional
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class TodoServiceTest {

    private val repository: TodoRepository = mockk()
    private val service = TodoService(repository)

    private fun persisted(id: Long, title: String, completed: Boolean = false) =
        Todo(title = title, completed = completed, id = id)

    @Test
    fun `findAll maps entities to responses in id order`() {
        every { repository.findAllByOrderByIdAsc() } returns listOf(persisted(1, "a"), persisted(2, "b", true))

        val result = service.findAll()

        assertEquals(listOf(1L, 2L), result.map { it.id })
        assertEquals(listOf("a", "b"), result.map { it.title })
        assertTrue(result[1].completed)
    }

    @Test
    fun `findById returns the todo when present`() {
        every { repository.findById(7L) } returns Optional.of(persisted(7, "seven"))

        val result = service.findById(7L)

        assertEquals(7L, result.id)
        assertEquals("seven", result.title)
    }

    @Test
    fun `findById throws when missing`() {
        every { repository.findById(99L) } returns Optional.empty()

        val ex = assertThrows<TodoNotFoundException> { service.findById(99L) }

        assertEquals("Todo 99 not found", ex.message)
    }

    @Test
    fun `create trims the title and persists`() {
        val saved = slot<Todo>()
        every { repository.save(capture(saved)) } answers { persisted(10, saved.captured.title, saved.captured.completed) }

        val result = service.create(TodoRequest(title = "  write tests  ", completed = false))

        assertEquals("write tests", saved.captured.title)
        assertFalse(saved.captured.completed)
        assertEquals(10L, result.id)
        assertEquals("write tests", result.title)
    }

    @Test
    fun `update changes title and completed flag`() {
        val existing = persisted(3, "old")
        every { repository.findById(3L) } returns Optional.of(existing)
        every { repository.save(existing) } returns existing

        val result = service.update(3L, TodoRequest(title = " new ", completed = true))

        assertEquals("new", result.title)
        assertTrue(result.completed)
        assertEquals(3L, result.id)
        verify(exactly = 1) { repository.save(existing) }
    }

    @Test
    fun `update throws when missing`() {
        every { repository.findById(4L) } returns Optional.empty()

        assertThrows<TodoNotFoundException> { service.update(4L, TodoRequest(title = "x")) }

        verify(exactly = 0) { repository.save(any()) }
    }

    @Test
    fun `delete removes an existing todo`() {
        val existing = persisted(5, "bye")
        every { repository.findById(5L) } returns Optional.of(existing)
        every { repository.delete(existing) } returns Unit

        service.delete(5L)

        verify(exactly = 1) { repository.delete(existing) }
    }

    @Test
    fun `delete throws when missing`() {
        every { repository.findById(6L) } returns Optional.empty()

        assertThrows<TodoNotFoundException> { service.delete(6L) }

        verify(exactly = 0) { repository.delete(any()) }
    }
}
