FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
ENV PYTHONUNBUFFERED=1 PORT=10000
EXPOSE 10000
CMD ["python", "cloud_server.py"]
