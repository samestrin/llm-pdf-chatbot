# The fake model of the compose smoke; see server.py. Standard library only.
FROM python:3.12-slim
WORKDIR /app
COPY server.py .
EXPOSE 8080
CMD ["python", "-u", "server.py"]
