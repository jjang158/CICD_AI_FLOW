# backend (Kotlin + Spring Boot)

Small Todo REST API used as the test application for the cicd-ai-flow pipeline.

- Kotlin 2.4 / Spring Boot 4.2 (Spring Framework 7, Jackson 3) / Gradle 8.14 (wrapper included) / JDK 21
- H2 in-memory database, port **8081**
- Dependency locking is enabled: `gradle.lockfile` pins the full graph (scanned by Trivy)

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/todos` | list todos |
| POST | `/api/todos` | create (`{"title": "...", "completed": false}`) → 201 |
| GET | `/api/todos/{id}` | get one (404 if missing) |
| PUT | `/api/todos/{id}` | full update |
| DELETE | `/api/todos/{id}` | delete → 204 |
| GET | `/api/health` | simple app health |
| GET | `/actuator/health` | Spring Boot actuator health |

## Build & test

```bash
cd app/backend
chmod +x gradlew            # the exec bit may be lost on Windows checkouts
./gradlew clean build jacocoTestReport
```

Outputs:

- JUnit XML: `build/test-results/test/*.xml`
- JaCoCo XML (SonarQube): `build/reports/jacoco/test/jacocoTestReport.xml`
- JaCoCo HTML: `build/reports/jacoco/test/html/index.html`
- Boot jar: `build/libs/app.jar`

`build` also runs `jacocoTestCoverageVerification` (minimum 80% line coverage).

After changing dependencies in `build.gradle.kts`, refresh the lock file:

```bash
./gradlew dependencies --write-locks
```

## Run locally

```bash
./gradlew bootRun
curl http://localhost:8081/api/todos
```

Set `app.sample-data.enabled=false` to start with an empty table (the `test` profile does this).

## Docker

```bash
docker build -t cicd-ai-flow/backend:latest .
docker run --rm -p 8081:8081 cicd-ai-flow/backend:latest
```

Or use the compose file one level up: `docker compose -f app/docker-compose.app.yml up -d --build`.
