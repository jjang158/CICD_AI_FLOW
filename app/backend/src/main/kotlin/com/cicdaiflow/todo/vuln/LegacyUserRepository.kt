package com.cicdaiflow.todo.vuln

import org.springframework.jdbc.core.JdbcTemplate
import java.sql.Connection
import java.sql.DriverManager

/**
 * INTENTIONALLY VULNERABLE demo code (cicd-ai-flow vuln-samples). Never ship this.
 *
 * SonarQube findings (measured on SonarQube 26.11, all reported as Vulnerability issues):
 *  - S2077  SQL built by concatenation: rule no longer exists in the Kotlin analyzer (visual example only)
 *  - S2068  Hard-coded password constant: rule active but not raised in 26.11
 *  - S6418  Hard-coded secret (LEGACY_API_SECRET): detected, BLOCKER
 *
 * This class is NOT a Spring bean: it is never instantiated by the running application.
 */
class LegacyUserRepository(private val jdbcTemplate: JdbcTemplate) {

    fun findByName(name: String): List<Map<String, Any?>> =
        jdbcTemplate.queryForList("SELECT * FROM users WHERE name = '" + name + "'") // S2077

    fun findByEmail(email: String): List<Map<String, Any?>> =
        jdbcTemplate.queryForList("SELECT * FROM users WHERE email = '$email'") // S2077

    fun deleteByName(name: String): Int =
        jdbcTemplate.update("DELETE FROM users WHERE name = '$name'") // S2077

    fun countByStatus(connection: Connection, status: String): Int {
        connection.createStatement().use { statement ->
            statement.executeQuery("SELECT COUNT(*) FROM users WHERE status = '$status'").use { rs -> // S2077
                return if (rs.next()) rs.getInt(1) else 0
            }
        }
    }

    fun openLegacyConnection(): Connection =
        DriverManager.getConnection(LEGACY_JDBC_URL, LEGACY_DB_USER, LEGACY_DB_PASSWORD)

    companion object {
        const val LEGACY_JDBC_URL = "jdbc:h2:mem:legacy"
        const val LEGACY_DB_USER = "admin"
        const val LEGACY_DB_PASSWORD = "P@ssw0rd-legacy-2019" // S2068: hard-coded password
        const val LEGACY_API_SECRET = "a3f9c2e1d4b5f6a7c8d9e0f1a2b3c4d5e6f7a8b9" // S6418: hard-coded secret
    }
}
