FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2, lxml, pypdf
RUN apt-get update && apt-get install -y \
    gcc libpq-dev libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data

EXPOSE 8000

CMD ["python", "main.py"]
