
# Base image with a recent Python 3 runtime
FROM python:3.11-slim
 
WORKDIR /app
 
# System deps required to build numpy/statsmodels/pmdarima wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*
 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
COPY App.py .
 
CMD ["python", "App.py"]