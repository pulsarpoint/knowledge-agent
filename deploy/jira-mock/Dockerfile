FROM python:3.12-slim
WORKDIR /mock
COPY server.py .
# A new named volume mounted at /data takes this owner, so the mock can save its state there.
RUN mkdir /data && chown nobody /data
USER nobody
EXPOSE 8080
CMD ["python", "server.py"]
