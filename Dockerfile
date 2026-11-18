FROM python:3.13-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GRADEBOOK_DATA=/data
RUN mkdir /data && chown 10001:10001 /data
COPY server.py campus.py /app/
COPY web /app/web
USER 10001:10001
EXPOSE 8080
CMD ["python", "/app/server.py"]
