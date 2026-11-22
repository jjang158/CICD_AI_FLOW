package com.cicdaiflow.todo.vuln

import java.util.Random

/**
 * INTENTIONALLY VULNERABLE demo code (cicd-ai-flow vuln-samples). Never ship this.
 *
 * SonarQube findings (measured on SonarQube 26.11, all reported as Vulnerability issues):
 *  - S2245  Using pseudorandom number generators (PRNGs) is security-sensitive
 *           (java.util.Random and Math.random() used to build security tokens)
 */
object InsecureRandomToken {

    private const val ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

    private val random = Random() // S2245: not cryptographically secure

    fun sessionToken(length: Int = 32): String =
        buildString {
            repeat(length) { append(ALPHABET[random.nextInt(ALPHABET.length)]) }
        }

    fun passwordResetCode(): Int = (Math.random() * 1_000_000).toInt() // S2245

    fun apiKey(): String = java.lang.Long.toHexString(random.nextLong())
}
