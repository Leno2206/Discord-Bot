# web-interface/Dockerfile

FROM python:3.12

# Install Docker CLI
RUN apt-get update && apt-get install -y docker.io

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 187
CMD ["python", "app.py"]
