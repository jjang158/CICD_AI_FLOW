package com.cicdaiflow.todo.api

import org.junit.jupiter.api.MethodOrderer
import org.junit.jupiter.api.Order
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestMethodOrder
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc
import org.springframework.http.MediaType
import org.springframework.test.context.ActiveProfiles
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.delete
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.put
import tools.jackson.databind.ObjectMapper
import tools.jackson.module.kotlin.readValue
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** Full-stack test through the HTTP layer against the in-memory H2 database. */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@TestMethodOrder(MethodOrderer.OrderAnnotation::class)
class TodoApiIntegrationTest {

    @Autowired
    private lateinit var mockMvc: MockMvc

    @Autowired
    private lateinit var objectMapper: ObjectMapper

    @Test
    @Order(1)
    fun `actuator health is UP`() {
        mockMvc.get("/actuator/health").andExpect {
            status { isOk() }
            jsonPath("$.status") { value("UP") }
        }
    }

    @Test
    @Order(2)
    fun `full CRUD round trip`() {
        // starts empty (sample data disabled by the test profile)
        mockMvc.get("/api/todos").andExpect {
            status { isOk() }
            jsonPath("$.length()") { value(0) }
        }

        val created = mockMvc.post("/api/todos") {
            contentType = MediaType.APPLICATION_JSON
            content = """{"title":"  Integration test  "}"""
        }.andExpect {
            status { isCreated() }
            jsonPath("$.title") { value("Integration test") }
            jsonPath("$.completed") { value(false) }
        }.andReturn().response.contentAsString

        val id = objectMapper.readValue<Map<String, Any>>(created)["id"].toString().toLong()
        assertTrue(id > 0)

        mockMvc.get("/api/todos/$id").andExpect {
            status { isOk() }
            jsonPath("$.id") { value(id) }
        }

        mockMvc.put("/api/todos/$id") {
            contentType = MediaType.APPLICATION_JSON
            content = """{"title":"Integration test","completed":true}"""
        }.andExpect {
            status { isOk() }
            jsonPath("$.completed") { value(true) }
        }

        val listBody = mockMvc.get("/api/todos").andReturn().response.contentAsString
        val list = objectMapper.readValue<List<Map<String, Any>>>(listBody)
        assertEquals(1, list.size)
        assertEquals(true, list[0]["completed"])

        mockMvc.delete("/api/todos/$id").andExpect { status { isNoContent() } }

        mockMvc.get("/api/todos/$id").andExpect {
            status { isNotFound() }
            jsonPath("$.message") { value("Todo $id not found") }
        }
        mockMvc.delete("/api/todos/$id").andExpect { status { isNotFound() } }
    }
}
