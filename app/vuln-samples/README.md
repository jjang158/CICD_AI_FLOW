# vuln-samples: intentionally vulnerable code for pipeline demos

Default state of the repository (`main`) is **clean**: nothing in this directory is compiled, analysed by SonarQube
(`sonar.exclusions` contains `app/vuln-samples/**`) or pulled in by the applications.
`apply.sh` copies the samples into the apps and pins vulnerable dependencies so that

- SonarQube reports new **Vulnerabilities** on new code -> Quality Gate **FAIL** (`new_violations > 0`)
- Trivy reports **HIGH/CRITICAL** CVEs from `gradle.lockfile` / `package-lock.json` -> Trivy gate **FAIL**

`revert.sh` undoes all of it without git. Both scripts need bash, JDK 21 (`./gradlew`) and Node 22 (`npm`),
exactly what the Jenkins `cicd-agent` has.

The demo branch `demo/vulnerable-code` in the repository is exactly this applied state (created from `main` by running
`apply.sh` and committing the result), so opening a PR or building that branch shows the failing gates without running
the script by hand.

```bash
app/vuln-samples/apply.sh            # apply   (--dry-run to preview, --skip-lock to skip lock regeneration)
app/vuln-samples/revert.sh           # revert
```

The applications still **build and pass all tests** with the samples applied (the samples are not wired into
any controller), so the failure comes only from the scanning / quality gate stages.

## What apply.sh changes

| Change | Target |
|---|---|
| copy `backend/*.kt` | `app/backend/src/main/kotlin/com/cicdaiflow/todo/vuln/` |
| copy `frontend/UnsafeHtml.tsx` | `app/frontend/src/vuln/UnsafeHtml.tsx` |
| insert `implementation("org.apache.logging.log4j:log4j-core:2.14.1")` between `// vuln-samples:begin` / `// vuln-samples:end` markers | `app/backend/build.gradle.kts` (`dependencies {` block) |
| `./gradlew dependencies --write-locks` | `app/backend/gradle.lockfile` |
| add `"lodash": "4.17.15"` to `dependencies` | `app/frontend/package.json` |
| `npm install --package-lock-only` | `app/frontend/package-lock.json` |

## SonarQube findings (measured)

Measured on SonarQube Community Build **26.11** with the default `Sonar way core` quality profiles and the default
`Sonar way` quality gate, new code period "previous version" (clean analysis first, then the applied state):

```
Quality Gate: ERROR
  ERROR new_violations                actual=11  > 0
  OK    new_duplicated_lines_density  actual=0.0 <= 3
issues: 11 (all type VULNERABILITY; severities BLOCKER 1, CRITICAL 7, MAJOR 3), new_security_rating = E
```

In this SonarQube version the former "Security Hotspot" rules (S2245, S4790, S1523, S6418, ...) are reported as
ordinary **Vulnerability** issues, so there is no separate hotspot review step: every finding counts as a new violation.

| Sample | Code | Rule | Measured |
|---|---|---|---|
| `backend/WeakCrypto.kt` | `Cipher.getInstance("AES/ECB/PKCS5Padding")` | kotlin:S5542 | detected (CRITICAL, 2 issues: mode + padding) |
| `backend/WeakCrypto.kt` | `Cipher.getInstance("DES")` | kotlin:S5547 | detected (CRITICAL) |
| `backend/WeakCrypto.kt` | `SSLContext.getInstance("TLSv1")` | kotlin:S4423 | detected (CRITICAL) |
| `backend/WeakCrypto.kt` | `KeyPairGenerator("RSA").initialize(1024)` | kotlin:S4426 | detected (CRITICAL) |
| `backend/WeakCrypto.kt` | `MessageDigest.getInstance("MD5")` | kotlin:S4790 | detected (CRITICAL) |
| `backend/InsecureRandomToken.kt` | `java.util.Random()`, `Math.random()` | kotlin:S2245 | detected (MAJOR, 2 issues) |
| `backend/LegacyUserRepository.kt` | `LEGACY_API_SECRET = "..."` | kotlin:S6418 | detected (BLOCKER) |
| `backend/LegacyUserRepository.kt` | `LEGACY_DB_PASSWORD = "..."` | kotlin:S2068 | rule active, **not raised** on a constant |
| `backend/LegacyUserRepository.kt` | SQL built with `+` / `"$name"` for `JdbcTemplate` / `Statement` | kotlin:S2077 | **rule does not exist** in this Kotlin analyzer (kept as a visual example only) |
| `frontend/UnsafeHtml.tsx` | `eval(expression)` | typescript:S1523 | detected (CRITICAL) |
| `frontend/UnsafeHtml.tsx` | `Math.random()` session token | typescript:S2245 | detected (MAJOR) |
| `frontend/UnsafeHtml.tsx` | `API_KEY = '...'` / `ADMIN_PASSWORD = '...'` | typescript:S6418 / S2068 | rules active, **not raised** (hex value below the randomness threshold; password constant ignored) |
| `frontend/UnsafeHtml.tsx` | `dangerouslySetInnerHTML={{ __html: markup }}` | (typescript:S6299 is Vue-only) | **no rule** for the React form in SonarJS; kept as a visual example only |

Net effect: 9 Kotlin + 2 TypeScript new Vulnerabilities -> Quality Gate FAIL. After `revert.sh` the next analysis has
no new-code issues and the gate is back to PASS (verified: clean -> vuln -> clean on one project key).

## Trivy findings (measured, `trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --ignore-unfixed`)

| Dependency | Lock file | CVEs |
|---|---|---|
| `org.apache.logging.log4j:log4j-core:2.14.1` | `app/backend/gradle.lockfile` | CVE-2021-44228 (CRITICAL, Log4Shell), CVE-2021-45046 (CRITICAL), CVE-2021-45105 (HIGH) |
| `lodash@4.17.15` | `app/frontend/package-lock.json` | CVE-2020-8203 (HIGH), CVE-2021-23337 (HIGH), CVE-2026-4800 (HIGH), NSWG-ECO-516 (HIGH) |

Total **7** HIGH/CRITICAL (Dockerfiles stay misconfiguration-free). `lodash` is only declared in `package.json`
(it is not imported), which is enough for Trivy because it scans the lock file. `log4j-core` is on the runtime
classpath but never initialised: Spring Boot keeps using Logback.

## Sample sources

- `backend/WeakCrypto.kt`: ECB/DES ciphers, MD5, TLSv1, RSA-1024
- `backend/LegacyUserRepository.kt`: SQL string concatenation with `JdbcTemplate` and `java.sql.Statement`, hard-coded DB password and API secret (not a Spring bean)
- `backend/InsecureRandomToken.kt`: `java.util.Random` / `Math.random()` tokens
- `frontend/UnsafeHtml.tsx`: `dangerouslySetInnerHTML` of user input, `eval`, `Math.random` token, hard-coded API key/password

None of the sample strings match Trivy's secret-detection rules (AWS/GitHub/Slack/private keys...), so the
clean checkout stays Trivy-clean even though these files sit in the repository.
