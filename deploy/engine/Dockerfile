# The engine app: Temporal server + Temporal web UI + the forgeapi worker, for a two-app layout
# where the API is the other app. Same code and dependencies as the main image; only the command
# differs. Build from the repository root:  docker build -f deploy/engine/Dockerfile .
FROM ghcr.io/azskylab/forgeapi:latest
EXPOSE 8000 7233
CMD ["python", "-m", "app.engine"]
