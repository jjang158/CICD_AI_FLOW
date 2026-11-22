package com.cicdaiflow.todo.service

import com.cicdaiflow.todo.domain.Todo
import com.cicdaiflow.todo.repository.TodoRepository
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.Test
import org.springframework.boot.DefaultApplicationArguments
import kotlin.test.assertEquals

class SampleDataInitializerTest {

    private val repository: TodoRepository = mockk()
    private val initializer = SampleDataInitializer(repository)
    private val args = DefaultApplicationArguments()

    @Test
    fun `seeds todos when the table is empty`() {
        val captured = slot<List<Todo>>()
        every { repository.count() } returns 0
        every { repository.saveAll(capture(captured)) } answers { captured.captured }

        initializer.run(args)

        assertEquals(2, captured.captured.size)
        verify(exactly = 1) { repository.saveAll(any<List<Todo>>()) }
    }

    @Test
    fun `does nothing when todos already exist`() {
        every { repository.count() } returns 3

        initializer.run(args)

        verify(exactly = 0) { repository.saveAll(any<List<Todo>>()) }
    }
}
