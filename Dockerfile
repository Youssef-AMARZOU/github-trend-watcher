FROM python:3.12-slim

WORKDIR /app
COPY trend_collector.py .

VOLUME ["/app/data"]
ENTRYPOINT ["python", "trend_collector.py"]
CMD ["all"]
