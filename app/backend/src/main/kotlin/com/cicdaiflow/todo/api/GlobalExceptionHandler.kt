package com.cicdaiflow.todo.api

import com.cicdaiflow.todo.dto.ApiError
import com.cicdaiflow.todo.exception.TodoNotFoundException
import org.slf4j.LoggerFactory
import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.http.converter.HttpMessageNotReadableException
import org.springframework.web.bind.MethodArgumentNotValidException
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException
import org.springframework.web.servlet.resource.NoResourceFoundException

@RestControllerAdvice
class GlobalExceptionHandler {

    private val log = LoggerFactory.getLogger(GlobalExceptionHandler::class.java)

    @ExceptionHandler(TodoNotFoundException::class)
    fun handleNotFound(ex: TodoNotFoundException): ResponseEntity<ApiError> =
        error(HttpStatus.NOT_FOUND, ex.message ?: "Resource not found")

    @ExceptionHandler(NoResourceFoundException::class)
    fun handleNoResource(ex: NoResourceFoundException): ResponseEntity<ApiError> =
        error(HttpStatus.NOT_FOUND, "No resource at ${ex.resourcePath}")

    @ExceptionHandler(MethodArgumentNotValidException::class)
    fun handleValidation(ex: MethodArgumentNotValidException): ResponseEntity<ApiError> {
        val details = ex.bindingResult.fieldErrors.map { "${it.field}: ${it.defaultMessage}" }
        return error(HttpStatus.BAD_REQUEST, "Validation failed", details)
    }

    @ExceptionHandler(HttpMessageNotReadableException::class)
    fun handleUnreadable(ex: HttpMessageNotReadableException): ResponseEntity<ApiError> {
        log.debug("Malformed request body", ex)
        return error(HttpStatus.BAD_REQUEST, "Malformed request body")
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException::class)
    fun handleTypeMismatch(ex: MethodArgumentTypeMismatchException): ResponseEntity<ApiError> =
        error(HttpStatus.BAD_REQUEST, "Invalid value for parameter '${ex.name}'")

    @ExceptionHandler(Exception::class)
    fun handleUnexpected(ex: Exception): ResponseEntity<ApiError> {
        log.error("Unhandled exception", ex)
        return error(HttpStatus.INTERNAL_SERVER_ERROR, "Unexpected error")
    }

    private fun error(status: HttpStatus, message: String, details: List<String> = emptyList()): ResponseEntity<ApiError> =
        ResponseEntity.status(status).body(ApiError(status.value(), status.reasonPhrase, message, details))
}
