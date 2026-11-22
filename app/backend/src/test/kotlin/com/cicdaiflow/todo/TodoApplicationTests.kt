package com.cicdaiflow.todo

import com.cicdaiflow.todo.repository.TodoRepository
import com.cicdaiflow.todo.service.SampleDataInitializer
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import kotlin.test.assertEquals
import kotlin.test.assertNotNull

/** Boots the application with the default profile, so the sample data runner is active. */
@SpringBootTest
class TodoApplicationTests {

    @Autowired
    private lateinit var repository: TodoRepository

    @Autowired
    private lateinit var initializer: SampleDataInitializer

    @Test
    fun `context loads and sample data is seeded`() {
        assertNotNull(initializer)
        assertEquals(2, repository.count())
    }
}
