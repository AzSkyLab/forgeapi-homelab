# Temporal dev server, for environments that cannot pull from Docker Hub at run time: build this
# into your own registry. In-memory history; use a real Temporal service for anything durable.
FROM temporalio/temporal:1.8.3
EXPOSE 7233 8233
ENTRYPOINT ["temporal", "server", "start-dev", "--ip", "0.0.0.0", "--ui-ip", "0.0.0.0", "--ui-port", "8233", "--ui-disable-news-fetch"]
