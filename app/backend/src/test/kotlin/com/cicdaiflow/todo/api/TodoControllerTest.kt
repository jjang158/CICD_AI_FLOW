package com.cicdaiflow.todo.api

import com.cicdaiflow.todo.dto.TodoRequest
import com.cicdaiflow.todo.dto.TodoResponse
import com.cicdaiflow.todo.exception.TodoNotFoundException
import com.cicdaiflow.todo.service.TodoService
import io.mockk.clearMocks
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest
import org.springframework.context.annotation.Bean
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.delete
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.put
import java.time.Instant

@WebMvcTest(controllers = [TodoController::class, HealthController::class])
class TodoControllerTest {

    @TestConfiguration
    class MockConfig {
        @Bean
        fun todoService(): TodoService = mockk()
    }

    @Autowired
    private lateinit var mockMvc: MockMvc

    @Autowired
    private lateinit var service: TodoService

    private val sample = TodoResponse(1L, "Write code", false, Instant.parse("2025-01-01T00:00:00Z"))

    @BeforeEach
    fun resetMocks() {
        clearMocks(service)
    }

    @Test
    fun `GET list returns todos`() {
        every { service.findAll() } returns listOf(sample)

        mockMvc.get("/api/todos").andExpect {
            status { isOk() }
            content { contentTypeCompatibleWith(MediaType.APPLICATION_JSON) }
            jsonPath("$.length()") { value(1) }
            jsonPath("$[0].id") { value(1) }
            jsonPath("$[0].title") { value("Write code") }
            jsonPath("$[0].completed") { value(false) }
        }
    }

    @Test
    fun `GET by id returns a todo`() {
        every { service.findById(1L) } returns sample

        mockMvc.get("/api/todos/1").andExpect {
            status { isOk() }
            jsonPath("$.title") { value("Write code") }
            jsonPath("$.createdAt") { value("2025-01-01T00:00:00Z") }
        }
    }

    @Test
    fun `GET by id returns 404 when missing`() {
        every { service.findById(42L) } throws TodoNotFoundException(42L)

        mockMvc.get("/api/todos/42").andExpect {
            status { isNotFound() }
            jsonPath("$.status") { value(404) }
            jsonPath("$.message") { value("Todo 42 not found") }
        }
    }

    @Test
    fun `GET by non-numeric id returns 400`() {
        mockMvc.get("/api/todos/abc").andExpect {
            status { isBadRequest() }
            jsonPath("$.message") { value("Invalid value for parameter 'id'") }
        }
    }

    @Test
    fun `GET unknown path returns 404 error body`() {
        mockMvc.get("/api/nothing-here").andExpect {
            status { isNotFound() }
            jsonPath("$.status") { value(404) }
        }
    }

    @Test
    fun `POST creates a todo`() {
        every { service.create(TodoRequest("Ship it", false)) } returns sample.copy(id = 2L, title = "Ship it")

        mockMvc.post("/api/todos") {
            contentType = MediaType.APPLICATION_JSON
            content = """{"title":"Ship it"}"""
        }.andExpect {
            status { isCreated() }
            jsonPath("$.id") { value(2) }
            jsonPath("$.title") { value("Ship it") }
        }
    }

    @Test
    fun `POST with blank title returns 400 with details`() {
        mockMvc.post("/api/todos") {
            contentType = MediaType.APPLICATION_JSON
            content = """{"title":"   "}"""
        }.andExpect {
            status { isBadRequest() }
            jsonPath("$.message") { value("Validation failed") }
            jsonPath("$.details[0]") { value("title: title must not be blank") }
        }
        verify(exactly = 0) { service.create(any()) }
    }

    @Test
    fun `POST with too long title returns 400`() {
        val longTitle = "x".repeat(201)
        mockMvc.post("/api/todos") {
            contentType = MediaType.APPLICATION_JSON
            content = """{"title":"$longTitle"}"""
        }.andExpect {
            status { isBadRequest() }
            jsonPath("$.details[0]") { value("title: title must be at most 200 characters") }
        }
    }

    @Test
    fun `POST with malformed JSON returns 400`() {
        mockMvc.post("/api/todos") {
            contentType = MediaType.APPLICATION_JSON
            content = """{"title": """
        }.andExpect {
            status { isBadRequest() }
            jsonPath("$.message") { value("Malformed request body") }
        }
    }

    @Test
    fun `PUT updates a todo`() {
        every { service.update(1L, TodoRequest("Write code", true)) } returns sample.copy(completed = true)

        mockMvc.put("/api/todos/1") {
            contentType = MediaType.APPLICATION_JSON
            content = """{"title":"Write code","completed":true}"""
        }.andExpect {
            status { isOk() }
            jsonPath("$.completed") { value(true) }
        }
    }

    @Test
    fun `DELETE removes a todo`() {
        every { service.delete(1L) } returns Unit

        mockMvc.delete("/api/todos/1").andExpect {
            status { isNoContent() }
        }
        verify(exactly = 1) { service.delete(1L) }
    }

    @Test
    fun `unexpected exception returns 500 error body`() {
        every { service.findAll() } throws IllegalStateException("boom")

        mockMvc.get("/api/todos").andExpect {
            status { isInternalServerError() }
            jsonPath("$.message") { value("Unexpected error") }
        }
    }

    @Test
    fun `GET api health returns UP`() {
        mockMvc.get("/api/health").andExpect {
            status { isOk() }
            jsonPath("$.status") { value("UP") }
        }
    }
}
