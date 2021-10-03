FROM python:3.8.5

RUN mkdir var/www
RUN mkdir var/www/html

COPY httpd.conf /etc
COPY http-test-suite/ /var/www/html

WORKDIR /app

COPY server.py /app

EXPOSE 80

CMD ["python3", "server.py"]
