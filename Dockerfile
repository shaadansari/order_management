# WHY 3.12-slim (not 3.14): slim images are small and 3.12 is a stable, widely-available
# base. The app code is compatible across 3.11/3.12/3.13/3.14.
FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching (code changes don't bust the pip layer).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# In a container, run without --reload and bind to all interfaces.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
