# ---------- build stage ----------
FROM eclipse-temurin:21-jdk AS builder
WORKDIR /workspace

# Wrapper + build scripts first so the dependency download layer is cached
COPY gradlew settings.gradle.kts build.gradle.kts gradle.properties gradle.lockfile ./
COPY gradle ./gradle
RUN chmod +x gradlew && ./gradlew --no-daemon --version

COPY src ./src
# Tests run in the CI Test stage; here we only produce the boot jar.
RUN ./gradlew --no-daemon clean bootJar -x test \
    && java -Djarmode=tools -jar build/libs/app.jar extract --layers --destination build/extracted

# ---------- runtime stage ----------
FROM eclipse-temurin:21-jre AS runtime
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app

COPY --from=builder --chown=app:app /workspace/build/extracted/dependencies/ ./
COPY --from=builder --chown=app:app /workspace/build/extracted/spring-boot-loader/ ./
COPY --from=builder --chown=app:app /workspace/build/extracted/snapshot-dependencies/ ./
COPY --from=builder --chown=app:app /workspace/build/extracted/application/ ./

USER app
EXPOSE 8081
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75.0 -Djava.security.egd=file:/dev/./urandom"

# The JRE image carries no curl/wget; probe the actuator over bash's /dev/tcp instead.
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=5 \
    CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/8081 && printf "GET /actuator/health HTTP/1.0\r\nHost: localhost\r\n\r\n" >&3 && grep -q "\"status\":\"UP\"" <&3' || exit 1

ENTRYPOINT ["java", "-jar", "app.jar"]
