FROM python:3.11-slim

# OCR (проверка картинок) использует OpenCV — ему нужны libGL и glib, в slim-образе их нет
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# модели OCR распаковываются при первом импорте — делаем это на сборке,
# чтобы первая карточка в проде не ждала
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"

COPY main.py image_filter.py ./
CMD ["python", "-u", "main.py"]
