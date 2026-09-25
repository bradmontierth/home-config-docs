FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fast-flights==3.1.0" typing_extensions paho-mqtt==2.1.0 \
    fastapi==0.115.* "uvicorn[standard]==0.30.*" pyyaml tzdata
COPY app/ /app/app/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "app.main"]
