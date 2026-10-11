// Static sample content used to preview the playlist-notes layout. Not wired to the API.

export interface DemoVideoNotes {
  position: number
  title: string
  duration: string
  markdown: string
}

export interface DemoPlaylistNotes {
  playlistTitle: string
  channel: string
  videoCount: number
  totalDuration: string
  masterGuide: string
  videos: DemoVideoNotes[]
}

export const demoPlaylistNotes: DemoPlaylistNotes = {
  playlistTitle: 'Docker for Beginners',
  channel: 'Sample Channel',
  videoCount: 3,
  totalDuration: '1h 12m',
  masterGuide: `# Docker for Beginners — Study Guide

## Overview
This playlist takes you from "what is a container?" to running a multi-service app with Docker Compose. Each video builds on the previous one, so watch them in order.

## Learning path
1. **Containers vs. virtual machines** — why containers are lightweight and where they fit.
2. **Images and Dockerfiles** — building a reproducible image for your own app.
3. **Docker Compose** — running an app, database and cache together.

## Key concepts
| Concept | One-line definition | Covered in |
|---|---|---|
| Image | Read-only template a container is started from | Video 1, 2 |
| Container | A running instance of an image | Video 1 |
| Dockerfile | Build recipe that produces an image | Video 2 |
| Layer cache | Reuse of unchanged build steps | Video 2 |
| Volume | Persistent storage that outlives a container | Video 3 |
| Compose service | One container definition in a multi-container app | Video 3 |

## Glossary
- **Registry** — a store for images (Docker Hub is the default).
- **Tag** — a label for an image version, e.g. \`node:20-alpine\`.
- **Bind mount** — maps a host folder into a container.

## Common pitfalls
- Putting \`COPY . .\` before dependency install busts the layer cache on every code change.
- Running as root inside the container.
- Forgetting a volume for database data, then losing it on \`docker compose down -v\`.

## Review checklist
- [ ] Explain image vs. container in your own words.
- [ ] Write a Dockerfile for a small app and build it.
- [ ] Run the app with Compose, with a database volume.
`,
  videos: [
    {
      position: 1,
      title: 'Containers vs. Virtual Machines',
      duration: '18 min',
      markdown: `## Summary
Containers share the host kernel and package only the app and its dependencies, so they start in seconds and use far less memory than VMs.

## Key points
- A **VM** virtualizes hardware and runs a full guest OS; a **container** virtualizes the OS.
- An **image** is the template; a **container** is a running instance of it.
- Isolation comes from Linux namespaces and cgroups.

## Commands shown
\`\`\`bash
docker pull nginx:alpine
docker run -d -p 8080:80 nginx:alpine
docker ps
docker stop <container_id>
\`\`\`

## Takeaways
- Use containers for consistent environments; use VMs when you need a different kernel.
- Containers are ephemeral by default — don't store state inside them.

## Quick check
1. What is the difference between an image and a container?
2. Why do containers start faster than VMs?
`,
    },
    {
      position: 2,
      title: 'Writing Your First Dockerfile',
      duration: '29 min',
      markdown: `## Summary
Walks through containerizing a small Node.js app, then optimizes the Dockerfile for build speed and image size.

## Key points
- Each instruction creates a **layer**; unchanged layers are cached.
- Copy dependency manifests first, install, then copy source.
- Prefer slim or alpine base images and a \`.dockerignore\`.

## Example
\`\`\`dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
CMD ["node", "server.js"]
\`\`\`

## Takeaways
- Ordering instructions from least to most frequently changed maximizes cache hits.
- Multi-stage builds keep build tools out of the final image.

## Quick check
1. Why copy \`package.json\` before the rest of the source?
2. What does \`.dockerignore\` do?
`,
    },
    {
      position: 3,
      title: 'Docker Compose for Multi-Container Apps',
      duration: '25 min',
      markdown: `## Summary
Uses Compose to run a web app with Postgres and Redis, covering networking, environment variables and volumes.

## Key points
- Services in one Compose file share a network and reach each other **by service name**.
- Use a **named volume** for database data.
- Keep secrets in an \`.env\` file, not in the Compose file.

## Example
\`\`\`yaml
services:
  web:
    build: .
    ports: ["3000:3000"]
    depends_on: [db]
  db:
    image: postgres:16
    volumes: [pgdata:/var/lib/postgresql/data]
volumes:
  pgdata:
\`\`\`

## Takeaways
- \`docker compose up -d\` starts everything; \`down\` stops it (add \`-v\` only if you want to delete data).
- \`depends_on\` controls start order, not readiness.

## Quick check
1. How does the web service reach the database?
2. What happens to data when you run \`docker compose down -v\`?
`,
    },
  ],
}
