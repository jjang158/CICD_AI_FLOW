package com.cicdaiflow.todo.domain

import jakarta.persistence.Column
import jakarta.persistence.Entity
import jakarta.persistence.GeneratedValue
import jakarta.persistence.GenerationType
import jakarta.persistence.Id
import jakarta.persistence.Table
import java.time.Instant

@Entity
@Table(name = "todos")
class Todo(
    @Column(nullable = false, length = MAX_TITLE_LENGTH)
    var title: String,

    @Column(nullable = false)
    var completed: Boolean = false,

    @Column(nullable = false, updatable = false)
    val createdAt: Instant = Instant.now(),

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    val id: Long? = null,
) {
    companion object {
        const val MAX_TITLE_LENGTH = 200
    }
}
