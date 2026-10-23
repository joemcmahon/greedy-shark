# Use an official Python image as the base
FROM python:3.10

RUN apt-get update && apt-get install -y ffmpeg

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container. .env is deliberately not
# copied: docker-compose supplies it at runtime via env_file, keeping
# secrets out of the image.
COPY *.py .

# Specify the command to run when the container starts
CMD ["python", "monitor_stream.py"]
