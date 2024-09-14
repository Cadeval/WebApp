FROM --platform=linux/amd64 python
WORKDIR /srv
COPY ./ /srv/cadevil/
WORKDIR /srv/cadevil
RUN pip install -r requirements.txt && python manage.py collectstatic
ENTRYPOINT python manage.py runserver 0.0.0.0:8000