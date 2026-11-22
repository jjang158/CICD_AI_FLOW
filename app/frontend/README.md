# frontend (React + TypeScript + Vite)

Todo UI for the cicd-ai-flow demo. Talks to the backend via `/api/todos`.

- React 18, TypeScript 5.9, Vite 8, Vitest 5 (+ jsdom, Testing Library), ESLint 10 (flat config)
- Node 22 LTS required (`npm ci` uses the committed `package-lock.json`)

## Commands

```bash
cd app/frontend
npm ci
npm run lint                       # eslint .
npm run test -- --run --coverage   # vitest: coverage/lcov.info + coverage/junit.xml
npm run build                      # tsc -b && vite build -> dist/
npm run dev                        # dev server on :3000, /api proxied to http://localhost:8081
```

Outputs:

- `coverage/lcov.info` (SonarQube; paths are relative to the repository root)
- `coverage/junit.xml` (Jenkins JUnit)
- `dist/` (static bundle)

## Docker

Multi-stage image: `node:22-alpine` build -> `nginxinc/nginx-unprivileged:1.30.6-alpine` serving `dist/`
as non-root (uid 101) on container port **8080**.
`nginx.conf` proxies `/api/` (and `/actuator/health`) to `http://backend:8081` and falls back to `index.html` for SPA routes.

```bash
docker build -t cicd-ai-flow/frontend:latest .
docker run --rm -p 3000:8080 cicd-ai-flow/frontend:latest   # backend must be reachable as "backend:8081"
```

Run together with the backend: `docker compose -f app/docker-compose.app.yml up -d --build` (host port 3000 -> container 8080).
