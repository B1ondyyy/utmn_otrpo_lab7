import pika
import time
import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
from urllib.parse import urljoin, urlparse

# Загружаем переменные окружения
load_dotenv()

# Чтение параметров из .env
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
RABBITMQ_PORT = os.getenv('RABBITMQ_PORT', '5672')
RABBITMQ_USER = os.getenv('RABBITMQ_USER', 'guest')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD', 'guest')
QUEUE_NAME = os.getenv('QUEUE_NAME', 'links_queue')
TIMEOUT = int(os.getenv('TIMEOUT', 10))

# Настройка логирования: консоль и файл
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Создаём обработчики
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Формат логов
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Добавляем обработчики
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Загружаем обработанные ссылки из файла, чтобы избежать дублей
def load_processed_links():
    if os.path.exists('processed_links.txt'):
        with open('processed_links.txt', 'r') as f:
            return set(f.read().splitlines())
    return set()

# Сохраняем новые обработанные ссылки
def save_processed_links(processed_links):
    with open('processed_links.txt', 'a') as f:
        for link in processed_links:
            f.write(link + '\n')

# Извлекаем все внутренние ссылки из страницы
def extract_links(base_url):
    try:
        response = requests.get(base_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc

        links = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            # Преобразуем относительные ссылки в абсолютные
            full_url = urljoin(base_url, href)
            parsed_url = urlparse(full_url)
            if parsed_url.netloc == base_domain:
                links.add(full_url)
        return links
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching {base_url}: {e}")
        return set()

# Функция обработки сообщений
def callback(ch, method, properties, body):
    url = body.decode()
    logger.info(f"Processing URL: {url}")

    # Извлекаем заголовок страницы
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        title = soup.title.string if soup.title else 'No Title'
        logger.info(f"Page Title: {title}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching {url}: {e}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Загружаем обработанные ссылки
    processed_links = load_processed_links()

    # Извлекаем новые ссылки
    links = extract_links(url)
    logger.info(f"Found {len(links)} internal links on {url}.")
    new_links = links - processed_links  # Убираем уже обработанные ссылки
    logger.info(f"{len(new_links)} new links to send to the queue.")

    # Логирование найденных ссылок
    for link in new_links:
        logger.info(f"Found link: {link}")

    # Отправляем новые ссылки в очередь
    for link in new_links:
        logger.info(f"Sending new link: {link}")
        ch.basic_publish(
            exchange='',
            routing_key=QUEUE_NAME,
            body=link,
            properties=pika.BasicProperties(
                delivery_mode=2,  # Сообщения будут долговечными
            ))

    # Сохраняем обработанные ссылки
    save_processed_links(new_links)

    # Подтверждаем получение сообщения
    ch.basic_ack(delivery_tag=method.delivery_tag)

# Подключаемся к RabbitMQ
credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
connection = pika.BlockingConnection(pika.ConnectionParameters(
    host=RABBITMQ_HOST, port=RABBITMQ_PORT, credentials=credentials))
channel = connection.channel()

# Объявляем очередь
channel.queue_declare(queue=QUEUE_NAME, durable=True)

# Настройка таймаута
timeout = TIMEOUT
start_time = time.time()

# Настройка потребителя
channel.basic_qos(prefetch_count=1)
channel.basic_consume(queue=QUEUE_NAME, on_message_callback=callback)

logger.info("Waiting for messages...")

# Запуск потребителя в отдельном потоке
try:
    channel.start_consuming()
except KeyboardInterrupt:
    logger.info("Consumer interrupted by user.")
finally:
    connection.close()