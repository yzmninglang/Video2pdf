FROM python:3.12-slim AS runtime

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV NO_PROXY=${NO_PROXY}
ENV no_proxy=${NO_PROXY}
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY engine.py job_manager.py web_app.py web_style.css /app/

RUN useradd -m -u 10001 appuser
USER appuser

EXPOSE 7860

ENTRYPOINT ["python", "web_app.py"]
CMD ["--server-name", "0.0.0.0", "--server-port", "7860", "--mapped-dir", "/data", "--state-dir", "/state"]
