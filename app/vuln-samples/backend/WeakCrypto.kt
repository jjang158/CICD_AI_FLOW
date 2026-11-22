package com.cicdaiflow.todo.vuln

import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.spec.SecretKeySpec
import javax.net.ssl.SSLContext

/**
 * INTENTIONALLY VULNERABLE demo code (cicd-ai-flow vuln-samples). Never ship this.
 *
 * SonarQube findings (measured on SonarQube 26.11, all reported as Vulnerability issues):
 *  - S5542  Encryption algorithms should be used with secure mode and padding scheme (AES/ECB)
 *  - S5547  Cipher algorithms should be robust (DES)                                          
 *  - S4423  Weak SSL/TLS protocols should not be used (TLSv1)                                
 *  - S4426  Cryptographic keys should be robust (RSA 1024)                                    
 *  - S4790  Using weak hashing algorithms is security-sensitive (MD5)
 */
object WeakCrypto {

    private val staticKey = SecretKeySpec("0123456789abcdef".toByteArray(Charsets.UTF_8), "AES")

    fun encryptWithEcb(plain: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/ECB/PKCS5Padding") // S5542: ECB mode
        cipher.init(Cipher.ENCRYPT_MODE, staticKey)
        return cipher.doFinal(plain)
    }

    fun legacyDesCipher(): Cipher = Cipher.getInstance("DES") // S5547: DES is not robust

    fun md5Hex(input: String): String =
        MessageDigest.getInstance("MD5") // S4790: weak hash
            .digest(input.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

    fun legacyTlsContext(): SSLContext = SSLContext.getInstance("TLSv1") // S4423: weak TLS protocol

    fun weakRsaKeyPair(): KeyPair {
        val generator = KeyPairGenerator.getInstance("RSA")
        generator.initialize(1024) // S4426: key too short
        return generator.generateKeyPair()
    }
}
