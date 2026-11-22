package com.cicdaiflow.todo.service

import com.cicdaiflow.todo.domain.Todo
import com.cicdaiflow.todo.repository.TodoRepository
import org.slf4j.LoggerFactory
import org.springframework.boot.ApplicationArguments
import org.springframework.boot.ApplicationRunner
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component

/** Seeds a couple of todos on startup so the demo UI is not empty. Disable with app.sample-data.enabled=false. */
@Component
@ConditionalOnProperty(prefix = "app.sample-data", name = ["enabled"], havingValue = "true", matchIfMissing = true)
class SampleDataInitializer(private val repository: TodoRepository) : ApplicationRunner {

    private val log = LoggerFactory.getLogger(SampleDataInitializer::class.java)

    override fun run(args: ApplicationArguments) {
        if (repository.count() > 0) {
            log.info("Sample data skipped: todos already present")
            return
        }
        val seeded = repository.saveAll(
            listOf(
                Todo(title = "Run the CI/CD pipeline"),
                Todo(title = "Review SonarQube quality gate", completed = true),
            ),
        )
        log.info("Sample data loaded: {} todos", seeded.size)
    }
}
