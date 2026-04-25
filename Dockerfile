FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Assemble the chores package from flat repo files
RUN mkdir -p chores && \
    mv router.py chores/router.py && \
    mv models.py chores/models.py && \
    mv state.py chores/state.py && \
    mv todoist.py chores/todoist.py && \
    touch chores/__init__.py

EXPOSE 8099

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8099"]
