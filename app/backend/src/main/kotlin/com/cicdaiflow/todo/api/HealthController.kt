package com.cicdaiflow.todo.api

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** Lightweight application-level health endpoint (the actuator endpoint lives at /actuator/health). */
@RestController
@RequestMapping("/api/health")
class HealthController {

    @GetMapping
    fun health(): Map<String, String> = mapOf("status" to "UP", "service" to "cicd-ai-flow-backend")
}
