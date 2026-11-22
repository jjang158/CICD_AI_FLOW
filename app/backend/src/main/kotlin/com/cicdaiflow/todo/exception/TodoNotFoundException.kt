package com.cicdaiflow.todo.exception

class TodoNotFoundException(id: Long) : RuntimeException("Todo $id not found")
