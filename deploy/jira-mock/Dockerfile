FROM python:3.12-slim
WORKDIR /mock
COPY server.py .
USER nobody
EXPOSE 8080
CMD ["python", "server.py"]
